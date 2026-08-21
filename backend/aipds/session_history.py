# backend/aipds/session_history.py
"""S3 session transcript -> chat history.

Location: `discovery/transcript/{session}/main/NNNNNNNN.jsonl`
          (mirrored by DiscoverySessionStore in agent/session_store.py)
Format:   the CLI transcript, i.e. the Anthropic Messages shape
          {"type":"assistant","message":{"role":...,"content":[{"type":"text"|
           "tool_use"|"tool_result", ...}]}}
          Lines with no message (queue-operation, attachment, ai-title, ...) are mixed
          in.

**Only one format is read.** This used to also read the strands fallback driver's
Bedrock Converse shape (`session_{pid}/agents/agent_default/messages/message_N.json`,
with `toolUse`/`toolResult` block keys). The reader was deleted along with that driver --
a session left in that format gets an empty history list (only test sessions were).

The session store is infrastructure outside the sandbox abstraction, so this module reads
it directly rather than through a Sandbox method.
"""
from __future__ import annotations
import json
import logging
from aipds.agent.answer_store import load_answers
from aipds.agent.questions_payload import (normalize_sdk_questions,
                                                question_file_from_sdk)
from aipds.agent.session_store import load_transcript
from aipds.models import HistoryItem, HistoryTraceEntry
from aipds.parsers.redaction import redact_credentials
from aipds.tool_trace import tool_detail
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

def _parse_answers(raw: str) -> dict[str, str] | None:
    """The body with its prefix stripped -> the answer dict, or None if it cannot be
    unpacked.

    None means a free-prose answer (not JSON). The caller then fills only the text
    fallback -- the frontend has to be able to draw the bubble without a dict.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    # A non-string value (the agent putting in a number, say) is unified to a string
    # too -- keeping the frontend's Record<string, string> contract.
    return {str(k): str(v) for k, v in parsed.items()}


def _answer_fallback_text(body: str, answers: dict[str, str] | None) -> str:
    """The Korean fallback wording, for a consumer that does not know about answers.

    The final wording a human reads is built by the frontend in the UI language
    (HistoryItem.answers). The Korean left here is only a safety net keeping an old
    frontend that does not know that field from showing an empty bubble -- the current
    frontend ignores this value. (That is why the Korean string below is intentional and
    must not be translated: it is what such a client would render.)
    """
    if answers:
        pretty = " · ".join(f"{k}: {v}" for k, v in
                            sorted(answers.items(), key=lambda kv: str(kv[0])))
        return f"답변 제출 — {pretty}"
    return f"답변 제출: {body}"

#: The tools represented as file_changed in a live event (the same set as
#: claude_driver._FILE_TOOLS). History has to use the same representation so the
#: scrollback does not look different from live.
_CLI_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _cli_answer_summary(content: object) -> tuple[str, dict[str, str] | None]:
    """An ask_questions tool_result body -> (fallback wording, answers dict or None).

    The body the CLI writes is an English sentence of its own making (see the
    answer_store.py header), so the answers cannot be unpacked here -- the exact values
    come from `answer_records`. This function is the fallback for an old session with no
    records. Note that it returns a tuple: the caller fills both HistoryItem's text and
    answers.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    answers = _parse_answers(inner)
    body = inner
    return _answer_fallback_text(body, answers), answers


def _is_error_result(block: dict) -> bool:
    """Whether this tool_result is a failure.

    A round the CLI blocked by schema validation arrives with `is_error: true` and a
    `<tool_use_error>` body (measured: the 3 cases where the model passed `questions` as
    a JSON string). **The question never even appeared to the user** in that round, so
    making a card for it on restore leaves a ghost question card nobody ever saw. The
    model reads that error and retries properly on the next turn, so the screen should
    hold exactly one round: the retried one.
    """
    if block.get("is_error") is True:
        return True
    content = block.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return "<tool_use_error>" in text


def _sdk_questions_to_file(raw_input: object) -> dict | None:
    """tool_use.input -> the frontend QuestionFile. None on failure (restore is not
    blocked).

    It reuses the very function that made the card live -- because letter assignment and
    the added Other option have to match what the user actually saw. A question with no
    options at all raises ValueError, and that falls back to the previous display, which
    merely lacks the card's name.
    """
    if not isinstance(raw_input, dict):
        return None
    questions = normalize_sdk_questions(raw_input.get("questions"))
    if not questions:
        return None
    try:
        return question_file_from_sdk(questions, name="discovery-questions")
    except ValueError:
        return None


def transform_cli_transcript(raw: list[dict], *,
                             answer_records: dict[str, dict] | None = None) -> list[HistoryItem]:
    """A CLI transcript (the Anthropic Messages shape) -> chat history.

    The goal is to produce the same representation as the live stream
    (claude_driver._translate) -- text as a bubble, a tool execution as a trace,
    AskUserQuestion as a card. That is what keeps the scrollback from looking different
    from the screen just seen.

    Lines with no message (queue-operation, attachment, ai-title, last-prompt, ...) are
    skipped. Those are the CLI's internal bookkeeping, not conversation.

    **One turn is one bubble.** The CLI writes a separate assistant line for every tool
    call (a measured 5-tool turn: 1 thinking line + 5 tool_use lines + 1 text line), so
    making an item per line splits a turn that was one bubble live into 7, most of them
    empty bubbles with no text -- on screen, a run of empty grey boxes carrying only a
    "reasoning" trace. Live makes one AiItem per turn, accumulates message into that one
    and stacks tools into the same item's trace (useTurnStream.ts:108,121-123), so the
    restore groups them the same way.

    The turn boundary is **a real user utterance**. A user line carrying only
    `tool_result` is not something the user said but a tool execution result, and live
    renders nothing for it -- treating it as a boundary would make every single tool call
    a turn again, back to the original problem.
    """
    records = answer_records or {}
    # Pass 1: collect AskUserQuestion tool_use ids. A real transcript has other
    # tool_results (Write, Read, ...) mixed in, so matching on id is essential to pick
    # out just the answer results.
    #
    # Two more things are collected in the same pass:
    #   ask_files   id -> that round's question payload. tool_use.input still holds the
    #               SDK original in structured form (not prose), so the card can recover
    #               "what was asked". This used to be discarded and the card demoted to
    #               name=None.
    #   errored     rounds the CLI blocked. No card is made for them
    #               (_is_error_result).
    ask_ids: set[str] = set()
    ask_files: dict[str, dict] = {}
    errored: set[str] = set()
    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (block.get("type") == "tool_use"
                    and block.get("name") == "AskUserQuestion"):
                tid = str(block.get("id", ""))
                ask_ids.add(tid)
                qfile = _sdk_questions_to_file(block.get("input"))
                if qfile is not None:
                    ask_files[tid] = qfile
            elif block.get("type") == "tool_result" and _is_error_result(block):
                errored.add(str(block.get("tool_use_id", "")))

    items: list[HistoryItem] = []
    # The assistant turn in progress. It is settled into a single item on meeting a real
    # user utterance or at the end of the transcript.
    turn_texts: list[str] = []
    turn_trace: list[HistoryTraceEntry] = []
    turn_cards: list[HistoryItem] = []

    def flush_turn() -> None:
        """Settle the accumulated assistant turn into one item (plus any cards that follow).

        A bubble is made even with no text and only a trace -- an interrupted turn (idle
        timeout, a dropped SSE) has that shape, and how far the tools got is the only
        record that will remain in the scrollback. The progress indicator that stood
        there live is not something to restore.

        Cards attach **after** the bubble. Live, the model first streams its explanation
        of "why I am asking" and the question card follows (claude_driver's
        _CONTACT_ADDENDUM requires that explanation), so emitting the card immediately
        would invert the order while the bubble is still waiting to be settled, and the
        question would appear without its explanation.
        """
        nonlocal turn_texts, turn_trace, turn_cards
        if turn_texts or turn_trace:
            items.append(HistoryItem(
                role="ai",
                text=redact_credentials("\n".join(turn_texts)) if turn_texts else "",
                trace=turn_trace))
        items.extend(turn_cards)
        turn_texts, turn_trace, turn_cards = [], [], []

    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue  # A bookkeeping line -- no conversation to restore
        role = msg.get("role")
        content = msg.get("content")
        # The first user line's content can be a plain string rather than a list
        # (measured: {"role":"user","content":"Say OK"}). Normalised so the block loop
        # does not iterate a string character by character.
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        texts: list[str] = []
        cards: list[HistoryItem] = []
        trace: list[HistoryTraceEntry] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if text:
                    texts.append(str(text))
            elif btype == "tool_use":
                name = str(block.get("name", ""))
                if name == "AskUserQuestion":
                    # This was a question card live. The question payload is carried
                    # along, so the card can show what was asked.
                    #
                    # No card is made for a round the CLI blocked -- that question never
                    # appeared to the user, and the round the model retried on the next
                    # turn makes its own card (that is what the user actually saw).
                    tid = str(block.get("id", ""))
                    if tid in errored:
                        continue
                    qfile = ask_files.get(tid)
                    cards.append(HistoryItem(
                        role="card", card="questions",
                        name=(qfile or {}).get("name"),
                        questions=qfile))
                elif name in _CLI_FILE_TOOLS:
                    inp = block.get("input")
                    path = ""
                    if isinstance(inp, dict):
                        path = str(inp.get("file_path", ""))
                    trace.append(HistoryTraceEntry(kind="file_changed",
                                                   path=path))
                else:
                    # Read/Glob/Grep/Bash/mcp__aipds__* and so on -- these have to use
                    # **the same representation** as the live status event. That is why
                    # the detail is extracted with the same module (tool_trace): if only
                    # one side shows detail, the screen differs across a refresh.
                    detail = tool_detail(name, block.get("input"))
                    trace.append(HistoryTraceEntry(
                        kind="status", text=name,
                        detail=redact_credentials(detail) if detail else None))
            elif btype == "tool_result":
                tid = str(block.get("tool_use_id", ""))
                if tid in ask_ids and tid not in errored:
                    # What the user actually answered -- the assistant turn in progress
                    # has to be closed first for the order to come out right (the
                    # question card, then the answer bubble).
                    flush_turn()
                    # When an answer record exists it is the truth
                    # (agent/answer_store.py): the question-number -> letter/elaboration
                    # map exactly as we received it, plus the question payload from that
                    # moment. The frontend then builds the same wording with **the same
                    # function** as live. Only a session with no record (from before this
                    # feature) is demoted to the English sentence the CLI wrote -- that is
                    # not re-parsed because a quote inside the question text makes it
                    # ambiguous in principle (there is a measured case).
                    rec = records.get(tid)
                    if rec is not None:
                        answers = {str(k): str(v) for k, v in rec["answers"].items()}
                        items.append(HistoryItem(
                            role="user",
                            text=redact_credentials(
                                _answer_fallback_text("", answers)),
                            answers=answers,
                            questions=rec.get("questions")))
                    else:
                        text, answers = _cli_answer_summary(block.get("content"))
                        items.append(HistoryItem(role="user",
                                                 text=redact_credentials(text),
                                                 answers=answers,
                                                 questions=ask_files.get(tid)))
            # thinking/redacted_thinking and the like are skipped -- not content to
            # restore and show.

        if role == "assistant":
            # Accumulated into the turn. Settling is deferred to the next user
            # utterance (or the end of the transcript) so that the separate line arriving
            # per tool call does not each become its own bubble.
            turn_texts.extend(texts)
            turn_trace.extend(trace)
            turn_cards.extend(cards)
            continue

        # A user line. Only a real utterance is a turn boundary -- a line carrying only
        # tool_result is a tool execution result and live renders nothing for it (the
        # tool_result branch above has already made the answer bubble).
        if texts:
            flush_turn()
            items.append(HistoryItem(role="user",
                                     text=redact_credentials("\n".join(texts))))
        items.extend(cards)

    flush_turn()
    return items


async def list_history(project_s3: S3StoreLike | None,
                       session_id: str) -> list[HistoryItem]:
    """Read the session transcript and transform it. Any failure is demoted to an empty
    list (history is secondary data -- it must not block the screen).

    Why there is one store: deleting the strands fallback driver left `projects/{pid}/` as
    the only prefix to read. This used to take two stores and work out which one held the
    content.

    A None project_s3 gives an empty list (store creation failed -- the route wraps
    that).
    """
    if project_s3 is None:
        return []
    try:
        cli_raw = await load_transcript(project_s3, session_id)
        if not cli_raw:
            return []
        # Answer records live under the same project prefix as the transcript. A failure
        # is demoted by load_answers to an empty dict, so the history restores along the
        # same path as an old session with no records.
        records = await load_answers(project_s3)
        return transform_cli_transcript(cli_raw, answer_records=records)
    except Exception:
        _log.exception("transcript read failed for %s", session_id)
        return []
