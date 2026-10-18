# backend/aipds/proto/history.py — history of prototype build conversations.
#
# The past conversation to show when the build panel reopens. The source of truth is the build
# agent's SDK transcript (proto/session_store.py mirrors it to S3), and this module turns it into
# the same HistoryItem as Discovery — the conversion itself reuses session_history's
# transform_cli_transcript as is. Both screens draw with the same ChatTimeline, so the
# representation must not diverge.
#
# It differs from Discovery in three ways, and each is a reason this module exists:
#
#   1. The conversation spans **multiple SDK sessions**. Improving a completed build switches
#      the session to a new id (the handoff branch of proto/session._resolve_session_id) — to carry
#      only a summary instead of the long build context. session.json remembers only the current id,
#      so we read every session under the transcript prefix and join them **in order of the first
#      line's timestamp**. Why the order is not recorded separately: the CLI already writes that value
#      on every line, and a separate order table would be a second copy that can drift from the transcript.
#
#   2. A user line in the transcript **may not be something the user said.** The session's first
#      message is sent wrapped by the server in an opening prompt (plan/resume/handoff)
#      (routes/prototypes._opening_text). Restoring it as is would show a long instruction as the
#      user's bubble. So for each opening turn we record "prompt sent → text shown on screen"
#      (`record_opening`), and restore joins on the prompt's hash. We do not reverse-parse the
#      template — the prompt is our own wording that changes per language, and a parser relying on
#      its shape goes silently wrong the moment the wording is edited. Opening turns without a record
#      (sessions from before this record existed) show the transcript as is.
#
#   3. An open session's turns are **replayed by the session** (usePrototypeStream.resume streams the
#      turn log from the start). The completion card and the in-progress question form exist only on
#      that path, so the replay cannot be removed, and if history emitted the same turns again the
#      conversation would appear twice. So history is cut before the time the open session's first
#      turn started (`before`).
#
# All records live under `prototypes/{slug}/history/`. Prototype reset deletes all of
# `prototypes/{slug}/` (proto/session.purge_session_state), so they disappear along with the
# conversation, and the project bundle does not include them for the same reason as transcripts
# (_PROTO_CONVERSATION_RE in project_bundle).
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime

from aipds.agent.answer_store import load_answers, save_answers
from aipds.context_usage import load_record
from aipds.models import HistoryItem
from aipds.proto.session_store import transcript_prefix
from aipds.s3store import S3StoreLike, get_by_etag, list_with_etags
from aipds.session_history import transform_cli_transcript

_log = logging.getLogger(__name__)

#: Name of the live question card (same value as _on_can_use_tool in proto/builder.py).
QUESTION_NAME = "prototype-questions"


def history_prefix(slug: str) -> str:
    return f"prototypes/{slug}/history/"


def _inputs_prefix(slug: str) -> str:
    return f"{history_prefix(slug)}inputs/"


def answers_prefix(slug: str) -> str:
    return f"{history_prefix(slug)}answers/"


def context_key(slug: str) -> str:
    """The turn's last context usage (written by proto/session.py)."""
    return f"{history_prefix(slug)}context.json"


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


async def record_opening(s3: S3StoreLike, slug: str, *, prompt: str,
                         shown: str | None) -> None:
    """One opening turn: the `prompt` sent to the agent is shown on screen as `shown`.

    If `shown` is None it was an auto-open (the user said nothing) — restore does not create a
    user bubble. Failures are swallowed: this record is only restore wording, and without it the
    turn simply appears as in the transcript. It is no reason to block the turn.
    """
    try:
        await s3.put(f"{_inputs_prefix(slug)}{_prompt_digest(prompt)}.json",
                     json.dumps({"shown": shown}, ensure_ascii=False))
    except Exception:
        _log.exception("opening-turn record failed for prototype %s", slug)


class AnswerLog:
    """Records the answers the builder received — the same record as Discovery's answer_store, under a different prefix.

    The builder does not know S3 (the session does) or the slug, so the place to record is bundled and passed in.
    The rationale for the record is the same as the agent/answer_store.py header: the transcript's tool_result
    is a sentence the CLI rewrote in English, so the answers cannot be unpacked from it.
    """

    def __init__(self, s3: S3StoreLike, slug: str):
        self._s3 = s3
        self._slug = slug

    async def save(self, *, tool_use_id: str, interrupt_id: str,
                   questions: dict, answers: dict[str, str]) -> None:
        """Never fails the turn. Without a tool_use_id it cannot be joined, so it is
        not written (same discipline as claude_driver._save_answers_quietly)."""
        if not tool_use_id:
            return
        try:
            await save_answers(self._s3, tool_use_id=tool_use_id,
                               interrupt_id=interrupt_id, questions=questions,
                               answers=answers, prefix=answers_prefix(self._slug))
        except Exception:
            _log.exception("prototype answer record failed for %s", self._slug)


async def _load_inputs(s3: S3StoreLike, slug: str) -> dict[str, str | None]:
    """Prompt hash → shown text. Any failure skips only that record."""
    prefix = _inputs_prefix(slug)
    try:
        keys = await s3.list(prefix)
    except Exception:
        _log.exception("opening-turn record listing failed for %s", slug)
        return {}
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    out: dict[str, str | None] = {}
    for key, body in zip(keys, bodies):
        try:
            if isinstance(body, BaseException):
                raise body
            shown = json.loads(body)["shown"]
        except Exception:
            _log.warning("unreadable opening-turn record skipped: %s", key)
            continue
        if shown is None or isinstance(shown, str):
            out[key[len(prefix):].removesuffix(".json")] = shown
    return out


async def _load_sessions(s3: S3StoreLike, slug: str) -> dict[str, list[dict]]:
    """Session id → main transcript lines (in write order). Subagent lines are not read.

    Subagents are not bubbles even live — they appear only as the lead's single Agent tool line and a
    single summary line after they finish. Expanding those conversations would turn one build into hundreds of bubbles.
    """
    root = transcript_prefix(slug)
    # Key shape: {root}{session_id}/main/NNNNNNNN.jsonl (proto/session_store._session_prefix)
    listed = sorted((k, etag) for k, etag in await list_with_etags(s3, root)
                    if k[len(root):].split("/")[1:2] == ["main"])
    keys = [k for k, _ in listed]
    # Parallel GET + ETag cache — the same judgment as agent/session_store.load_transcript.
    # Batches are only appended under seq keys, so on reopening the panel only new batches need to be fetched,
    # and without the cache every past session is re-fetched on each open. Builds add a session on every
    # improvement, so they are heavier than Discovery (observed 2026-10-17: a prototype with 13 sessions, 23 batches, 8.1MB).
    bodies = await asyncio.gather(*(get_by_etag(s3, k, etag) for k, etag in listed),
                                 return_exceptions=True)
    sessions: dict[str, list[dict]] = {}
    for key, body in zip(keys, bodies):
        sid = key[len(root):].split("/", 1)[0]
        lines = sessions.setdefault(sid, [])
        if isinstance(body, BaseException):
            _log.warning("unreadable transcript batch skipped: %s", key)
            continue
        for line in body.splitlines():
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # one line truncated mid-write does not lose the whole session
    return sessions


def _timestamp(line: dict) -> float | None:
    raw = line.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _first_timestamp(lines: list[dict]) -> float:
    for line in lines:
        ts = _timestamp(line)
        if ts is not None:
            return ts
    return float("inf")  # sessions without a timestamp go last — there is no basis to invent an order


def _before(lines: list[dict], cutoff: float) -> list[dict]:
    """Drops lines after `cutoff`. Bookkeeping lines without a timestamp follow the preceding line."""
    for i, line in enumerate(lines):
        ts = _timestamp(line)
        if ts is not None and ts >= cutoff:
            return lines[:i]
    return lines


def _prompt_text(line: dict) -> str | None:
    """The prompt string of a user line. The SDK's query(prompt) is recorded as a plain string."""
    msg = line.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if (isinstance(content, list) and len(content) == 1
            and isinstance(content[0], dict) and content[0].get("type") == "text"):
        return content[0].get("text")
    return None


def _segments(lines: list[dict], inputs: dict[str, str | None]) -> list[list[dict]]:
    """Turns opening prompts into the shown text, and cuts the conversation so turn boundaries are not lost.

    An auto-open has no user bubble. Simply deleting that line would merge the previous turn's last
    bubble and this turn's first bubble into one — because transform_cli_transcript's turn boundary
    is a user utterance. So the conversation is cut there and each piece is converted
    separately.
    """
    out: list[list[dict]] = [[]]
    for line in lines:
        prompt = _prompt_text(line)
        digest = _prompt_digest(prompt) if prompt is not None else None
        if digest is None or digest not in inputs:
            out[-1].append(line)
            continue
        shown = inputs[digest]
        if shown is None:
            out.append([])
            continue
        out[-1].append({**line, "message": {**line["message"], "content": shown}})
    return [seg for seg in out if seg]


async def load_history(s3: S3StoreLike, slug: str, *,
                       before: float | None = None) -> list[HistoryItem]:
    """This prototype's past build conversations. Lines after `before` (epoch seconds) are excluded.

    Any failure is downgraded to an empty list — the same principle as Discovery's list_history
    (history is auxiliary data and does not block the panel).
    """
    try:
        sessions, inputs, answers = await asyncio.gather(
            _load_sessions(s3, slug), _load_inputs(s3, slug),
            load_answers(s3, prefix=answers_prefix(slug)))
        ordered = sorted(sessions.items(),
                         key=lambda kv: (_first_timestamp(kv[1]), kv[0]))
        items: list[HistoryItem] = []
        for _, lines in ordered:
            if before is not None:
                lines = _before(lines, before)
            for seg in _segments(lines, inputs):
                items.extend(transform_cli_transcript(
                    seg, answer_records=answers, question_name=QUESTION_NAME))
        return items
    except Exception:
        _log.exception("prototype history read failed for %s", slug)
        return []


async def load_context(s3: S3StoreLike, slug: str) -> dict | None:
    """Context usage to show after the session closes. Given **only when it is also right for the next conversation**.

    Improving a completed build starts a new SDK session (handoff), so usage restarts from nearly 0 —
    showing the closed session's 85% would read as the opposite of that fact. So it is returned only when
    the record's session id matches the current session.json and there is no handoff that would make
    the next start() a new conversation.
    """
    from aipds.proto.session import handoff_key, session_key
    record = await load_record(s3, context_key(slug))
    if record is None:
        return None
    try:
        current = json.loads(await s3.get(session_key(slug))).get("session_id")
    except Exception:
        return None
    if record.get("session_id") != current:
        return None
    try:
        await s3.get(handoff_key(slug))
    except FileNotFoundError:
        return record
    except Exception:
        return None
    return None
