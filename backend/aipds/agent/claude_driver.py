# backend/aipds/agent/claude_driver.py — Discovery agent driver on the
# Claude Agent SDK, running IN-PROCESS (no VM). The ONLY Discovery driver; the
# `strands` fallback was deleted (see app.driver_factory for why). It still
# implements runner.py's three-method contract, asserted by
# tests/driver_contract.py's assert_driver_contract -- kept as a separate
# contract file so the interface runner.py depends on stays written down.
#
# Most of the SDK plumbing below (client construction, can_use_tool
# interception, PostToolUse hook, event translation, the queue-polling race
# while a question is pending, --session-id/--resume conflict avoidance) is
# COPIED from aipds/proto/builder.py -- the prototype build driver that
# already solved this exact problem and runs in production. The comments
# documenting *why* each piece exists are carried across unchanged because
# they record findings from real failures; duplicating them here (rather than
# extracting a shared module) is a deliberate, human-ruled choice: the
# boundary between the two drivers isn't known yet, and this task's job is to
# get ClaudeDriver working and passing the contract, not to design that
# abstraction prematurely.
#
# ONE STRUCTURAL DIFFERENCE FROM builder.py, and it is the whole reason this
# file is not a copy-paste: what a pending question does to the turn.
#
#   builder.py keeps ONE SSE stream open across the question round trip. Its
#   `run()` polls forever, and proto/session.py's `send_answers` is not a
#   stream at all -- it just resolves the future (returns bool), after which
#   the SAME `run()` generator keeps yielding.
#
#   Discovery's contract is the opposite: runner.py exposes `send_answers` as
#   its OWN async iterator (runner.py:154-179), and the frontend refuses to
#   submit answers while a stream is still open
#   (frontend/lib/useWorkspaceStream.ts:230, `if (stopRef.current) return`).
#   So `run()` MUST terminate when a question is raised -- yield `questions`,
#   then `done`, and return -- or the user can never answer. runner.py needs
#   that terminal event for a second reason: it syncs the workspace to S3 only
#   on done/error (runner.py:134-140), so a turn parked mid-question would
#   leave everything the agent already wrote in the VOLATILE local workspace.
#
#   `_pump` below therefore stops on a queued `questions` event, leaving the
#   SDK's can_use_tool callback suspended on its future, and `run_answers`
#   resolves that future and relays the REST of the same turn (never a second
#   `query()`).
#
#   What makes that round trip safe is `_MessageReader`: a task that owns the
#   turn's `receive_response()` iterator, keeps reading across the whole
#   question round trip, and collects into a plain inbox on the driver. So the
#   messages the CLI sends while the question is on screen -- or while the SSE
#   client is disconnected -- are held by US, not by anyio, and the answers turn
#   relays them. Read `_MessageReader`'s docstring before editing anything in
#   this area: the alternative shape (peek future + cancel) is unfixable, and it
#   cost three review rounds one lost-message defect each.
#
# The other genuinely new piece is pending-question persistence across a
# backend restart (see _on_can_use_tool/pending/_resume_with_answers) --
# builder.py has no analog because a prototype build's "pending" is purely
# in-memory (PrototypeSession owns its own idle-timeout/close lifecycle);
# Discovery's GET /pending must survive both a page refresh (same process) and
# a backend redeploy (different process), so the payload is mirrored to S3.
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from aipds.agent import prompts
from aipds.agent.answer_store import save_answers
from aipds.agent.discovery_guard import (WRITE_TOOLS, bash_denial,
                                              write_denial)
from aipds.agent.pending_store import (clear_pending, load_pending,
                                              load_pending_file, save_pending,
                                              save_pending_file)
from aipds.agent import reconcile
from aipds.agent.question_file_answers import (looks_like_question_file,
                                                    record_answers)
from aipds.agent.questions_payload import (normalize_sdk_questions,
                                                 question_file_from_sdk)
from aipds.agent.session_store import DiscoverySessionStore
from aipds.agent.workspace_rules import place_rules
from aipds.cli_settings import cli_context_env
from aipds.models import AgentEvent
from aipds.pathsafe import workspace_relative as _rel
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.tool_trace import tool_detail
from aipds.workspace_sync import publish_file

_log = logging.getLogger("aipds.agent")

#: 파일 쓰기 도구. discovery_guard가 소유한다 — PostToolUse(관측)와
#: PreToolUse(차단)가 **같은** 집합을 봐야 한다. 두 벌로 두면 한쪽에만 도구가
#: 추가되어 "관측되지만 막히지 않는" 구멍이 생긴다.
_FILE_TOOLS = WRITE_TOOLS
_LETTERS = "ABCDEFGHIJ"

# How long `_pump` blocks on the next SDK message before checking the
# callback/hook queue again. Copied from builder.py:319 -- see `_pump`'s
# docstring for why the poll exists at all.
_POLL_SECONDS = 0.05

# The turn-failure text the CLI's `ResultMessage.is_error` produces, and the
# "answer the open question first" line, both live in agent/prompts.py now --
# they are per-project-language, so they cannot be module constants. See that
# module's header for why (2026-08-04: an English project was reading Korean
# on every one of these paths).
#
# They are actionable prose, unlike the internal "agent turn failed" the other
# error paths use: those are exception paths where the frontend substitutes its
# own copy, whereas these are ordinary ends of a turn the user watched happen.

# `ResultMessage.terminal_reason` 값 중 "취소됨"을 뜻하는 것들
# (claude_agent_sdk/types.py:1249-1257): interrupt()로 끊긴 턴은 스트리밍 중이었든
# 도구 실행 중이었든 이 둘 중 하나로 온다. 리터럴을 분기 안에 박지 않고 여기 모아
# 두는 이유는 세 번째 값이 SDK에 추가됐을 때 이 목록 하나만 고치면 되게 하려는
# 것이다.
_INTERRUPTED_TERMINAL_REASONS = frozenset({"aborted_streaming", "aborted_tools"})

#: 중단된 턴을 표시하는 `status` 이벤트의 text. **기계 신호이고 사람이 읽는
#: 문구가 아니다** — frontend/lib/useWorkspaceStream.ts가 이 값을 비교해
#: interrupted 플래그를 세우고, 화면 문구("중단됨"/"Interrupted")는 프론트가
#: UI 언어로 그린다. proto/builder.py가 같은 값을 쓴다 — 두 드라이버가 어긋나면
#: 프론트가 경로에 따라 다르게 동작한다.
#:
#: 언어 중립인 이유가 승인 마커(frontend/lib/approvalMarker.ts)와 다르다는 점에
#: 주의: 저쪽은 에이전트에게 가고 트랜스크립트에 사용자 말풍선으로 남으므로
#: 프로젝트 언어의 단어여야 한다. 이 마커는 라이브 SSE 큐에만 있고 아무도
#: 읽지 않는다.
INTERRUPTED_MARKER = "interrupted"

# Discovery runs with a human in the loop watching the chat, unlike the
# unattended prototype build -- but AskUserQuestion is still routed through
# can_use_tool (see _on_can_use_tool below) and every other tool must execute
# without a separate approval round-trip, since the UI's only approval
# mechanism IS the questions card. Kept as the same default as builder.py's
# DEFAULT_PERMISSION_MODE for the same reason: any mode that can prompt stalls
# the turn with no operator to answer the CLI-level prompt.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

#: 질문 파일을 PostToolUse 훅에서 **파일 그대로** 물을지. **기본 켜짐.**
#:
#: 이것이 유일한 질문 경로다 — 켜지면 AskUserQuestion 호출은 거부되고 거부
#: 메시지가 질문 파일을 쓰라고 돌려보낸다. 둘을 동시에 켜면 같은 질문이 화면에
#: 두 번 뜬다(에이전트가 파일을 쓰고, 훅이 카드를 띄우고, 이어서 도구까지 부른다).
#:
#: **왜 기본이 켜짐인가.** 파일에 쓴 질문을 이 도구의 입력으로 다시 만들면서
#: 실측 19문항 중 15개(79%)가 훼손됐다 — 한글 문자 치환 11건("푸로토하이프가 …
#: 어느 쉘입니까?"가 화면에 떴다), 축약으로 답변 유실 4건. 파일을 그대로 읽으면
#: 그 실패 종류가 사라진다.
#:
#: 2026-08-17에 실제 Discovery 턴으로 한 바퀴 돌려 뒤집었다: 훅이 카드를 띄우고
#: 턴이 멈추고, 답변이 파일에 기록되고, **다음 턴에 모델이 그 답을 읽어 워크플로우를
#: 이어갔다**(AskUserQuestion을 다시 부르지 않았다). 그 마지막 지점이 유일한
#: 미검증 항목이었다.
#:
#: 탈출로: 이 env를 falsy로 두면 옛 경로로 돌아간다. 인스턴스에서는 user-data가
#: systemd `Environment=`로 값을 주입하므로 그 파일을 고치면 인스턴스 교체가
#: 필요하다 — 대신 gitignore된 `backend/.env`를 만들면 `aipds-update`가
#: 되돌리지 않으므로(추적되지 않는 파일) 재배포 없이 끌 수 있다.
FILE_QUESTIONS_ENV = "AIPDS_FILE_QUESTIONS"

#: 불리언 env를 **끄는** 쪽으로 읽을 값. cli_settings.py·routes/proto_public.py의
#: `_TRUTHY`와 같은 규율의 반대편이다 — 기본이 켜짐인 설정은 "값이 없음"을 켜짐으로
#: 읽어야 하므로 켜는 목록이 아니라 끄는 목록이 필요하다.
_FALSY = {"0", "false", "no", "off"}


def _file_questions_enabled() -> bool:
    import os
    return os.environ.get(FILE_QUESTIONS_ENV, "").strip().lower() not in _FALSY



class _MessageReader:
    """Owns ONE `receive_response()` iterator and drains it into an inbox.

    This class exists to make a whole bug class unrepresentable, so its
    rationale is worth stating in full.

    `_pump` has to race two sources: the SDK's next message, and the
    hook/tool-callback queue (while an AskUserQuestion is parked the CLI is
    blocked on the permission response and `receive_response()` yields nothing
    at all, so a plain `async for` would never let the queued `questions` event
    reach the SSE stream). The obvious way to race them is a *peek future* over
    `agen.__anext__()`, polled on a short timeout — which is what this driver
    did for three review rounds, and it lost messages in every one of them.

    Why the peek future cannot be made safe, probed on a real anyio
    memory-object stream (the SDK owns exactly such a stream —
    claude_agent_sdk/_internal/query.py:121):

      - `send_nowait` hands an item DIRECTLY to a parked receiver and does not
        buffer it (anyio/streams/memory.py:220-231). So the item's only copy
        lives in that receiver.
      - Cancelling the peek future therefore DESTROYS the item; it is not left
        in the buffer for a later iterator. Measured: `(parked, buffered)`
        goes `(1, 0) -> (0, 0)` on the send, and a fresh iterator then times
        out.
      - Every `await` and every `yield` between arming the peek and cancelling
        it is a window where that can happen. Each round closed one window and
        left (or opened) another: the queue drain, the second sweep, `yield
        done`, the pre-terminal drain, the abandoned exit.
      - "Cancel before suspending, re-arm afterwards" cannot fix it either:
        cancelling `agen.__anext__()` CLOSES the async generator. Probed —
        re-arming the same iterator after a cancel raises StopAsyncIteration,
        and the item is only recoverable through a brand-new iterator. So the
        peek future has no safe suspension protocol at all.

    The fix is to stop racing the message at all. A dedicated task owns the
    iterator and never stops reading; `_pump` only ever consumes from
    `inbox`, a plain list on the driver. Then:

      - The pump owns no cancellable RECEIVE, so no suspension of the pump can
        destroy a message in flight from the SDK. There is no window to
        enumerate.
      - Unread messages live in OUR inbox, not in anyio's buffer, so they
        survive the consumer abandoning the generator (SSE disconnect, proxy
        timeout, navigation — runner.py:144-152 takes that path routinely) and
        are picked up by the answers turn. Probed: a reader nobody cancels
        receives every item sent after the consumer walked away.
      - Each message is popped exactly once, so double translation (and any
        side effect a message carries) is impossible by construction.

    The only cancellable wait left is `settle()`'s, and it waits on an
    `asyncio.Event`, which carries no payload — cancelling it cannot lose
    anything. That is the whole structural difference.

    NOTE what this does NOT buy on its own, because assuming otherwise was the
    round-4 regression: moving ownership here protects a message only while it
    is IN `inbox`/`outbox`. An event copied out into a local list before being
    yielded is back to living in a frame `GeneratorExit` destroys. `_pump`'s
    invariant 2 states the ownership rule that closes that half.

    The reader is cancelled only when the turn it belongs to is being
    REPLACED (`_retire_reader`) or the subprocess is going away
    (`disconnect`); never while the turn may still continue.
    """

    def __init__(self, agen) -> None:
        self._agen = agen
        self.inbox: list = []
        # Translated events not yet delivered to the consumer. This is their
        # DURABLE HOME -- see `_relay`'s ownership rule. It lives on the reader
        # so its lifetime is exactly the turn's: `_retire_reader` disposing of
        # the reader disposes of the undelivered events of the turn nobody will
        # relay, and a new turn starts with an empty one.
        self.outbox: list[AgentEvent] = []
        self.ended = False
        self.error: BaseException | None = None
        # Set when the CLI's ResultMessage says the turn FAILED (is_error).
        # Not an exception: the turn ran, produced messages, and ended
        # cleanly -- it just ended in failure. Kept separate from `error`
        # (which is a raised exception from the iterator) because the two need
        # different terminal events: `error` propagates out of `_pump` for
        # `_stream` to degrade, while this one only changes which terminal
        # event `_pump` emits. See `_translate`'s ResultMessage branch.
        self.failed = False
        self.transcript_flushed = False
        # Woken on every append so the pump reacts immediately instead of
        # waiting out its poll interval; sticky, cleared by settle().
        self.wake = asyncio.Event()
        self.task: asyncio.Task = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        try:
            async for msg in self._agen:
                self.inbox.append(msg)
                self.wake.set()
        except asyncio.CancelledError:
            # Deliberate teardown (see _retire_reader/disconnect) -- must not
            # be recorded as a turn failure.
            raise
        except Exception as e:
            # Captured rather than left on the task, so nothing logs
            # "Task exception was never retrieved" and `_pump` can raise it on
            # the caller's stack, where `_stream` degrades it to the contract's
            # "agent turn failed".
            self.error = e
        finally:
            self.ended = True
            self.wake.set()

    async def settle(self, timeout: float) -> None:
        """Wait for something new, or `timeout`.

        The ONE cancellable await `_pump` owns. It waits on an Event, which
        carries no item, so a cancellation here cannot destroy a message --
        unlike awaiting the message future itself.

        No lock and no re-check after `clear()`: one event loop, and `_drain`
        appends to `inbox` BEFORE it sets the event, so the only ordering that
        could lose a wakeup (clear -> append -> set) cannot happen without an
        await between the check and the clear, and there is none. A stale
        wakeup is harmless anyway -- `_pump` just harvests an empty inbox and
        loops.
        """
        if self.inbox or self.ended:
            return
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), timeout)
        except TimeoutError:  # asyncio.TimeoutError is TimeoutError on 3.11
            pass


def _iid_of(event: AgentEvent) -> str | None:
    """The interrupt_id inside a `questions` event's payload, or None."""
    if not event.payload:
        return None
    try:
        value = json.loads(event.payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _validate_permission_mode(mode: str) -> str:
    """Reject an unknown mode here rather than letting it reach the CLI.

    An unrecognized --permission-mode kills the subprocess during connect(),
    which run() reports as a generic "agent turn failed" -- the exact
    late-and-opaque failure mode the --session-id/--resume clash produced in
    the prototype builder. The valid set is read off the SDK's own Literal so
    it cannot drift.
    """
    from typing import get_args

    from claude_agent_sdk.types import PermissionMode

    valid = get_args(PermissionMode)
    if mode not in valid:
        raise ValueError(
            f"unknown permission_mode {mode!r}; expected one of {', '.join(valid)}")
    return mode


def _sdk_session_id(session: dict) -> tuple[str, bool]:
    """(session_id, resume) for ClaudeAgentOptions -- a UUID, always.

    The CLI rejects a non-UUID outright: probed against the bundled binary,
    `claude --session-id=pilot1 -p hi` prints "Error: Invalid session ID. Must
    be a valid UUID." and exits 1. That kills the subprocess during connect(),
    which run() can only report as a generic "agent turn failed" -- i.e. every
    turn of the workshop dies opaquely.

    And Discovery's session id is NOT a UUID today: app.py:299-304 sets
    `session_id = project_id`, which is free-form user input (routes/
    projects.py's CreateProject does not validate it), so a project named
    "pilot1" is a 100% failure. proto/session.py:124-138 already solved this
    for the builder -- mint a UUID and treat a non-UUID value as "start
    fresh" -- so the same judgment is carried over here rather than trusting
    the caller.

    Deriving the UUID from the project id (uuid5) rather than uuid4 keeps it
    STABLE across restarts, which is what makes `--resume` able to find the
    transcript at all; a fresh random id every process would silently start a
    new conversation each redeploy. A value that is already a UUID is passed
    through untouched, so a caller that does the right thing is not overridden.

    The `resume` flag this returns is the CALLER's request, not the decision.
    Stability alone is what makes `--session-id` collide (see
    `_transcript_exists`), so `_ensure_client` overrides this flag from the
    transcript on disk; `_resume_with_answers` still passes resume=True to
    express intent, and a caller asking to resume a session that has no
    transcript must not be taken at its word either.
    """
    import uuid

    raw = session.get("session_id") or ""
    resume = bool(session.get("resume"))
    try:
        return str(uuid.UUID(str(raw))), resume
    except (ValueError, AttributeError, TypeError):
        pass
    if not raw:
        # Nothing to derive from: a random id is still better than a value the
        # CLI will reject, but there is no transcript to resume.
        return str(uuid.uuid4()), False
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aipds:{raw}")), resume


def _transcript_path(config_dir: str, workspace: str, session_id: str) -> Path:
    """Where the CLI keeps this (cwd, session_id) pair's transcript.

    `<config_dir>/projects/<encoded cwd>/<session_id>.jsonl`, where the
    encoding replaces every character outside [A-Za-z0-9-] with "-". Both the
    layout and the encoding are measured against the bundled binary (2.1.220),
    not guessed -- eight cwds were probed, including spaces, dots, "+", "=",
    underscores and Hangul, and `re.sub(r"[^A-Za-z0-9-]", "-", cwd)` reproduced
    every directory name the CLI created. The cwd is resolved first because the
    CLI encodes the REAL path: with cwd a symlink to /tmp/c1probe/realws the
    transcript landed under `-tmp-c1probe-realws`, not under the link name.

    Note the encoding is lossy (Hangul project ids all collapse to runs of
    "-"), so two different workspaces could in principle share a directory.
    That is the CLI's own namespace and this function's job is only to look in
    the same place it does; a collision would make this probe say "resume" for
    a session the CLI can also resume, which is consistent either way. The
    session id itself is per-project (uuid5 of the project id), so the FILE
    within that directory is still distinct.
    """
    try:
        cwd = str(Path(workspace).resolve())
    except OSError:  # pragma: no cover -- unresolvable cwd; use it verbatim
        cwd = workspace
    return (Path(config_dir) / "projects"
            / re.sub(r"[^A-Za-z0-9-]", "-", cwd) / f"{session_id}.jsonl")


def _transcript_exists(config_dir: str, workspace: str, session_id: str) -> bool:
    """Whether the CLI already has a conversation under this id and cwd.

    This is THE decision `--session-id` vs `--resume` turns on, and it is not a
    heuristic: the CLI's "already in use" check IS this file's existence.
    Probed against 2.1.220 -- after `claude --session-id=<id>` succeeded once,
    a second run with the same id in the same cwd exits 1 with "Session ID
    <id> is already in use."; moving that one .jsonl aside made the SAME id
    succeed again. And the two errors are exact complements, so neither flag is
    safe unconditionally:

        --session-id=<id> with a transcript   -> exit 1 "already in use"
        --resume=<id>     with no transcript  -> exit 1 "No conversation found"

    Either one kills the subprocess inside connect(), which run() can only
    report as "agent turn failed" -- permanently, since the transcript file is
    durable, and a poisoned client cache used to make it permanent per project
    as well (see _ensure_client). The failure needs no restart to trigger:
    disconnect() on DELETE /projects/acme followed by re-creating `acme` in the
    same process hits it on the second turn.

    A filesystem probe rather than a flag we carry ourselves, because the CLI's
    own state is the only thing that can answer it. Anything we persisted
    separately (S3, a driver attribute) would be a second source of truth that
    drifts the moment the config dir is recycled, the instance is replaced, or
    a deploy wipes /opt/aipds -- and the drift is silent in both
    directions. proto/session.py:124-138 makes the same "resume only if the
    saved id is real" call for the builder; the difference is only WHERE the
    evidence lives (it owns its S3 record, we read the CLI's disk).

    Racy in principle (the file could appear between this check and connect),
    but the writer is the CLI subprocess we are about to start, and this
    driver is single-turn (`_acquire_turn`), so nothing else in the process is
    starting a session for this workspace concurrently.
    """
    try:
        return _transcript_path(config_dir, workspace, session_id).is_file()
    except OSError:  # pragma: no cover -- an unreadable config dir
        _log.exception("transcript probe failed — starting a fresh session")
        return False


def _default_client_factory(driver: "ClaudeDriver") -> Callable[[dict], Any]:
    def make(session: dict):
        from claude_agent_sdk import (
            ClaudeAgentOptions, ClaudeSDKClient,
        )
        from claude_agent_sdk.types import HookMatcher

        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            # Swap the config HOME rather than disabling settings entirely
            # (setting_sources=[]): the bundled binary is ordinary Claude Code
            # and reads ~/.claude when this is unset, which on the workshop
            # EC2 is the operator's personal skills/agents/CLAUDE.md. Beyond
            # that leak, Discovery needs its OWN config dir rather than the
            # prototype builder's proto-config: sharing would leave the
            # prototype's shadcn-design skill instruction active while
            # Discovery writes documents (discovery-config/README.md).
            "CLAUDE_CONFIG_DIR": driver._config_dir,
        }
        if driver._anthropic_model:
            env["ANTHROPIC_MODEL"] = driver._anthropic_model
        # 자동 컴팩션 시점. 미설정이면 키가 없고 CLI 기본값으로 간다.
        # Discovery가 후반 스테이지에서 요약된 컨텍스트로 문서를 쓰는 것을
        # 늦추는 스위치다(cli_settings 헤더의 실측 264k→53k).
        env.update(cli_context_env())
        session_id, resume = _sdk_session_id(session)
        options = ClaudeAgentOptions(
            permission_mode=driver._permission_mode,
            cwd=driver._workspace,
            env=env,
            # "user" now means OUR config dir (discovery-config/), so this is
            # safe -- and it is what lets the CLAUDE.md there be discovered.
            setting_sources=["user", "project"],
            # `skills` intentionally NOT set: unlike the prototype builder
            # (skills="all", for shadcn-design), Discovery's upstream AI-PLC
            # setup has no skills of its own -- it is CLAUDE.md plus on-demand
            # rule-file reads (discovery-config/README.md).
            # **커스텀 도구가 없다.** `mcp_servers`와 `allowed_tools`를 아예 넘기지
            # 않는다(둘의 SDK 기본값이 `{}`/`[]`이므로 내장 도구는 그대로다).
            #
            # 셋이었다: `report_stage`와 `handoff_prototype`이 2026-08-18에,
            # `submit_document`가 2026-08-21에 PostToolUse 훅으로 옮겨 갔다. 판정
            # 기준은 매번 같았다 — **신호가 워크스페이스에서 유도되는가.** 도구는
            # 모델이 부르지 않으면 침묵하고, 그 침묵이 세 번 실측됐다
            # (agent/reconcile.py 헤더 + useWorkspaceStream.ts:177).
            #
            # 도구를 없애면 두 값을 함께 돌려받는다: 호출마다 붙던 추론 왕복과, 매 턴
            # 컨텍스트에 실리던 도구 설명. 되살릴 때는 그 값을 내는 것이므로,
            # "신호가 파일에서 유도되지 않는다"를 먼저 보여야 한다.
            # Exactly one of the two, never both: the CLI rejects
            # `--session-id` alongside `--resume` unless `--fork-session` is
            # also passed ("--session-id can only be used with --continue or
            # --resume if --fork-session is also specified"), which killed
            # every resumed build at connect() in the prototype driver.
            # Forking is not the fix either -- it would continue under a NEW
            # id, orphaning the transcript under the old one. `--resume=<id>`
            # alone already keeps the session on that same id, which is all
            # session_id bought us.
            #
            # WHICH one is not a property of the call site: both flags exit 1
            # when they disagree with the transcript on disk (`--session-id` on
            # an existing one, `--resume` on a missing one). The caller has
            # already settled it -- `ClaudeDriver._resolve_resume` probes the
            # CLI's own transcript file and puts the answer in `session`
            # ("resume"), so this factory only spells the choice out.
            session_id=None if resume else session_id,
            resume=session_id if resume else None,
            # Mirror transcript batches to S3. Without it the conversation
            # lives ONLY in the CLI's local .jsonl, so an EC2 replacement or
            # redeploy loses the whole Discovery history -- and chat restore had
            # nothing durable to read (session_history was still pointed at
            # strands' S3 layout, which this driver never writes). Same
            # mechanism the prototype builder already uses (proto/builder.py's
            # session_store), different key prefix.
            session_store=driver._session_store,
            # Flush once at AI-PDS's explicit turn boundary. `_pump`
            # handles normal/question terminals, while stream finally blocks
            # cover errors and abandoned SSE consumers.
            session_store_flush="batched",
            # Kept even under bypassPermissions, which the SDK warns shadows
            # this callback entirely. The warning overstates our case: probed
            # against the real CLI (see builder.py), Bash/Write do skip the
            # callback, but AskUserQuestion still reaches it -- and that is
            # the only tool we intercept (it is how a question becomes an
            # SSE `questions` event). Dropping the callback to silence the
            # warning would break that.
            can_use_tool=driver._on_can_use_tool,
            # PreToolUse가 이 제품의 유일한 실효 게이트다. 위 can_use_tool은
            # bypassPermissions에서 Write/Bash에 대해 호출되지 않는다 — SDK가
            # 그 사실과 해법을 직접 적어 뒀다(types.py의
            # _get_can_use_tool_shadowed_warning: "To gate every tool call, use
            # a PreToolUse hook instead").
            #
            # **matcher에 AskUserQuestion을 넣지 않는다.** types.py의 can_use_tool
            # 설명에 따르면 PreToolUse 훅이 *allow*를 돌려주면 can_use_tool도
            # 건너뛴다 — 질문 가로채기가 그 콜백에 있으므로 그러면 질문 왕복
            # 전체가 죽는다. 같은 이유로 _on_pre_tool_use는 통과시킬 때
            # "allow"가 아니라 **빈 dict**를 돌려준다.
            hooks={
                "PreToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit|Bash",
                                           hooks=[driver._on_pre_tool_use])],
                "PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                            hooks=[driver._on_post_tool_use])],
            },
        )
        return ClaudeSDKClient(options=options)
    return make


def _suppress_shadowed_callback_warning() -> None:
    """Mute CanUseToolShadowedWarning -- for THIS wiring it is a false
    positive.

    See the can_use_tool comment above: the callback exists only for
    AskUserQuestion, which still reaches it under bypassPermissions. The
    warning fires on every connect() and would otherwise train operators to
    ignore backend warnings. Scoped to this one category, never a blanket
    filter, so a genuinely new SDK warning still surfaces.
    """
    import warnings
    try:
        from claude_agent_sdk import CanUseToolShadowedWarning
    except ImportError:  # older/newer SDK without the category -- nothing to mute
        return
    warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


class ClaudeDriver:
    """Discovery agent driver. Implements runner.py's 3-method contract.

    One connected ClaudeSDKClient, kept across turns so the subprocess and its
    transcript persist. Hook/tool callbacks run on the SDK's own tasks while
    the turn drains on the caller's loop -- both on the SAME event loop, so a
    plain list handoff is safe (no cross-thread locking).
    """

    def __init__(self, workspace: str, rules_dir: str, config_dir: str,
                 s3: S3StoreLike, anthropic_model: str | None = None,
                 language: str = "ko",
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[dict], Any] | None = None,
                 session_store: Any = None):
        self._workspace = workspace
        self._rules_dir = rules_dir
        self._config_dir = config_dir
        self._s3 = s3
        # Transcript mirror. Built here rather than per-turn so the sequence
        # counter it seeds from S3 is reused across turns of one process
        # (session_store.py's header explains why that seeding exists).
        # Injectable so a test can pass None and skip S3 entirely.
        self._session_store: Any = (session_store if session_store is not None
                                    else DiscoverySessionStore(s3))
        self._anthropic_model = anthropic_model
        # 이 프로젝트의 생성물 언어. 두 곳으로 흐른다: place_rules(워크스페이스
        # CLAUDE.md의 언어 지시)와 이 드라이버가 만드는 모델·사용자 대상
        # 텍스트(agent/prompts.py). 셋이었던 시절의 세 번째는 커스텀 도구의 설명·반환
        # 문자열이었고, Discovery의 도구가 2026-08-21에 사라지면서 그 채널도 사라졌다.
        #
        # 처음에는 place_rules 하나뿐이었고 그것이 결함이었다 — 지시만 영어로
        # 바꿔도 도구 설명과 거부 메시지가 한국어로 남아 매 턴 모델 컨텍스트에
        # 들어갔다. 공유 CLAUDE_CONFIG_DIR은 전 프로젝트가 공유하므로 여전히
        # 프로젝트 언어를 담을 수 없다(그래서 그쪽 문서는 언어 중립이어야 한다).
        self._language = language
        self._permission_mode = _validate_permission_mode(permission_mode)
        self._client_factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        # A plain list, not collections.deque -- same as builder.py, for the
        # same reason: everything here runs on one event loop, and a per-turn
        # queue is never long enough for deque's O(1) popleft to matter.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._turn_token: object | None = None
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None
        # (도구 이름, detail) — 이름만으로 접으면 파일이 다른 연속 Read가
        # 한 줄로 뭉개진다. INTERRUPTED_MARKER 비교는 event.text로 하므로
        # 이 키가 튜플이 되어도 그 경로는 영향받지 않는다.
        self._last_status: tuple[str, str | None] | None = None
        # rel path → 그 파일에서 **이미 물어본 미답 문항 집합**. 같은 집합을 두 번
        # 묻지 않는 가드다(_file_question_round 참조). 드라이버 인스턴스가 프로젝트
        # 수명을 살기 때문에 턴을 넘어 유지된다 — 백엔드 재시작 시 비지만, 그때는
        # 답변이 이미 파일에 있으므로 "미답 문항 없음" 조건이 재질문을 막는다.
        self._asked_question_sets: dict[str, tuple[str, ...]] = {}
        # rel path → 파싱 실패를 이미 알린 내용. 같은 내용에 같은 노트를 반복하지
        # 않되, 내용이 달라지면 다시 알린다(_on_post_tool_use 참조).
        self._unparsed_noted: dict[str, str] = {}
        # 스테이지 이름 → 이미 흘린 상태. `aiplc-state.md`에서 유도한 `stage`
        # 이벤트의 diff 커서다(agent/reconcile.stage_events). 프론트가 이벤트를
        # 누적하므로 같은 상태를 다시 흘리면 사이드바 목록이 자란다.
        #
        # 드라이버 인스턴스가 프로젝트 수명을 사는 것에 의존하지 않는다: 백엔드
        # 재시작으로 커서가 비면 다음 상태 파일 쓰기에서 전체를 한 번 다시 흘리고,
        # 프론트는 그 시점에 이미 REST로 상태를 읽어 화면을 세운 뒤다.
        self._stage_status: dict[str, str] = {}
        # 이미 `prototype_ready`를 흘린 프로토타입 id. 두 번 알리면 채팅에 카드가
        # 두 장 뜬다(agent/reconcile.prototype_events).
        self._handed_off: set[str] = set()
        # 산출물 경로 → (버전, 내용 해시). `document` 이벤트의 diff 커서다
        # (agent/reconcile.document_events). 해시가 "바뀌었나"를, 서수가 "몇 번째
        # 갱신인가"를 답한다 — 배너의 닫기가 버전을 기억하므로 갱신마다 값이
        # 달라져야 한다. 재시작으로 비면 서수가 1로 돌아가고, 그 대가는
        # reconcile.document_events가 적어 뒀다.
        self._doc_versions: dict[str, tuple[int, str]] = {}
        # 위 커서를 디스크 상태로 한 번 채웠는지. `_seed_document_cursor` 참조.
        self._docs_seeded = False
        self._current_session_id: str | None = None
        # The task draining the current turn's receive_response(). Outlives the
        # `run()` generator on purpose when a question parks the turn -- that is
        # what keeps the rest of the turn's messages for `run_answers`. See
        # _MessageReader.
        self._reader: _MessageReader | None = None
        self._on_file_published: (
            Callable[[str, str, str | None], None] | None
        ) = None

    # ---- plumbing ----

    # `_publish`가 여기 있었다. `report_stage`가 상태 파일을 직접 쓰면서 PostToolUse
    # 훅을 지나지 않았기 때문에 그 도구에게 게시자를 손에 쥐여 줘야 했다. 그 도구가
    # 훅으로 옮겨 간 뒤(agent/reconcile.py) 상태 파일도 다른 산출물과 같은 경로로
    # 게시되므로 예외가 사라졌고, 예외를 위한 게시자도 사라졌다. 게시는
    # `_on_post_tool_use`가 `publish_file`을 직접 부른다.

    def _emit(self, event: AgentEvent) -> None:
        """이벤트를 턴 큐에 넣는다.

        커스텀 도구에게 넘기는 `emit` 싱크였다. 그 도구들이 훅으로 옮겨 간 뒤
        (마지막이 2026-08-21의 `submit_document`) 프로덕션 호출부가 없어졌지만,
        `_queue`에 직접 append하는 것보다 의도가 드러나므로 남긴다 — 유도된
        이벤트는 `_emit_stage_events`·`_emit_document_events`·`_handoff_stop`이
        각자 append한다.
        """
        self._queue.append(event)

    def set_file_published_callback(
        self,
        callback: Callable[[str, str, str | None], None] | None,
    ) -> None:
        self._on_file_published = callback

    # There is deliberately NO `drain_queue()` here, though builder.py:183 has
    # one and this file otherwise mirrors it. A batch pop is precisely the
    # defect round 5 fixed: it moves events out of the queue that owns them and
    # into the caller's frame, which `GeneratorExit` destroys, so every one not
    # yet yielded is lost. Every consumer of `_queue` on this driver goes
    # through `_relay_queue` instead. Re-adding a batch drain would reintroduce
    # the bug at whatever call site used it.

    async def _relay_queue(self) -> AsyncIterator[AgentEvent]:
        """Yield queued tool/hook events, popping each only after delivery.

        Same ownership rule as `_pump`'s `relay` (read that one for the full
        argument): an event stays at the head of `self._queue` across the
        `yield`, so a consumer that walks away mid-sequence leaves the remainder
        owned rather than stranded in a dead generator's frame. Used by the
        error paths, which yield whatever a tool queued before the failure.
        """
        while self._queue:
            ev = self._queue[0]
            yield ev
            if self._queue and self._queue[0] is ev:
                self._queue.pop(0)

    def _resolve_resume(self, session: dict) -> dict:
        """Decide `--session-id` vs `--resume` from the transcript on disk.

        The caller's `resume` flag states INTENT (only
        `_resume_with_answers` sets it) and cannot be trusted in either
        direction, because the CLI's two failure modes are exact complements
        and both kill connect(): `--session-id` on an id that already has a
        transcript exits 1 ("already in use"), and `--resume` on an id that has
        none exits 1 ("No conversation found"). See `_transcript_exists`, where
        both are measured against the bundled binary.

        So the truth is the file, and the flag is overridden by it -- upward as
        well as downward. Upward is the case C1 was: the DEFAULT path
        (`run()` -> `_stream(resume=False)`) is what breaks after a restart,
        because the id is stable by design and the transcript outlives the
        process. Downward matters on the answers path, where a redeploy that
        also recycled the config dir would otherwise resume a session the CLI
        cannot find.

        Returns a COPY -- `session` belongs to app.py (one dict per project,
        shared by every turn), so mutating it would make one turn's resume
        decision leak into the next.
        """
        session_id, requested = _sdk_session_id(session)
        resume = _transcript_exists(self._config_dir, self._workspace,
                                    session_id)
        if resume != requested:
            # Worth a line in the log: on the answers path this is the
            # difference between continuing the conversation the question came
            # from and starting a blank one, and after a restart it is the
            # difference between a working turn and a dead project.
            _log.info("resume=%s for session %s (caller asked %s) — "
                      "transcript %s", resume, session_id, requested,
                      "found" if resume else "absent")
        out = dict(session)
        out["resume"] = resume
        return out

    async def _ensure_client(self, session: dict):
        if self._client is None:
            started = time.perf_counter()
            _suppress_shadowed_callback_warning()
            client = self._client_factory(session)
            try:
                await client.connect()
            except BaseException:
                # Do NOT cache a client that never connected. It used to be
                # assigned before this await, so one failed connect poisoned
                # the driver for the life of the process: every later turn
                # returned the same broken object and died in
                # receive_response() with "CLIConnectionError: Not connected"
                # (probed: 3 consecutive turns, `_client` non-None each time).
                # That is what turned C1 from one bad turn into a dead project,
                # and it would do the same for any transient connect failure
                # (a Bedrock throttle at startup, a config dir not yet
                # mounted). Leaving the cache empty makes the next turn build a
                # fresh client and re-probe the transcript.
                #
                # BaseException, not Exception: a cancelled connect (SSE
                # disconnect mid-turn) leaves an equally unusable client.
                self._client = None
                raise
            self._client = client
            log_performance(
                _log,
                self._current_session_id, "connect", started, cold="true")
        return self._client

    async def _flush_transcript_mirror(self) -> bool:
        """Force this turn's mirrored transcript out to S3.

        Reaches into the SDK's batcher (`client._query`) rather than calling a
        public API, because there isn't one: `flush` is driven by the read loop
        on `result`/`close()`, and Discovery hits neither (see the call site).
        Guarded with getattr and a broad except precisely BECAUSE it is a
        private handle -- an SDK upgrade that moves or renames it must cost the
        durability of chat history, not the turn the user is watching.
        """
        started = time.perf_counter()
        batcher = getattr(getattr(self._client, "_query", None),
                          "_transcript_mirror_batcher", None)
        if batcher is None:
            log_performance(
                _log,
                self._current_session_id, "transcript_flush", started,
                available="false")
            return True
        try:
            await batcher.flush()
        except Exception:
            _log.exception("transcript mirror flush failed — "
                           "chat history for this turn may be lost")
            return False
        finally:
            log_performance(
                _log,
                self._current_session_id, "transcript_flush", started,
                available="true")
        return True

    async def interrupt(self) -> None:
        """진행 중인 턴을 끊는다. 지금까지 한 작업은 살린다.

        proto/builder.py의 interrupt를 패턴으로 따르되 한 가지가 다르다 —
        Discovery의 pending 질문은 S3에도 미러링된다(agent/pending_store.py).
        인메모리만 지우면 `GET /pending`이 답할 수 없는 질문을 복원하고,
        사용자가 제출한 답변은 아무도 듣지 않는 future를 resolve한다.

        순서가 load-bearing이다: 우리 상태를 먼저 정리하고 마지막에 클라이언트를
        건드린다. client.interrupt()가 던져도 pending이 남지 않는다.

        멱등: 돌고 있는 턴이 없으면 아무것도 하지 않는다. 라우트는 세션 유무만
        보고 이 메서드를 부르므로 이미 끝난 턴에 대한 요청이 정상적으로 들어온다.

        `_turn_token`이 아니라 `_turn_active`/`_pending_question`으로 판단한다.
        질문에서 파킹된 턴은 run()의 제너레이터가 questions -> done으로 끝나며
        `_release_turn`이 이미 돌아 `_turn_token`이 None이 된 뒤에도 CLI
        서브프로세스와 `_pending_question` future는 여전히 살아 있다(파일 상단
        "single-turn slot" 절 참고) — `_turn_token`으로 판단하면 정확히 중단해야
        할 그 상태(파킹된 질문)를 "중단할 것 없음"으로 오판한다.

        **`has_live_turn`이 그 계열의 세 번째 경우다(2026-08-19).** 소비자가
        사라진 채 생성 중인 턴은 `_turn_active`가 False이고 파킹된 질문도 없다 —
        위 두 조건만 보면 "중단할 것 없음"이 되어 아무것도 하지 않았다. `run()`이
        진행 중인 턴을 덮지 못하게 거부하기 시작한 지금 그것은 **막힘**이다:
        재접속해서 볼 수는 있지만 끊을 수가 없고, 새 메시지도 거부된다. 절전에서
        돌아온 사용자에게 유일한 탈출구가 이 경로다.
        """
        if self._client is None:
            return
        if (not self._turn_active and self._pending_question is None
                and not self.has_live_turn()):
            return
        if self._pending_question is not None and not self._pending_question.done():
            # 이 future를 기다리던 _on_can_use_tool은 턴과 함께 버려진다.
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        # 큐에 남은 questions 이벤트는 답할 수 없는 카드다 — 흘려보내면 화면에
        # 폼이 뜬다.
        self._queue = [e for e in self._queue if e.kind != "questions"]
        # 중단 사실을 남긴다. 새 kind를 만들지 않고 기존 status로 흘리는 이유는
        # 프론트가 이미 다루는 이벤트 모양을 재사용하기 위해서다 — 프론트는 이
        # 마커를 보고 그 턴에 "중단됨" 한 줄을 UI 언어로 그린다. 이 마커는
        # 라이브 SSE 큐에만 있다 — 트랜스크립트에 들어가지 않으므로 새로고침 후
        # 복원되지 않는다.
        self._queue.append(AgentEvent(kind="status", text=INTERRUPTED_MARKER))
        await self._client.interrupt()

    async def _on_pre_tool_use(self, input_data, tool_use_id, context) -> dict:
        """Discovery의 쓰기 범위 게이트. 판정은 agent/discovery_guard.py.

        왜 훅인가: Discovery는 bypassPermissions로 돌아 Write/Bash가 자동
        승인되고 can_use_tool에 도달하지 않는다 — SDK가 지정한 유일한 게이트가
        PreToolUse다(그 근거는 위 팩토리의 hooks 주석과 discovery_guard 헤더).

        **통과는 빈 dict다.** "allow"를 돌려주면 can_use_tool까지 건너뛰어
        AskUserQuestion 가로채기가 죽는다(types.py의 can_use_tool 설명).
        """
        name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        if name in _FILE_TOOLS:
            offender = write_denial(tool_input.get("file_path"), self._workspace)
            reason = (None if offender is None
                      else prompts.write_outside_docs(self._language, offender))
        elif name == "Bash":
            offender = bash_denial(tool_input.get("command"))
            reason = (None if offender is None
                      else prompts.build_command_refused(self._language, offender))
        else:
            # matcher가 위 셋만 걸지만, 훅 설정과 이 분기가 어긋나도 조용히
            # 통과해야 한다 — 알 수 없는 도구를 막으면 턴이 멈춘다.
            return {}
        if reason is None:
            return {}
        # 로그로 남긴다: 거부 이유는 모델에게만 가므로, 무엇이 막혔는지
        # 운영자가 확인할 경로가 따로 필요하다.
        _log.warning("discovery gate denied %s: %s", name, offender)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    def _file_question_round(self, rel: str) -> tuple[Any, str] | None:
        # 돌려주는 것 셋: None(해당 없음) / ("unparsed", 원문) / (QuestionFile, 원문).
        # 파싱 실패를 None과 구별하는 이유는 침묵이 곧 질문 소실이기 때문이다.
        """방금 써진 `rel`이 **물어야 할 질문 라운드**면 `(파싱 결과, 원문)`을 준다.

        원문을 함께 주는 이유: 호출부가 그 파일을 S3에 올려야 하고(카드를 광고하기
        전에 정본에 있어야 한다), 같은 파일을 두 번 읽을 이유가 없다.

        네 가지를 모두 만족해야 한다:

        1. **`looks_like_question_file`이 참이어야 한다** — 줄 맨 앞의 `[Answer]:`
           슬롯이 있고, 이름이 `NEVER_QUESTION_FILES`가 아니어야 한다.
           포함은 이름이 아니라 내용으로 판단한다: 상류는 자기 명명규칙
           (`{phase}-questions.md`)을 어긴 전례가 있다 — `design-context.md`에도
           질문을 넣었다(question_file_answers.py 헤더). 반대로 audit.md처럼 상류가
           **용도를 규정한** 파일은 이름으로 제외한다.

           2026-08-18까지 이 관문이 되기록과 갈라져 있었다(여기는
           `"[Answer]:" in md` 단순 포함, 저쪽은 `^` 앵커). 그래서 audit.md가
           태그를 기록하자 여기에만 걸렸고, 에이전트가 "audit.md는 질문 파일이
           아니므로 그 표기를 없애겠다"며 **자기 감사 기록을 훼손**했다 —
           `core-workflow.md:303`이 원문 그대로 남기라고 요구한 그 기록이다.
        2. `parse_ok`. 파싱이 안 되면 **조용히 지나간다.** 상류 포맷은 안정적이지
           않으므로(2026-08-17: 8파일 중 1개가 파서를 벗어났다) 여기서 막으면
           질문이 화면에 아예 뜨지 않는다 — 차단이 아니라 열화여야 한다.
        3. 답이 비어 있는 문항이 하나라도 있어야 한다. 에이전트가 답변을 되읽고
           파일을 다시 쓰는 것은 정상 동작이고, 그때 다시 물으면 안 된다.
        4. **같은 미답 문항 집합을 두 번 묻지 않는다.** 가드를 파일 단위가 아니라
           미답 문항 집합으로 두는 이유: 답변 뒤 문항이 추가되는 경우
           (`### Clarification Question 2`)는 물어야 한다.

        디스크에서 읽는다 — `Edit`/`MultiEdit`의 tool_input에는 전체 내용이 없고,
        PostToolUse는 쓰기가 끝난 뒤에 돈다.
        """
        from aipds.parsers.questions import parse_question_file
        try:
            md = (Path(self._workspace) / rel).read_text(encoding="utf-8")
        except OSError:
            return None
        if not looks_like_question_file(rel, md):
            return None
        qfile = parse_question_file(rel, md)
        if not qfile.parse_ok:
            # 조용히 넘기지 않는다 — AskUserQuestion이 거부되는 지금 이것은 질문의
            # 완전한 소실이다(prompts.file_questions_unparsed의 근거).
            return "unparsed", md
        unanswered = tuple(q.ask or q.text for q in qfile.questions
                           if not (q.answer or "").strip())
        if not unanswered:
            return None
        if self._asked_question_sets.get(rel) == unanswered:
            return None
        self._asked_question_sets[rel] = unanswered
        return qfile, md

    def _emit_stage_events(self) -> None:
        """`aiplc-state.md`를 읽어 상태가 바뀐 스테이지만 `stage`로 흘린다.

        커서(`self._stage_status`)를 갱신하는 유일한 자리다 — 훅 경로와 턴 경계
        재조정이 **같은 함수**를 지나므로 두 경로가 같은 diff를 본다. 두 벌로 두면
        한쪽이 흘린 것을 다른 쪽이 다시 흘린다.
        """
        events, self._stage_status = reconcile.stage_events(
            reconcile.read_state(Path(self._workspace)), self._stage_status)
        for ev in events:
            self._queue.append(ev)

    def _emit_document_events(self) -> None:
        """내용이 바뀐 산출물만 `document`로 흘린다. 옛 `submit_document`를 대체한다.

        `_emit_stage_events`와 같은 이유로 함수가 하나다 — 훅 경로와 턴 경계가 **같은
        커서**를 지나야 한다. 두 벌로 두면 턴 경계가 훅이 이미 알린 문서를 다시
        알리고, 갱신 배너가 문서마다 두 번 뜬다.
        """
        events, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)
        for ev in events:
            self._queue.append(ev)

    def _seed_document_cursor(self) -> None:
        """첫 턴이 시작할 때 **이미 있던** 문서를 "본 것"으로 표시한다.

        `document`는 워크스페이스 스캔에서 유도되므로 커서가 빈 채로 턴이 시작하면
        기존 문서가 전부 새 문서로 보인다. 커서를 드는 주체가 이 드라이버이므로 그
        상태는 **새 드라이버의 첫 턴**에서 온다 — 백엔드 재시작이나 재배포 뒤 첫
        attach가 그것이고, 그때 트리는 이미 채워져 있다(cold restore가 전부 내려받거나
        로컬 워크스페이스가 이전 턴에서 남아 있다). 문서 열 개짜리 프로젝트라면 열 번
        알리고, 갱신 배너는 방금 쓴 문서가 아니라 스캔 순서상 마지막 문서를 가리킨다.

        **한 번만 한다.** 매 턴 씨딩하면 턴 사이에 바뀐 문서를 조용히 삼킨다(다른
        인스턴스의 쓰기, 복원된 내용이 다른 경우). 첫 턴 이후로는 모든 변경이 알림
        대상이다.

        **`__init__`이 아니라 여기인 이유**도 그 복원이다: 드라이버가 만들어지는
        시점의 워크스페이스는 비어 있고, 채워지는 것은 러너가 `run()`을 부르기
        직전이다. 생성 시점에 씨딩하면 아무것도 못 보고, 그러면 첫 턴이 다시 전부를
        알린다.
        """
        if self._docs_seeded:
            return
        self._docs_seeded = True
        _, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)

    def _reconcile_turn(self) -> None:
        """턴이 끝나기 전에 워크스페이스와 UI를 맞춘다. `_pump`가 부른다.

        훅이 못 본 변경(Bash 경유)과 놓친 이벤트(배치 드롭)를 여기서 되찾는다.
        인계는 알리기만 하고 **턴을 끊지 않는다** — 턴은 이미 끝나는 중이고,
        여기서 `continue_: False`를 낼 자리도 없다.

        예외를 삼킨다. 재조정은 백스톱이므로 그것이 턴을 실패시키면 백스톱이
        아니라 새 실패 원인이 된다(runner._sync_abandoned_turn이 같은 판단이다).
        """
        try:
            self._emit_stage_events()
        except Exception:
            _log.exception("stage reconciliation failed")
        try:
            events, self._handed_off = reconcile.prototype_events(
                Path(self._workspace), self._handed_off)
            for ev in events:
                self._queue.append(ev)
        except Exception:
            _log.exception("prototype handoff reconciliation failed")
        try:
            self._emit_document_events()
        except Exception:
            _log.exception("document reconciliation failed")

    def _handoff_stop(self, rel: str) -> dict | None:
        """`build-instructions.md` 쓰기를 인계로 확정하고 턴을 끝낸다.

        `None`이면 확정하지 않았다는 뜻이고, 그때는 턴이 계속된다. 확정하지 않는
        경우는 명세가 아직 없을 때다 — Prototypes 탭은 명세에서 카드를 만들므로
        (`routes/prototypes.py`가 `layout.discover`로 목록을 만든다) 빌드 지시만
        있으면 사용자가 빈 탭을 본다. 옛 `handoff_prototype`이 명세 존재를 확인한
        이유가 그것이고, 그 검사는 `reconcile.handed_off`로 옮겨 갔다.

        **턴을 끝내는 이유는 질문 파일과 같다.** 여기서 Discovery의 일이 끝나고
        다음 행동은 사용자의 것이다(Prototypes 탭에서 빌드). 끝내지 않으면 상류
        Step 4(Iterate)로 계속 가거나 자격증명을 묻는다 — 둘 다 실측된 실패다.
        그래서 `stopReason`이 다음 행동을 **지정한다**: 이유만 주면 모델이
        즉흥한다는 것이 prompts.py 헤더의 원칙이다.
        """
        events, cursor = reconcile.prototype_events(
            Path(self._workspace), self._handed_off)
        if not events:
            # 이미 알린 인계라면 조용히 지나간다(빌드 지시를 고쳐 쓴 경우) — 카드가
            # 두 장 뜨지 않고, 턴도 다시 끊지 않는다.
            if reconcile.prototype_id_for(rel) not in self._handed_off:
                _log.warning("build instructions at %s have no prototype spec "
                             "yet — not handing off", rel)
            return None
        self._handed_off = cursor
        for ev in events:
            self._queue.append(ev)
        slug = reconcile.prototype_id_for(rel) or ""
        _log.info("prototype handed off from %s (slug=%s)", rel, slug)
        return {"continue_": False,
                "stopReason": prompts.prototype_handoff_stop(self._language, slug)}

    async def _on_post_tool_use(self, input_data, tool_use_id, context) -> dict:
        name = input_data.get("tool_name", "")
        if name not in _FILE_TOOLS:
            return {}
        fp = (input_data.get("tool_input") or {}).get("file_path", "")
        rel = _rel(fp, self._workspace)
        if rel is None:
            self._queue.append(AgentEvent(
                kind="status", text="file outside workspace ignored"))
            return {}
        # **광고하기 전에 게시한다.** `file_changed`를 받은 UI는 곧바로 그 문서를
        # 읽으러 오는데(WorkspaceDocPanel), 읽기 경로는 전부 정본(S3)이다. 정본
        # 게시를 턴 종료까지 미루면 "작성됐다는데 목록에 없다 / 골라도 내용이
        # 없다 / 잠깐 보이다 사라진다"가 된다 — 2026-08-18에 실제로 그랬고,
        # 실측한 S3 타임스탬프 16개가 전부 턴 끝 1초 안이었다.
        #
        # 실패해도 턴을 죽이지 않는다(publish_file이 삼킨다). 턴 종료 배치 sync가
        # 여전히 백스톱이다.
        await publish_file(
            self._s3,
            Path(self._workspace),
            rel,
            on_published=self._on_file_published,
        )
        # 질문 파일도 산출물이다 — 문서 패널이 이 이벤트로 갱신되므로 아래 분기와
        # 무관하게 먼저 흘린다.
        self._queue.append(AgentEvent(kind="file_changed", path=rel))
        # 스테이지 배지: 상태 파일을 **파싱해서** 유도한다(옛 `report_stage` 도구를
        # 대체한다 — agent/reconcile.py 헤더에 그 전말이 있다). 상류 룰이 에이전트에게
        # 이 파일을 직접 갱신하라고 요구하므로(common/workflow-changes.md,
        # discovery/prototype-validation.md Step 10) 신호는 이미 워크스페이스에 있다.
        #
        # 훅 페이로드가 아니라 **디스크**를 읽는다. Edit는 패치만 담으므로 페이로드로는
        # 파일 전문을 알 수 없고, `_file_question_round`가 같은 이유로 같은 선택을 했다.
        if rel == reconcile.STATE_KEY:
            self._emit_stage_events()
        # 문서 갱신 배너와 문서 패널의 activeDoc: 산출물 쓰기에서 **유도한다**(옛
        # `submit_document` 도구를 대체한다 — 그 도구는 매 문서마다 부르라고 지시받고도
        # 대부분 불리지 않았다. 프론트가 실측을 적어 뒀다:
        # useWorkspaceStream.ts:177 "대부분의 문서를 submit_document 없이
        # file_write로만 만든다"). 스테이지·인계와 같은 부류의 침묵이었다.
        if reconcile.is_document(rel):
            self._emit_document_events()
        # 프로토타입 인계: Step 3의 마지막 산출물이 쓰이는 순간이다. 옛
        # `handoff_prototype` 도구와 달리 모델이 잊을 수 없다 — 2026-08-17
        # keumkang-v5에서 탭 안내가 0회였던 것이 그 도구가 생긴 이유였고, 도구는
        # "부르지 않으면 침묵"이라는 같은 실패를 한 단계 뒤로 미룬 것이었다.
        if reconcile.prototype_id_for(rel) is not None:
            stop = self._handoff_stop(rel)
            if stop is not None:
                return stop
        if not _file_questions_enabled():
            return {}
        round_ = self._file_question_round(rel)
        if round_ is None:
            return {}
        qfile, md = round_
        if qfile == "unparsed":
            # 같은 내용에 같은 노트를 반복하지 않는다(턴 안 무한 왕복 방지). 내용이
            # **달라지면** 다시 알린다 — 고쳐 썼는데 여전히 틀린 경우가 그것이고,
            # 그때 침묵하면 다시 질문이 사라진다.
            if self._unparsed_noted.get(rel) == md:
                _log.warning("file questions: %s still does not parse (already "
                             "reported this content)", rel)
                return {}
            self._unparsed_noted[rel] = md
            _log.warning("file questions: %s has [Answer] tags but no parsable "
                         "question — telling the agent", rel)
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": prompts.file_questions_unparsed(
                    self._language, rel)}}
        # `interrupt_id`는 빈 문자열이다: 파킹된 can_use_tool future가 없으므로
        # 되돌아올 곳이 턴이 아니라 **파일**이다. 프론트는 `file`로 그 차이를
        # 판별해 답변을 PUT /projects/{pid}/questions/{name}으로 보낸다.
        self._queue.append(AgentEvent(kind="questions", payload=json.dumps(
            {"interrupt_id": "", "file": rel, "questions": qfile},
            ensure_ascii=False, default=lambda o: o.model_dump())))
        # **파일을 먼저 S3에 올린다 — 턴 종료 sync를 기다리지 않는다.**
        #
        # 2026-08-17 실측한 실패: 실제 턴에서 카드는 떴는데 `GET /pending`이
        # `file=None`을, 답변 제출이 404를 돌려줬다. 훅은 로컬 파일을 읽는데 그
        # 파일이 S3(정본)에 올라가는 것은 러너의 done/error sync 시점이다. 그 사이가
        # 창이고, 그 창에서 `pending()`은 마커가 가리키는 파일을 못 찾고 답변 제출은
        # `runner.read_file`이 S3를 읽으므로 404가 된다.
        #
        # 카드를 광고하는 순간 그 파일은 이미 정본에 있어야 한다. 내용을 이미 손에
        # 들고 있으므로 여기서 올리는 것이 가장 싸고 확실하다(라운드당 put 하나).
        # 러너의 sync가 나중에 같은 내용을 다시 올리는 것은 무해하다.
        try:
            # 파일은 위에서 이미 게시됐다(publish_file) — 모든 산출물이 같은 경로를
            # 지나므로 질문 파일 전용 업로드는 남기지 않는다. 마커는 그 **다음**이다:
            # 순서가 뒤집히면 마커가 정본에 없는 파일을 가리키는 창이 남는다.
            await save_pending_file(self._s3, file=rel)
        except Exception:
            # 라이브 카드는 이미 큐에 있다 — 실패해도 이 턴의 질문은 화면에 뜨고,
            # 새로고침 복원과 답변 제출만 못 된다. 턴을 죽이는 것이 더 나쁘다.
            _log.exception("publishing the open question file failed")
        # 훅은 사람을 기다리지 않는다 — 즉시 턴만 멈춘다. 실측(2026-08-17):
        # `continue_: False`는 `terminal_reason='hook_stopped'` + `is_error=False`로
        # 끝나므로 `_translate`가 이미 정상 `done`으로 처리하고, 같은 메시지에
        # 배치로 온 뒤 도구 호출들은 실행되지 않는다(그것이 의도다 — 모델이
        # AskUserQuestion으로 같은 질문을 다시 만들지 못한다).
        _log.info("file questions: asking %d question(s) from %s",
                  len(qfile.questions), rel)
        return {"continue_": False,
                "stopReason": prompts.file_questions_stop(self._language, rel)}

    def _answer_to_sdk(self, value: str, sdk_options: list[dict]) -> str:
        """QuestionForm answer value → SDK label(s). Accepted forms:
        "A" | "A,C" | "A: note" | free text (unmatched passes through)."""
        def label(letter: str) -> str | None:
            idx = _LETTERS.find(letter.strip())
            if 0 <= idx < len(sdk_options):
                return sdk_options[idx].get("label", "")
            return None
        if ":" in value:
            head, _, note = value.partition(":")
            l = label(head)
            if l is not None:
                return f"{l}:{note}"
        parts = [label(p) for p in value.split(",")]
        if parts and all(p is not None for p in parts):
            return ", ".join(parts)
        return value  # free text (Other)

    # ---- the question round trip ----

    async def _on_can_use_tool(self, tool_name, input_data, context):
        """AskUserQuestion → `questions` event, then park until answered.

        The SDK dispatches this on its own task while the CLI blocks waiting
        for the permission response, so `receive_response()` yields nothing
        for as long as we stay suspended here -- which is exactly what
        `_pump`'s queue poll exists to cope with.
        """
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)
        # 파일 경로가 켜져 있으면 이 도구는 쓰지 않는다. **거부**이지 삭제가
        # 아니다 — 가로채기만 없애면 모델이 도구를 부른 순간 질문이 조용히
        # 사라진다(화면에도 채팅에도 없다). 거부는 대체 행동을 함께 준다.
        #
        # 스위치가 하나인 이유: 두 경로가 동시에 살아 있으면 에이전트가 파일을
        # 쓰고(훅이 카드를 띄우고) 이어서 이 도구까지 불러 같은 질문이 두 번 뜬다.
        if _file_questions_enabled():
            _log.info("AskUserQuestion denied — file questions are the only path")
            return PermissionResultDeny(
                message=prompts.ask_user_question_denied(self._language))
        import uuid
        # 문자열로 온 payload를 여기서 리스트로 편다 — 정규화 없이 넘기면
        # question_file_from_sdk가 문자열을 문자 단위로 훑다가 AttributeError로
        # 터지고, 그 예외는 이 콜백 밖으로 새어 턴을 죽인다. 관측된 거절
        # 3건은 CLI가 이 콜백 **전에** 막은 것이라 여기서 살아나지 않는다
        # (normalize_sdk_questions의 docstring에 근거를 적었다).
        sdk_questions = normalize_sdk_questions(input_data.get("questions"))
        # 복원 조인 키. SDK가 이 콜백에 tool_use_id를 주고(비어 있지 않음이 와이어
        # 프로토콜 보장) 트랜스크립트의 tool_result도 같은 id를 들고 있으므로,
        # 답변 레코드를 이것으로 키하면 복원이 순서·타임스탬프 추측 없이 정확히
        # 조인된다(agent/answer_store.py의 헤더 참조). getattr로 읽는 것은 이
        # 콜백을 직접 부르는 테스트 더블이 컨텍스트를 축약해 넘길 수 있기 때문이다.
        tool_use_id = getattr(context, "tool_use_id", None) or ""
        # question_file_from_sdk raises ValueError on unusable input (e.g. a
        # question with zero options) -- deny with a message the model can
        # read and retry from, instead of letting the exception escape.
        # PermissionResultDeny is the SDK-native way to hand the model an
        # explanation; the can_use_tool contract only speaks PermissionResult,
        # so no other shape is invented here.
        try:
            qfile = question_file_from_sdk(sdk_questions, name="discovery-questions")
        except ValueError as e:
            _log.warning("AskUserQuestion payload rejected: %s", e)
            return PermissionResultDeny(
                message=prompts.question_payload_rejected(self._language, str(e)))
        iid = uuid.uuid4().hex
        payload = json.dumps({"interrupt_id": iid, "questions": qfile},
                             ensure_ascii=False)
        self._pending_payload = payload
        self._pending_iid = iid
        # sdk_questions is NOT stashed on self: this coroutine keeps it in its
        # own frame across the await, and the restart path reads it back from
        # S3 instead. A second copy on the instance would be one more thing to
        # keep in sync with _clear_pending_state.
        loop = asyncio.get_running_loop()
        self._pending_question = loop.create_future()
        await self._save_pending_quietly(iid, qfile, sdk_questions)
        self._queue.append(AgentEvent(kind="questions", payload=payload))
        try:
            answers = await self._pending_question  # stays open until answered
        except asyncio.CancelledError:
            # Nothing in Discovery cancels this deliberately (there is no
            # interrupt route), so a cancellation here means the turn is being
            # torn down around us -- leaving _pending_payload set would make
            # pending() advertise a question no future is listening on.
            self._clear_pending_state()
            raise
        try:
            # "number -> letter/text" (our contract) → "question text ->
            # label" (SDK contract).
            sdk_answers = {}
            for k, v in answers.items():
                try:
                    q = sdk_questions[int(k) - 1]
                except (ValueError, IndexError):
                    continue
                sdk_answers[q.get("question", "")] = self._answer_to_sdk(
                    v, q.get("options", []))
        finally:
            self._clear_pending_state()
        # 복원용 레코드. **updated_input을 돌려주기 전에** 쓴다 — 이 반환으로
        # 턴이 재개되고 곧 다음 도구가 돌기 시작하므로, 뒤에 두면 실패했을 때
        # 어느 라운드의 기록이 빠졌는지 로그로도 짚기 어려워진다.
        await self._save_answers_quietly(tool_use_id, iid, qfile, answers)
        # 답변을 질문 파일의 `[Answer]:` 칸에도 심는다. ai-plc 워크플로우가 그
        # 칸을 읽기 때문이다 — 근거와 텍스트 매칭의 이유는
        # agent/question_file_answers.py 헤더에 있다. 여기(반환 직전)인 이유는
        # _save_answers_quietly와 같다: 이 반환으로 턴이 재개되고 다음 도구가
        # 곧 돌기 시작하므로, 뒤에 두면 파일이 다음 스테이지의 읽기보다 늦을 수
        # 있다. record_answers는 어떤 실패도 삼키고 빈 목록을 준다.
        for rel in record_answers(self._workspace, sdk_questions, answers):
            await self._mirror_question_file_quietly(rel)
            self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

    async def _mirror_question_file_quietly(self, rel: str) -> None:
        """되기록한 질문 파일을 S3에도 올린다. 턴을 죽이지 않는다.

        **왜 필요한가.** record_answers는 로컬 워크스페이스 파일에 쓰는데,
        `runner.read_file`은 S3에서 읽는다(runner.py:55) — 화면의 산출물 패널과
        다음 스테이지가 보는 것이 그쪽이다. 로컬만 쓰면 답변은 턴이 끝나
        `_sync_workspace_to_s3`가 돌 때까지 **보이지 않는다.**

        그리고 그 지연은 유실 창이기도 하다: `_restore_workspace_from_s3`가 매 턴
        시작에 돌고 그 주석이 "S3가 무조건 이긴다"다(runner.py:79). 턴이 종결
        이벤트 없이 버려지면(`_sync_abandoned_turn`은 베스트에포트다) 다음 턴이
        S3의 빈 파일로 로컬을 덮어 답변이 사라진다.

        로컬 파일을 되읽어 올리는 이유: 디스크에 있는 것과 정확히 같은 바이트를
        올린다. 메모리의 사본을 따로 들고 다니면 둘이 어긋날 수 있다.
        """
        try:
            content = (Path(self._workspace) / rel).read_text(encoding="utf-8")
            await self._s3.put(rel, content)
        except Exception:
            # 로컬 파일은 이미 갱신됐고 턴 종료 sync가 두 번째 기회를 준다 —
            # 여기서 턴을 죽이면 방금 제출한 답변이 사라진다.
            _log.exception("question-file S3 mirror failed: %s", rel)

    def _clear_pending_state(self) -> None:
        self._pending_payload = None
        self._pending_question = None
        self._pending_iid = None

    async def _save_pending_quietly(self, iid: str, qfile: dict,
                                    sdk_questions: list) -> None:
        """Mirror the pending question to S3. Never fails the turn.

        Persistence is restore convenience -- losing an in-flight question
        over an S3 hiccup is the bigger loss (the same judgment
        runner._sync_abandoned_turn makes about its own best-effort sync).
        """
        session_id = self._current_session_id or ""
        if not session_id:
            # load_pending validates session_id as a non-empty string, so a
            # record written without one is unreadable -- writing it anyway
            # would only leave junk that masks nothing. In-memory pending()
            # still covers the same-process refresh.
            _log.warning("no session id for pending-question persist — skipped")
            return
        try:
            await save_pending(self._s3, interrupt_id=iid, questions=qfile,
                               sdk_questions=sdk_questions, session_id=session_id)
        except Exception:
            _log.exception("pending-question S3 persist failed")

    async def _save_answers_quietly(self, tool_use_id: str, iid: str,
                                    qfile: dict, answers: dict) -> None:
        """제출된 답변을 S3에 기록한다. 절대 턴을 실패시키지 않는다.

        pending 미러와 같은 판단이다 — 이 레코드는 복원 편의이고, S3 딸꾹질
        때문에 사용자가 방금 답한 턴이 죽는 것이 더 나쁘다. 레코드가 없으면
        복원은 구 세션과 같은 경로(CLI 산문 문구)로 떨어진다.

        tool_use_id가 없으면 건너뛴다: 그 값이 복원 조인 키이므로, 없이 쓴
        레코드는 어느 라운드의 것인지 알 수 없어 읽는 쪽이 쓸 수 없다.
        """
        if not tool_use_id:
            _log.warning("no tool_use_id for answer record — skipped")
            return
        try:
            await save_answers(self._s3, tool_use_id=tool_use_id,
                               interrupt_id=iid, questions=qfile,
                               answers={str(k): str(v) for k, v in answers.items()})
        except Exception:
            _log.exception("answer record S3 persist failed")

    async def _clear_pending_quietly(self) -> None:
        try:
            await clear_pending(self._s3)
        except Exception:
            _log.exception("pending-question S3 clear failed")

    # ---- message translation + the turn pump ----

    def _translate(self, msg, reader: "_MessageReader | None" = None) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        if tname == "SystemMessage" and getattr(msg, "subtype", "") == "mirror_error":
            # A transcript batch failed to reach S3. The SDK does NOT retry it
            # (at-most-once), so this message is the only signal there is -- and
            # we were dropping it whole. That is what made the empty-history bug
            # so slow to pin down: with no log either way, "the write failed"
            # and "the write was never attempted" look identical from outside,
            # and it turned out to be the second one.
            #
            # Logged, not surfaced: durable history is secondary data, and there
            # is nothing a workshop attendee can do about an S3 error mid-turn.
            _log.warning("transcript mirror failed (history for this turn may "
                         "be lost): %s", getattr(msg, "error", "unknown"))
            return events
        if tname == "AssistantMessage":
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    # 무엇을 했는지까지 보낸다 — `Read`만 뜨면 트레이스의 요점이
                    # 빠진다(tool_trace 모듈 헤더). 값만 보내고 `🔍 Read · …`의
                    # 아이콘·구분자는 프론트가 UI 언어로 그린다.
                    detail = tool_detail(block.name, getattr(block, "input", None))
                    # 중복 접기 키에 detail을 넣는다. 이름만으로 접으면 연속된
                    # Read 세 번이 `Read` 한 줄로 뭉개진다 — 파일이 달라도 그렇다.
                    key = (block.name, detail)
                    if key != self._last_status:
                        self._last_status = key
                        events.append(AgentEvent(
                            kind="status", text=block.name,
                            payload=(json.dumps({"detail": detail},
                                                ensure_ascii=False)
                                     if detail else None)))
        elif tname == "ResultMessage":
            # The CLI reports turn failure HERE, not by raising: `is_error`
            # true means the turn ran but ended in failure (a Bedrock 429/500/
            # 529, a wedged tool, an aborted stream). Ignoring it is what let
            # a failed PR/FAQ turn render as a normal answer -- the CLI writes
            # its own "API Error: ..." prose into an AssistantMessage, which
            # `message` above relays as ordinary text, and then this branch
            # said `done`. The frontend closes the stream on `done`
            # (sse.ts:29) and never takes its error branch, so the user saw
            # English error prose glued to Korean output and the agent
            # retrying the same step -- with nothing in our logs at all,
            # because every field describing the failure was dropped right
            # here.
            #
            # `api_error_status` is the field worth logging: it distinguishes a
            # transient 429/529 (same request succeeds on retry) from a 500
            # (does not). Logged rather than shown -- an HTTP status is not
            # something a workshop attendee can act on.
            if getattr(msg, "is_error", False):
                # `is_error` alone conflates two different things. The CLI sets
                # it for a genuine failure (Bedrock 429/500/529, a wedged tool)
                # AND for a turn the user cancelled via our own interrupt() --
                # `terminal_reason` is what tells them apart
                # (claude_agent_sdk/types.py:1249-1257 documents
                # "aborted_streaming"/"aborted_tools" as the cancelled-turn
                # values). Without this check, pressing the interrupt button
                # showed "중단됨" (Task 5's status line, correct) stacked with
                # "이번 턴이 실패했습니다" (a lie) -- the real-CLI probe that
                # unit tests missed, because the fake SDK never scripted
                # is_error=True together with an aborted terminal_reason.
                #
                # `terminal_reason is None` (older CLIs that predate this
                # field) falls through to the failure path below, same as
                # before -- `is_error` is the only signal we have then.
                terminal_reason = getattr(msg, "terminal_reason", None)
                interrupted = terminal_reason in _INTERRUPTED_TERMINAL_REASONS
                if not interrupted:
                    _log.error(
                        "claude CLI reported a failed turn: api_error_status=%s "
                        "subtype=%s terminal_reason=%s errors=%s",
                        getattr(msg, "api_error_status", None),
                        getattr(msg, "subtype", None),
                        terminal_reason,
                        getattr(msg, "errors", None),
                    )
                    # Recorded on the reader, NOT returned as an `error` event.
                    # `_pump` owns the terminal event and emits exactly one, always
                    # last (its invariant 1); returning a second terminal here
                    # would break that. The flag makes `_pump` emit `error`
                    # instead of `done` after its drain.
                    #
                    # On the READER rather than on `self` because this is per-turn
                    # state: the reader's lifetime IS the turn's, so a failed turn
                    # cannot leak its verdict into the next one. A driver-level
                    # flag would have to be reset by hand on every entry path
                    # (`_stream`, `_continue_after_answers`) and would be wrong the
                    # moment one of them forgot.
                    if reader is not None:
                        reader.failed = True
                # 중단은 error 로그로 남기지 않는다: 사용자가 방금 누른 버튼의
                # 정상적인 결과이지 우리가 찾아야 할 실패가 아니다. error로
                # 남기면 워크숍 로그가 매 중단마다 오염되고, 진짜 실패(429/500/
                # 529, 교착된 도구)를 찾을 때 잡음이 늘어난다.
            events.append(AgentEvent(kind="done"))
        return events

    def _retire_reader(self) -> None:
        """Drop the reader owning the PREVIOUS turn's iterator.

        Called only where the old turn is being replaced (a new `query()`) or
        the subprocess is going away (`disconnect`) -- never while the turn it
        belongs to may still continue, because cancelling the reader is the one
        remaining way to lose a message that was handed to it. When a question
        is parked the reader is deliberately left running: it is what collects
        the rest of the turn for `run_answers` to relay.
        """
        reader, self._reader = self._reader, None
        if reader is not None and not reader.task.done():
            reader.task.cancel()

    async def _pump(self, reader: "_MessageReader") -> AsyncIterator[AgentEvent]:
        """Relay one turn: the reader's messages, raced against the callback queue.

        Two sources feed a turn, and only one of them can be awaited. The
        hook/tool-callback queue (`self._queue`) is filled synchronously by
        callbacks running on the SDK's own tasks and has nothing to await, so it
        is POLLED on `_POLL_SECONDS`. That poll is not an optimization: while an
        AskUserQuestion is parked the CLI is blocked on the permission response
        and the message stream yields nothing at all, so a driver that only
        awaited messages would never let the queued `questions` event reach the
        SSE stream (this is builder.py's hard-won detail, carried across).

        Messages come from `reader.inbox`, never from an awaited receive. That
        is THE structural property of this function, and the reason it no longer
        needs to reason about interleavings: `_pump` owns no cancellable
        receive, so no suspension of `_pump` -- not a `yield`, not an `await`,
        not the `GeneratorExit` of an abandoned generator -- can destroy a
        message. See `_MessageReader` for the full argument, including why the
        peek-future shape it replaces had no safe suspension protocol and cost
        three review rounds one lost-message defect each.

        Where this DIVERGES from builder.py: a queued `questions` event ends the
        turn (`questions`, then `done`, then return), because Discovery answers
        arrive on a separate stream -- see the module docstring. The suspended
        can_use_tool callback is left parked on its future, and the READER IS
        LEFT RUNNING, for `run_answers` to relay through `_continue_after_answers`.

        Two invariants this function owns, both verified by tests:

        1. Exactly one terminal event, always LAST. `_translate`'s `done` (from
           a ResultMessage) is never yielded; it only sets `ended`, so the
           terminal event has a single origin and cannot be doubled.
           `frontend/lib/api/sse.ts:29` closes the EventSource on `done`, so
           anything after it never reaches `onEvent` -- a `stage`/`document`
           emitted by a tool during the same ready burst would be silently
           dropped and the sidebar/document panel would go stale. Hence the
           terminal harvest below drains BOTH sources to exhaustion before
           `done`.
        2. No event this driver has produced is dropped. Both invariants rest on
           ONE ownership rule, and it is checkable by reading rather than by
           reasoning about interleavings:

               An event is in exactly one place that outlives this generator,
               and it leaves that place only AFTER the consumer has received it.

           The places are `reader.outbox` (translated messages) and
           `self._queue` (tool/hook events). `_relay` below is the only code
           that yields, and it is the only code that removes an item -- popping
           after the `yield` returns, never before. So an item is either
           already delivered or still owned; `GeneratorExit` at any yield leaves
           the remainder owned, and the next pump over the same reader relays
           it.

           This is the rule the previous round got wrong. It moved message
           ownership out of anyio's buffer and into the driver (which is what
           fixed the abandoned-receive class), but then batch-popped the whole
           inbox into a LOCAL LIST before yielding any of it -- so the
           not-yet-yielded remainder lived in this generator's frame, which
           `GeneratorExit` destroys. Reproduced through the real AgentRunner: a
           3-message burst, consumer abandons after the first, 2 lost. The batch
           pop is exactly what made this invariant false, so no batch pop.
        """
        asked = False
        ended = False
        # 이 pump를 시작한 홀더의 신원. 재접속이 슬롯을 선점하면
        # (`_acquire_turn(preempt=True)`) 이 값은 더 이상 홀더가 아니고, 아래
        # 검사가 그것을 **실제 중단**으로 옮긴다.
        #
        # 토큰만 바꾸고 이 루프를 두면 부족하다: 옛 소비자의 제너레이터는 `yield`에
        # 멈춰 있을 뿐 죽지 않았으므로, 클라이언트의 TCP가 되살아나 다시 읽으면 두
        # 소비자가 같은 `outbox`를 나눠 읽는다. 실측(테스트): 선점 후 옛 소비자가
        # `문장 2`·`questions`·`done`을 가져가 재접속한 화면에서 사라졌다.
        #
        # **검사는 `yield` 앞이다.** 위 소유권 규칙("항목은 소비자가 받은 뒤에만
        # 자기 자리를 떠난다")이 그대로 유지되어야 하므로, 여기서 멈추면 남은
        # 항목은 pop되지 않고 `outbox`/`_queue`에 소유된 채 남아 다음 pump가
        # relay한다 — 즉 선점은 프레임을 버리지 않고 **넘긴다.**
        pump_token = self._turn_token

        def owns_turn() -> bool:
            return self._turn_token is pump_token

        def translate_into_outbox() -> None:
            """Move messages inbox -> outbox, translating. Never yields.

            Wholly synchronous, so a message is never in neither place: it
            leaves `inbox` and enters `outbox` with no suspension in between.
            `outbox` lives on the reader, so anything still there when this
            generator dies belongs to the next pump over the same reader.
            """
            nonlocal ended
            while reader.inbox and not ended:
                for ev in self._translate(reader.inbox.pop(0), reader):
                    if ev.kind == "done":
                        ended = True   # terminal event comes from the exit below
                        continue
                    reader.outbox.append(ev)

        async def relay():
            """Yield from both owned queues, popping only after delivery.

            `queue[0]` then `pop(0)` -- not `pop(0)` then `yield` -- is the whole
            fix. If the consumer abandons us at the `yield`, the event is still
            at the head of its owned queue.
            """
            nonlocal asked
            for queue in (reader.outbox, self._queue):
                while queue:
                    if not owns_turn():
                        return          # 선점됨 — 남은 항목은 소유된 채 넘긴다
                    ev = queue[0]
                    yield ev
                    # Reached only if the consumer came back for the next item,
                    # i.e. it really received this one.
                    if queue and queue[0] is ev:
                        queue.pop(0)
                    asked = asked or ev.kind == "questions"

        while True:
            if not owns_turn():
                return                  # 선점됨 — 위 pump_token 주석 참조
            # The only cancellable wait in this function, and it waits on an
            # Event -- which carries no payload, so a cancellation here cannot
            # lose anything.
            await reader.settle(_POLL_SECONDS)
            translate_into_outbox()
            async for ev in relay():
                yield ev
            if reader.ended and not reader.inbox:
                ended = True
            if asked or ended:
                break
        if reader.error is not None:
            # Surfaced on the caller's stack so `_stream` can degrade it to the
            # contract's "agent turn failed" -- messages already relayed above
            # have reached the consumer first.
            raise reader.error
        # 턴 경계 재조정 — 훅이 놓친 것을 워크스페이스에서 되찾는다.
        #
        # **왜 훅만으로는 부족한가.** PostToolUse는 `Write|Edit|MultiEdit`에만 붙으므로
        # 에이전트가 Bash로 파일을 고치면(`python3 -c`, `sed`, 리다이렉션) 훅이 그것을
        # 보지 못한다. discovery_guard.py 헤더가 같은 한계를 이미 기록해 뒀다 — Bash는
        # 임의 코드 실행이라 거부목록으로 모든 경로를 덮을 수 없고, matcher에 Bash를
        # 넣어도 명령에서 대상 파일을 알아낼 수 없다.
        #
        # 그래서 매 순간의 선언(도구)도, 매 쓰기의 관측(훅)도 아니라 **경계의 정합성**이
        # 마지막 근거다. 여기서 디스크를 한 번 읽으면 Bash 우회·훅 유실·배치 드롭이
        # 전부 덮인다. 2026-08-18 test123456의 유실된 `report_stage`도 여기서 잡힌다.
        #
        # **종결 배출 앞이라는 위치가 요점이다.** `frontend/lib/api/sse.ts:29`가 `done`에서
        # EventSource를 닫으므로 그 뒤의 이벤트는 화면에 닿지 않는다(위 invariant 1).
        # 여기서 큐에 넣으면 아래 드레인 루프가 그것을 종결 이벤트 앞에 배출한다.
        #
        # 이미 흘린 것은 다시 흘리지 않는다 — 두 커서(`_stage_status`, `_handed_off`)가
        # 훅 경로와 공유되므로 재조정은 대개 아무 일도 하지 않는다. 그것이 정상이다.
        #
        # `asked`(질문으로 끝난 턴)에서도 돈다. 그 경로가 정확히 유실이 관측된
        # 경로다: 질문 파일 쓰기가 턴을 끊으면서 같이 배치된 호출이 사라졌다.
        self._reconcile_turn()
        # Terminal relay. Every `yield` above handed control to the scheduler,
        # which is exactly when the reader can append another message and a tool
        # callback can queue another event -- so drain both to exhaustion before
        # the terminal event, or an event that arrived during those yields would
        # land after `done` (invariant 1) or wait for the next turn (invariant 2,
        # correct but needlessly late). Terminates because both sources are now
        # finished: after `asked` the CLI is blocked on the permission response,
        # and after `ended` the iterator is done.
        while True:
            translate_into_outbox()
            if not (reader.outbox or self._queue):
                break
            async for ev in relay():
                yield ev
        # Still exactly one terminal event, still last -- only its KIND now
        # depends on whether the CLI reported the turn as failed
        # (`_translate`'s ResultMessage branch). `error` rather than a raise
        # because the turn is over and everything it produced has already been
        # relayed: raising here would send the caller down `_stream`'s except
        # arm, which relays the queue a second time.
        #
        # Deliberately NOT reset here. A failed turn's verdict dies with the
        # reader, and `_continue_after_answers` pumps the SAME reader -- so if
        # the question turn already failed, its continuation stays failed
        # rather than reporting success.
        # Push this turn's transcript to S3 before the terminal event, because
        # for the caller `done` means the turn is over -- the SSE response
        # closes and nothing else will run on our behalf.
        #
        # The SDK's batched mode normally flushes on `result` or `close()`, and
        # a question turn reaches neither (`asked` leaves the CLI parked while
        # the client stays cached). This explicit boundary keeps batching from
        # weakening durability.
        reader.transcript_flushed = await self._flush_transcript_mirror()
        if reader.failed:
            yield AgentEvent(kind="error",
                             text=prompts.turn_failed(self._language))
        else:
            yield AgentEvent(kind="done")

    # ---- the single-turn slot ----
    #
    # Ownership lives in `run`/`run_answers` -- the two contract entry points,
    # which are always the OUTERMOST generator -- and nowhere else. That is not
    # stylistic: `aclose()` runs only the outermost generator's `finally`
    # synchronously, and a `finally` on a nested generator (`_stream` inside
    # `run`) needs GeneratorExit to propagate down, which takes extra event-loop
    # ticks. Measured: with the flag released in `_stream`, `_turn_active` was
    # still True immediately after `await agen.aclose()` and only cleared after
    # two bare `await asyncio.sleep(0)`.
    #
    # Why that mattered: runner.py:144-152 routinely abandons this generator
    # (SSE disconnect, proxy timeout, user navigating away) and clears its own
    # `_turn_active` synchronously in the same `finally`. So a browser that
    # reconnects on the very next tick passed runner's guard and was then
    # rejected by ours with "turn already in progress" -- the user's retry
    # bounced off a turn that no longer existed.

    def _acquire_turn(self, *, preempt: bool = False) -> object | None:
        """Claim the turn slot; None if one is already running. The token
        identifies THIS turn so a rejected caller cannot release the slot the
        live turn is holding.

        `preempt=True` takes the slot regardless and **never returns None**. Only
        the reattach path uses it, and the policy it implements is "the request
        that just arrived wins": a person watches one screen at a time, so a new
        connection is the real user and whatever held the slot is presumed
        stale.

        This is what makes reattach possible at all. The flag says "a consumer
        exists" (see `has_live_turn`), and a suspended laptop never sends a FIN
        -- so the generator of the consumer that went away is still alive at its
        `yield`, still holding the slot, exactly when reattach is needed. Before
        preemption the reattach request was refused with "turn already in
        progress" (measured on the deployed instance 2026-08-19, where that text
        reached the user as agent speech).

        Issuing a NEW token is the whole eviction mechanism: `_release_turn` is
        token-guarded, so when the stale holder finally closes, its release is a
        no-op and cannot free the slot underneath the reattached consumer.
        """
        if self._turn_active and not preempt:
            return None
        self._turn_active = True
        self._turn_token = object()
        return self._turn_token

    def _release_turn(self, token: object) -> None:
        if self._turn_token is token:
            self._turn_active = False
            self._turn_token = None

    async def _stream(self, text: str, session: dict,
                      resume: bool = False) -> AsyncIterator[AgentEvent]:
        """Assumes the caller already holds the turn slot (see above)."""
        turn_started = time.perf_counter()
        first_sdk_event_logged = False
        first_text_logged = False
        reader_for_turn: _MessageReader | None = None
        self._last_status = None
        self._current_session_id = session.get("session_id")
        try:
            # `resume` is only what the CALLER wants; `_resolve_resume` decides
            # from the CLI's transcript, because both flags are fatal when they
            # disagree with the disk. Building the dict here (rather than
            # inside _ensure_client) keeps the client factory's contract
            # unchanged: it still receives a session dict with `resume`
            # already settled.
            connect_session = self._resolve_resume(dict(session,
                                                       resume=resume))
            client = await self._ensure_client(connect_session)
            # A new `query()` starts a new turn, so anything the previous turn's
            # reader was still holding belongs to a turn nobody will relay --
            # this is the only place it is safe to drop it.
            self._retire_reader()
            query_started = time.perf_counter()
            await client.query(text)
            log_performance(
                _log,
                self._current_session_id, "query", query_started)
            reader_for_turn = _MessageReader(
                client.receive_response().__aiter__())
            self._reader = reader_for_turn
            async for ev in self._pump(reader_for_turn):
                if not first_sdk_event_logged:
                    log_performance(
                        _log,
                        self._current_session_id,
                        "first_sdk_event",
                        turn_started,
                    )
                    first_sdk_event_logged = True
                if not first_text_logged and ev.kind == "message" and ev.text:
                    log_performance(
                        _log,
                        self._current_session_id, "first_text", turn_started)
                    first_text_logged = True
                yield ev
        except Exception:
            _log.exception("claude sdk turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            if reader_for_turn is None or not reader_for_turn.transcript_flushed:
                await self._flush_transcript_mirror()
            log_performance(
                _log,
                self._current_session_id, "driver_turn_total", turn_started)

    async def _continue_after_answers(self) -> AsyncIterator[AgentEvent]:
        """Relay the REST of a turn that `_pump` parked on a question.

        No `query()` here -- the turn is still mid-flight; the CLI was only
        waiting for the permission response that resolving the future produces.

        And no new iterator either: the SAME `_MessageReader` from the question
        turn is still reading, which is what makes this safe. It never stopped,
        so messages the CLI sent while the question was on screen -- or while
        the SSE consumer was away, or after it abandoned the `run()` generator
        entirely (runner.py:144-152) -- are sitting in `reader.inbox` waiting to
        be relayed. This is precisely what the old shape got wrong: it opened a
        FRESH iterator and depended on anyio having buffered those messages,
        which it had not, because `send_nowait` hands an item straight to a
        parked receiver (see _MessageReader).

        Assumes the caller already holds the turn slot.
        """
        if self._client is None:  # defensive: no client, nothing to resume
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        reader = self._reader
        if reader is None:
            # No reader means no turn in flight for this future to belong to.
            # Reported as a turn failure rather than a silent empty turn, since
            # the answers the user submitted have nowhere to go.
            _log.warning("answers resolved with no reader for the turn")
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        reader.transcript_flushed = False
        try:
            async for ev in self._pump(reader):
                yield ev
        except Exception:
            _log.exception("claude sdk answer turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            if not reader.transcript_flushed:
                await self._flush_transcript_mirror()

    async def _resume_with_answers(self, interrupt_id: str,
                                   answers: dict[str, str],
                                   session: dict) -> AsyncIterator[AgentEvent]:
        """The backend restarted: no future to resolve, so resume the session
        and deliver the answers as an ordinary text turn. The model already
        has the question's context in the transcript, so all this prompt has
        to do is say which option was chosen.

        `resume=True` below is a REQUEST, not a guarantee: `_resolve_resume`
        overrules it when the transcript is gone (a replaced instance, a
        recycled config dir), because `--resume` on a missing session exits 1
        and would turn the user's answer into "agent turn failed". The answers
        then land in a fresh session -- the model loses the question's context,
        which is why the prompt restates the question and the chosen label
        rather than relying on the transcript.

        Assumes the caller already holds the turn slot.
        """
        data = await load_pending(self._s3)
        if data is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        # Same guard as the live path, for the same reason -- an old browser
        # tab answering a superseded round. Without it the stored questions
        # were translated against the CALLER's answers regardless of which
        # round they belonged to, so a stale tab silently answered the current
        # question with the wrong round's answers AND deleted the real pending
        # record on the way out (reproduced: seeded "i-CURRENT"/"NEW question",
        # submitted "i-STALE" -> prompt said `- NEW question → 진행`, record
        # gone). Refuse and leave the record intact so the live form still works.
        if data.get("interrupt_id") != interrupt_id:
            _log.warning("answers for a superseded question round — refused")
            yield AgentEvent(kind="error", text="no pending questions")
            return
        sdk_questions = data.get("sdk_questions") or []
        lines = []
        for k, v in answers.items():
            try:
                q = sdk_questions[int(k) - 1]
            except (ValueError, IndexError, KeyError, TypeError):
                continue
            label = self._answer_to_sdk(v, q.get("options", []))
            lines.append(f"- {q.get('question', '')} → {label}")
        # The last line is a machine-readable record of the round these
        # answers belong to. It is in the prompt (not just the log) because
        # after a restart the S3 pending record is deleted on the way out and
        # the transcript becomes the ONLY durable trace of which interrupt a
        # given set of answers resolved -- which is what you need when a
        # resumed session answers the wrong question. It is also what the
        # contract test's echo fake reads back to prove the caller's
        # interrupt_id/answers reached the SDK call.
        prompt = prompts.answers_resumed(
            self._language, "\n".join(lines),
            json.dumps({"interrupt_id": interrupt_id, "answers": answers},
                       ensure_ascii=False))
        await self._clear_pending_quietly()
        self._clear_pending_state()
        async for ev in self._stream(prompt, session, resume=True):
            yield ev

    # ---- driver contract (same three methods as StrandsDriver) ----

    def _place_rules(self) -> bool:
        """Rule placement happens every turn -- the workspace is volatile
        (runner reconstructs it from S3 each turn, and runner.py:36 restores
        only aiplc-docs/, prototype/, uploads/ -- never the rules) and without
        them the agent runs with no workflow to follow, which shows up as an
        empty conversation rather than an error. False means the turn must be
        abandoned.

        매 턴 쓰는 것이 언어에도 유리하다: 언어 지시가 워크스페이스에 남아
        있지 않아도 다음 턴에 다시 깔린다."""
        started = time.perf_counter()
        try:
            place_rules(self._workspace, self._rules_dir, self._language)
            return True
        except Exception:
            _log.exception("rule placement failed")
            return False
        finally:
            log_performance(
                _log,
                self._current_session_id, "rules", started)

    async def run(self, text: str, session: dict) -> AsyncIterator[AgentEvent]:
        """Contract: runner.py:129 calls this."""
        self._current_session_id = session.get("session_id")
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # 이 시점의 워크스페이스에 이미 있는 문서를 "본 것"으로 표시한다 — 새
        # 드라이버의 첫 턴이 기존 문서 전부를 갱신으로 알리지 않게.
        self._seed_document_cursor()
        # The concurrency guard comes BEFORE the pending-question short-circuit
        # below: if a turn is genuinely still streaming, "turn already in
        # progress" is the accurate report, and re-surfacing the question
        # instead would tell the caller to answer a form while the turn it
        # belongs to is still being consumed by someone else. A question parked
        # by a turn that already RETURNED leaves the slot free (run() releases
        # it on the way out), so that case still reaches the short-circuit.
        token = self._acquire_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        try:
            # Mirrors StrandsDriver's B1 short-circuit (driver.py:166-177) for
            # the same class of failure, with a Claude-SDK-specific cause:
            # while a question is parked, the CLI is blocked waiting for the
            # permission response, so `query()` would be accepted and then
            # never answered -- the turn would poll to nowhere until the client
            # gave up. Re-surface the pending question instead of calling the
            # model.
            fut = self._pending_question
            if fut is not None and not fut.done():
                yield AgentEvent(kind="message",
                                 text=prompts.answer_first(self._language))
                if self._pending_payload is not None:
                    yield AgentEvent(kind="questions",
                                     payload=self._pending_payload)
                yield AgentEvent(kind="done")
                return
            # **버려진 턴을 새 턴이 덮지 못하게 한다(2026-08-19).** 슬롯은
            # 소비자가 사라지면 즉시 풀리므로(위 주석) 여기까지 올 수 있는데,
            # 그때 `_stream`은 `_retire_reader()`로 **진행 중인 턴의 리더를
            # 취소하고** 같은 CLI 세션에 `query()`를 겹쳐 넣는다. 그 함수의
            # 주석이 스스로 "the turn nobody will relay"를 전제로 쓰여 있다 —
            # 재접속 경로(`run_live`)가 생긴 지금 그 전제가 더 이상 참이 아니다.
            #
            # 파킹된 질문 short-circuit **뒤에** 두는 것이 중요하다: 그 리더도
            # 살아 있으므로(has_live_turn 참) 앞에 두면 질문 폼을 다시 띄우는
            # 경로가 이 거부로 바뀐다.
            if self.has_live_turn():
                _log.info("refusing a new turn: one is still streaming with no "
                          "consumer — the caller should reattach")
                yield AgentEvent(kind="error", text="turn already in progress")
                return
            async for ev in self._stream(text, session):
                yield ev
        finally:
            self._release_turn(token)

    def has_live_turn(self) -> bool:
        """A turn is still streaming with nobody consuming it.

        **왜 `_turn_active`로는 알 수 없는가.** 그 플래그는 *소비자*가 있는지를
        말한다. SSE가 끊기면 `run()`의 finally가 그것을 즉시 지운다 —
        의도된 동작이다(재접속한 브라우저가 "turn already in progress"로 튕기지
        않아야 한다). 하지만 CLI 턴 자체는 계속 돌고 `_MessageReader`가 계속
        읽는다. 그 둘을 구별하는 것이 이 함수다.

        `ended`까지 보는 이유: 파킹된 질문의 리더는 살아 있지만 끝나지 않았고
        (그래서 True), 정상 종료한 턴의 리더는 태스크가 끝났거나 `ended`다.
        """
        reader = self._reader
        return (reader is not None and not reader.task.done()
                and not reader.ended)

    async def run_live(self) -> AsyncIterator[AgentEvent]:
        """진행 중인 턴에 다시 붙는다 — 새 `query()` 없이.

        **왜 필요한가(2026-08-19).** 사용자의 PC가 절전·화면보호기로 들어가면
        네트워크가 끊기고 SSE가 죽는다. 턴이 2.5~5.6분이므로 화면보호기 기본값
        (5~10분)과 정면으로 겹친다. 그때 잃는 것은 **화면뿐**이다: 리더는 계속
        읽고(`has_live_turn`), 파일은 PostToolUse가 쓰는 즉시 S3에 올라가고,
        포기 경로가 트랜스크립트를 flush한다. 그런데 그 진행 중인 턴을 **다시
        볼 창구가 없었다** — `GET /pending`은 질문에 파킹된 경우만, `GET /history`
        는 끝난 뒤만이고, `GET /events?turn=`은 POST가 만든 1회용·60초 핸들을
        요구한다.

        **`_continue_after_answers`를 그대로 쓴다.** 그 함수의 본문은 이미
        "진행 중인 턴의 나머지를 같은 리더로 흘린다"가 전부다 — 답변 해소
        (`fut.set_result`)는 `run_answers`가 그것을 부르기 **전에** 한다. 즉
        재접속과 답변 후 재개는 같은 동작이고, 다른 것은 그 앞에 무엇을 하는지뿐이다.

        붙을 턴이 없으면 `done` 하나만 준다 — 에러가 아니다. 사용자가 늦게
        돌아왔고 턴이 그동안 끝난 것이 정상 경로이며, 그때 화면은
        `GET /history`로 복원된다.
        """
        # **선점한다** — 이 경로는 거부하지 않는다.
        #
        # 예전에는 `_acquire_turn()`으로 잡고 실패하면 "turn already in
        # progress"를 냈고, 근거는 "탭 두 개가 같은 outbox를 읽으면 한쪽이
        # 메시지를 잃는다"였다. 그 근거는 맞지만 **이 게이트가 막는 것의 대부분은
        # 두 번째 탭이 아니라 죽은 첫 번째 탭이다**: 절전된 클라이언트는 FIN을
        # 보내지 않으므로 떠난 소비자의 제너레이터가 슬롯을 쥔 채 살아 있고,
        # 그것이 정확히 재접속이 필요한 순간이다. 실측(2026-08-19): 거부 문구가
        # 사용자 화면에 에이전트 발화로 떴다.
        #
        # 정책은 "방금 온 요청이 이긴다"다. 사람은 한 번에 한 화면만 보므로 새
        # 연결이 진짜 사용자다. 옛 소비자는 새 토큰 발급으로 축출된다(아래
        # `_owns_turn` 검사가 그것을 실제 중단으로 옮긴다).
        token = self._acquire_turn(preempt=True)
        try:
            if not self.has_live_turn():
                yield AgentEvent(kind="done")
                return
            async for ev in self._continue_after_answers():
                yield ev
        finally:
            self._release_turn(token)

    async def run_answers(self, interrupt_id: str, answers: dict[str, str],
                          session: dict) -> AsyncIterator[AgentEvent]:
        """Contract: runner.py:167 calls this.

        Two paths, and BOTH validate `interrupt_id` against the round they are
        about to answer -- a mismatch is an old tab answering a superseded
        question, and is refused with the contract's "no pending questions".

        A waiting future means a normal round trip: resolve it, and the answers
        reach the model as the AskUserQuestion tool result. No future means the
        backend restarted, so resume the session and deliver the answers as a
        text turn instead.
        """
        # The rules go down on this path too, and this is the ONE path where
        # the workspace is guaranteed cold: no-future means a redeploy, so the
        # agent's first post-restart action would otherwise run with no
        # CLAUDE.md at all. Cheap and idempotent, so it is unconditional
        # rather than branch-dependent.
        self._current_session_id = session.get("session_id")
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # 이 시점의 워크스페이스에 이미 있는 문서를 "본 것"으로 표시한다 — 새
        # 드라이버의 첫 턴이 기존 문서 전부를 갱신으로 알리지 않게.
        self._seed_document_cursor()
        token = self._acquire_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        try:
            fut = self._pending_question
            if fut is not None and not fut.done():
                if self._pending_iid != interrupt_id:
                    # Resolving the live future with a superseded round's
                    # answers would feed the model answers to a different
                    # question; falling through to the resume path is no
                    # better, since `query()` while the CLI is blocked hangs.
                    yield AgentEvent(kind="error", text="no pending questions")
                    return
                fut.set_result(answers)
                await self._clear_pending_quietly()
                # Drop any `questions` event for THIS round that is still owned
                # and undelivered. It exists when the question turn was abandoned
                # before the card reached the browser (the user then got it from
                # GET /pending instead), and relaying it now would re-show a card
                # the user has just answered -- and answering it a second time is
                # refused with "no pending questions", since the future is gone.
                # The answers this call carries are that event's whole purpose,
                # so it has been fulfilled, not lost.
                self._queue[:] = [ev for ev in self._queue
                                  if not (ev.kind == "questions"
                                          and _iid_of(ev) == interrupt_id)]
                async for ev in self._continue_after_answers():
                    yield ev
                return
            async for ev in self._resume_with_answers(interrupt_id, answers,
                                                      session):
                yield ev
        finally:
            self._release_turn(token)

    async def pending(self, session: dict) -> str | None:
        """Contract: runner.py:183 calls this. In-memory first, then S3 --
        covers both a same-process refresh and a backend restart.

        세 번째로 **파일 질문 라운드**를 본다. 그 라운드는 파킹된 future를 남기지
        않으므로(PostToolUse 훅이 턴을 끝냈다) 위 두 경로에 아무것도 없다.
        마지막에 두는 이유: 위 둘이 값을 가진 상태는 답을 기다리는 살아 있는
        future가 있다는 뜻이고, 그때는 그 질문이 답해져야 턴이 재개된다.
        """
        if self._pending_payload is not None:
            return self._pending_payload
        data = await load_pending(self._s3)
        if data is not None:
            return json.dumps({"interrupt_id": data["interrupt_id"],
                               "questions": data["questions"]},
                              ensure_ascii=False)
        return await self._pending_from_file()

    async def _pending_from_file(self) -> str | None:
        """열려 있는 질문 파일을 S3에서 다시 읽어 카드를 재구성한다.

        **로컬 워크스페이스가 아니라 S3를 읽는다.** 로컬은 턴 시작에만 복원되므로
        (runner.py의 `_restore_workspace_from_s3`) 재시작 직후의 `GET /pending`은
        빈 디렉터리를 보게 된다. S3가 유일한 진실이다.

        미답 문항이 없으면 None이다 — 그것이 이 라운드의 종료 신호이고, 그래서
        답변 제출 경로가 무엇도 지우지 않아도 된다.
        """
        rel = await load_pending_file(self._s3)
        if rel is None:
            return None
        from aipds.parsers.questions import parse_question_file
        try:
            md = await self._s3.get(rel)
        except FileNotFoundError:
            # 파일이 사라졌다(삭제·경로 변경). 복원은 편의이므로 조용히 포기한다.
            return None
        qfile = parse_question_file(rel, md)
        if not qfile.parse_ok:
            return None
        if not any(not (q.answer or "").strip() for q in qfile.questions):
            return None
        return json.dumps({"interrupt_id": "", "file": rel, "questions": qfile},
                          ensure_ascii=False,
                          default=lambda o: o.model_dump())

    # ---- lifecycle (beyond the three-method contract) ----

    async def disconnect(self) -> None:
        """Tear down the claude subprocess. Idempotent.

        NOT part of the driver contract and NOT yet called by anything --
        runner.stop() only rmtree's the local workspace, so today every deleted
        project leaks a ~300-500MB `claude` process for the life of the
        backend. builder.py:395-404 has the same method and proto/session.py
        calls it on close/idle-timeout; providing it here lets Task 8 wire
        runner.stop() to it without touching this file (runner.py is off-limits
        in this task).

        A pending question cannot survive the teardown -- its future is
        abandoned along with the subprocess, so leaving _pending_payload set
        would make pending() advertise a question that can never be answered
        (builder.interrupt() clears the same state for the same reason).

        Two more places the same dead question hides, both carry-forward from
        Task 6 and both real once this method is actually wired (Task 8):

        - The S3 mirror (`save_pending`). `pending()` checks the in-memory
          `_pending_payload` FIRST but falls back to `load_pending(self._s3)`
          when that is None -- so clearing only the in-memory copy just moves
          the dead question onto the other path; it does not remove it.
        - `self._queue` may still hold an unpopped `questions` AgentEvent for
          this exact question, if the turn that raised it was abandoned
          before the event was delivered (`_pump`'s ownership rule leaves it
          at the head of the queue for the next turn to relay -- see `_pump`'s
          docstring). With the subprocess gone, "the next turn" is a brand new
          one that has nothing to do with that question, so relaying it would
          hand the user a card for a question no future will ever resolve.
        """
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        self._queue[:] = [ev for ev in self._queue if ev.kind != "questions"]
        # The subprocess is going away, so there is no turn left for the reader
        # to collect and nothing that could relay what it holds.
        self._retire_reader()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("claude driver disconnect failed")
