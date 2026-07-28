# backend/pathfinder/proto/builder.py — the prototype build agent, running
# IN-PROCESS in the backend (was harness/sdk_driver.py inside a Tokyo MicroVM).
#
# One build session = one connected ClaudeSDKClient. Hook/tool callbacks run on
# the SDK's tasks while run() drains on the caller's loop -- both on the SAME
# event loop, so a plain list handoff is safe.
#
# Three things differ from the VM-era driver:
#   1. CLAUDE_CONFIG_DIR is always injected. The bundled binary is ordinary
#      Claude Code and reads ~/.claude when this is unset -- harmless in the
#      VM (empty home) but on the workshop EC2 that is the operator's personal
#      skills/agents/CLAUDE.md, which would leak into every workshop build and
#      make results depend on host config.
#   2. session_store + resume make the transcript durable, so a session can be
#      resumed days later or after a backend redeploy.
#   3. disconnect() exists. Stopping the VM used to reclaim the process; now
#      the idle timer must do it explicitly.
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Callable

from pathfinder.agent.questions_payload import question_file_from_sdk
from pathfinder.models import AgentEvent

_log = logging.getLogger(__name__)

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_LETTERS = "ABCDEFGHIJ"

# A workshop build runs unattended -- there is no operator to approve a Write,
# so any mode that can prompt stalls the turn until the idle timer kills it.
DEFAULT_PERMISSION_MODE = "bypassPermissions"


def _interrupt_id_of(event: AgentEvent) -> str | None:
    """The interrupt_id inside a `questions` event's payload, or None.

    Same shape and same fail-soft posture as proto/session.py's
    `_interrupt_id_from` and claude_driver's `_iid_of`: a malformed or
    contract-drifted payload degrades to None rather than raising, because the
    only caller is `_drop_answered_question_card` and a parse failure there must
    not blow up an otherwise valid `submit_answers`. Returning None simply keeps
    the event (the conservative direction -- a re-shown card is recoverable, a
    dropped one is the message loss this whole change is about).
    """
    if not event.payload:
        return None
    try:
        value = json.loads(event.payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; reject escapes.
    (Ported from the old claude_driver._rel — see its docstring for why any
    `..` in the relativized parts is an escape, not merely relative.)

    Fix vs. the brief's literal version: `relative_to` also raises ValueError
    when `path` is absolute but shares no prefix with `workspace` at all
    (e.g. "/etc/passwd" vs workspace "/workspace") — not just for genuinely
    relative inputs. The original fallback (`path.lstrip("/")`) treated both
    cases as "already relative", which let an unrelated absolute path escape
    undetected (caught by test_post_tool_hook_rejects_escape). Only fall back
    to the lstrip path when `path` was not absolute to begin with; an
    absolute path that isn't under the workspace is always an escape.
    """
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        if path.startswith("/"):
            return None
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


def _validate_permission_mode(mode: str) -> str:
    """Reject an unknown mode here rather than letting it reach the CLI.

    An unrecognized --permission-mode kills the subprocess during connect(),
    which run() reports as a generic "agent turn failed" -- the exact
    late-and-opaque failure mode the --session-id/--resume clash produced.
    The valid set is read off the SDK's own Literal so it cannot drift.
    """
    from typing import get_args

    from claude_agent_sdk.types import PermissionMode

    valid = get_args(PermissionMode)
    if mode not in valid:
        raise ValueError(
            f"unknown permission_mode {mode!r}; expected one of {', '.join(valid)}")
    return mode


def _default_client_factory(builder: "PrototypeBuilder") -> Callable[[], Any]:
    def make():
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from claude_agent_sdk.types import HookMatcher

        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            # Swap the config HOME rather than disabling settings entirely
            # (setting_sources=[]): this keeps a place to put OUR skills and
            # subagents later, and keeps the local transcript copy under a
            # Pathfinder-owned path instead of the operator's home.
            "CLAUDE_CONFIG_DIR": builder._config_dir,
        }
        if builder._anthropic_model:
            env["ANTHROPIC_MODEL"] = builder._anthropic_model
        options = ClaudeAgentOptions(
            permission_mode=builder._permission_mode,
            cwd=builder._workspace,
            env=env,
            # "user" now means OUR config dir, so this is safe -- and it is
            # what `skills` needs open to discover anything.
            setting_sources=["user", "project"],
            # Enable every skill discovered under CLAUDE_CONFIG_DIR (the repo's
            # proto-config/skills/, shipped to /opt/pathfinder/proto-config).
            # "all" rather than an explicit name list so adding a skill is one
            # committed file with no code change. Safe precisely BECAUSE the
            # config dir is ours: with the default ~/.claude this would enable
            # whatever the host user happens to have installed.
            # Note this makes the SDK pass `--allowedTools Skill`; under
            # bypassPermissions that is not expected to restrict Bash/Write,
            # but the e2e checklist verifies a real build turn still works.
            skills="all",
            # Exactly one of the two, never both: the CLI rejects
            # `--session-id` alongside `--resume` unless `--fork-session` is
            # also passed ("--session-id can only be used with --continue or
            # --resume if --fork-session is also specified"), which killed
            # every resumed build at connect(). Forking is not the fix either
            # -- it would continue under a NEW id, orphaning the transcript
            # that session.py persisted. `--resume=<id>` alone already keeps
            # the session on that same id, which is all session_id bought us.
            session_id=None if builder._resume else builder._session_id,
            resume=builder._session_id if builder._resume else None,
            session_store=builder._session_store,
            # Kept even under bypassPermissions, which the SDK warns shadows
            # this callback entirely. The warning overstates our case: probed
            # against the real CLI, Bash/Write do skip the callback, but
            # AskUserQuestion still reaches it -- and that is the only tool we
            # intercept (it is how a question becomes an SSE `questions` event).
            # Dropping the callback to silence the warning would break that.
            can_use_tool=builder._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[builder._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


def _suppress_shadowed_callback_warning() -> None:
    """Mute CanUseToolShadowedWarning -- for THIS wiring it is a false positive.

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


class PrototypeBuilder:
    def __init__(self, workspace: str, config_dir: str, session_id: str,
                 resume: bool, session_store: Any = None,
                 anthropic_model: str | None = None,
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[], Any] | None = None):
        self._workspace = workspace
        self._config_dir = config_dir
        self._session_id = session_id
        self._resume = resume
        self._session_store = session_store
        self._anthropic_model = anthropic_model
        self._permission_mode = _validate_permission_mode(permission_mode)
        self._factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        # A plain list, not collections.deque: tests assert `_queue == []`
        # after draining, and deque never compares equal to a list literal.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._interrupted = False
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None

    # There is deliberately NO `drain_queue()` batch pop here anymore. A batch
    # pop moves events out of the queue that OWNS them and into the caller's
    # frame, which `GeneratorExit` destroys -- so every one not yet yielded is
    # lost when the consumer walks away mid-sequence (SSE disconnect, proxy
    # timeout, navigation; routes/prototypes.py's EventSourceResponse takes
    # that path routinely). Reproduced on this file before the fix: three
    # `file_changed` events queued by one MultiEdit burst, consumer takes the
    # first and disconnects -> `_queue == []` and the other two reached no
    # consumer, this turn or any later one. Its Discovery-side twin was deleted
    # for the same reason (claude_driver.py:537) -- a method whose SHAPE is the
    # bug invites reintroduction at the next call site, so every consumer of
    # `_queue` goes through `_relay_queue` instead.

    async def _relay_queue(self) -> AsyncIterator[AgentEvent]:
        """Yield queued hook/callback events, popping each only AFTER delivery.

        The ownership rule, copied verbatim in effect from
        claude_driver._relay_queue / `_pump`'s `relay` closure (read those for
        the full argument -- it survived five review rounds there):

            An event lives in exactly one place that outlives this generator,
            and it leaves that place only after the consumer has actually
            received it.

        `queue[0]` -> `yield` -> `pop(0)`, never `pop(0)` -> `yield`, is the
        whole fix. Reaching the line after the `yield` IS the proof of
        delivery: it only runs when the consumer came back for the next item.
        So a consumer abandoned at any `yield` leaves the remainder still owned
        by `self._queue`, and the next turn's poll loop relays it.

        The identity re-check before popping is LOAD-BEARING -- not defensive,
        which is what an earlier draft of this docstring wrongly claimed on the
        grounds that producers "only ever append". They do not: this very class
        has a head-MUTATING producer, and it is reachable precisely while this
        generator is suspended at its `yield`.

        `_drop_dead_question_cards()` rewrites the list in place
        (`self._queue[:] = [...]`), and `interrupt()` calls it from a SEPARATE
        request (`POST /interrupt`) while the turn's generator is parked
        mid-delivery. Reproduced: the agent asks a question, the card is
        delivered and this generator suspends with it still at `queue[0]`; the
        agent's last `Write` queues `file_changed: realwork.js` BEHIND it; the
        user hits stop; the drop removes the head and shifts `realwork.js` into
        slot 0. `queue[0] is ev` then fails, nothing is popped, and `realwork.js`
        is delivered on the next pass. Mutated to an unconditional `pop(0)`, it
        is destroyed outright -- the exact defect class this change exists to
        eliminate -- and the whole suite still passes, which is why
        `test_a_head_mutation_during_a_suspended_relay_does_not_eat_the_next_event`
        exists to pin it.

        Accepts at-least-once delivery at the abandonment boundary -- a consumer
        cancelled at its `__anext__` await AFTER this generator produced the
        value leaves the event un-popped, so the next turn relays it again.
        That is the deliberate trade the Discovery driver made and it holds here
        too: see the duplicate-safety note on `_on_post_tool_use`.

        SCOPE -- the ownership story in this file is ASYMMETRIC, deliberately so,
        and a reader should not generalize from this method. The QUEUE side (hook
        and can_use_tool events, everything routed through here) honors the rule.
        The MESSAGE side does NOT: `_translate()` returns a plain list that `run`
        yields one item at a time, so an `AssistantMessage` carrying several
        blocks still loses its un-yielded remainder when the consumer walks away
        (reproduced; byte-identical before and after this change, i.e.
        pre-existing rather than introduced here). Closing that half needs the
        durable-inbox structure `claude_driver._MessageReader` was built for -- a
        task that owns `receive_response()` and drains into a list on the driver,
        so no suspension of the relaying generator can destroy an in-flight
        message. It is not reachable by another `_relay_queue`-shaped fix, which
        is why it was left for its own change.
        """
        while self._queue:
            ev = self._queue[0]
            yield ev
            # Reached only if the consumer came back for the next item.
            if self._queue and self._queue[0] is ev:
                self._queue.pop(0)

    async def _ensure_client(self):
        if self._client is None:
            _suppress_shadowed_callback_warning()
            self._client = self._factory()
            await self._client.connect()
        return self._client

    # DUPLICATE-DELIVERY SAFETY for the prototype path, checked against these
    # consumers rather than inherited from the Discovery ruling.
    #
    # `_relay_queue` pops only after delivery, so the one item in flight when a
    # consumer is cancelled at its `__anext__` is re-sent on the next turn:
    # at-least-once, never at-most-once (the alternative loses it outright,
    # which is the defect). What a duplicate does to the prototype tab, per kind:
    #
    #   file_changed -> usePrototypeStream.ts:68-70 dedupes by path
    #                   (`prev.includes(path) ? prev : [...prev, path]`), so
    #                   `changedPaths` is idempotent. The trace entry (line 85-87)
    #                   does append a second row -- cosmetic, in a collapsed
    #                   "추론 과정" accordion, and it accurately reports that the
    #                   file was written.
    #   status       -> trace-only, same append-only cosmetic story.
    #   message      -> NOT queued here; messages come from `_translate`, never
    #                   through this queue, so text cannot be doubled by it.
    #   questions    -> the ONE kind a duplicate would harm: a re-shown card
    #                   whose future is gone answers with a 409
    #                   (routes/prototypes.py:212). Handled structurally instead
    #                   of by luck -- `_drop_dead_question_cards` removes it at
    #                   exactly the two points its answerer dies (interrupt,
    #                   disconnect). A card whose future is still LIVE is
    #                   correctly re-shown: it is still answerable.
    #   stage/document -> never produced on this path at all. The prototype
    #                   builder has no report_stage/submit_document tools (those
    #                   are Discovery's, agent/tools.py), and
    #                   usePrototypeStream has no stage/document branch. So the
    #                   brief's "document panel and artifact list" concern maps
    #                   here to `changedPaths` only, which is dedupe-safe.
    #
    # Conclusion: the Discovery ruling does transfer, but for a reason that had
    # to be re-checked -- this tab's reducers are dedupe-by-path or append-only
    # cosmetic, and the one non-idempotent kind is dropped at its death points.
    async def _on_post_tool_use(self, input_data, tool_use_id, context) -> dict:
        name = input_data.get("tool_name", "")
        if name in _FILE_TOOLS:
            fp = (input_data.get("tool_input") or {}).get("file_path", "")
            rel = _rel(fp, self._workspace)
            if rel is None:
                self._queue.append(AgentEvent(
                    kind="status", text="file outside workspace ignored"))
            else:
                self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return {}

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

    async def _on_can_use_tool(self, tool_name, input_data, context):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)
        import json as _json, uuid
        sdk_questions = input_data.get("questions", [])
        # question_file_from_sdk raises ValueError on unusable input (e.g. a
        # question with zero options) -- mirror ask_questions in tools.py:
        # deny with a message the model can read and retry from, instead of
        # letting the exception escape. Unlike ask_questions (which returns a
        # tool-result string), the can_use_tool contract only speaks
        # PermissionResult -- PermissionResultDeny is the SDK-native way to
        # hand the model an explanation and no other shape is invented here.
        try:
            qfile = question_file_from_sdk(sdk_questions, name="prototype-questions")
        except ValueError as e:
            _log.warning("AskUserQuestion payload rejected: %s", e)
            return PermissionResultDeny(
                message=f"질문을 만들 수 없다: {e}\n"
                        "각 질문에 옵션을 최소 1개 넣어 AskUserQuestion을 다시 호출해라.")
        iid = uuid.uuid4().hex
        payload = _json.dumps({"interrupt_id": iid, "questions": qfile},
                              ensure_ascii=False)
        self._pending_payload = payload
        self._pending_iid = iid
        loop = asyncio.get_running_loop()
        self._pending_question = loop.create_future()
        self._queue.append(AgentEvent(kind="questions", payload=payload))
        try:
            answers = await self._pending_question  # stays open until /answers
        except asyncio.CancelledError:
            # interrupt() cancels this future and clears pending state
            # itself, but guard defensively in case cancellation reached
            # us some other way (e.g. task cancellation from outside).
            self._pending_payload = None
            self._pending_question = None
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
            self._pending_payload = None
            self._pending_question = None
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

    def _translate(self, msg) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        if tname == "AssistantMessage":
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    if block.name != self._last_status:
                        self._last_status = block.name
                        events.append(AgentEvent(kind="status", text=block.name))
        elif tname == "ResultMessage":
            events.append(AgentEvent(kind="done"))
        return events

    async def run(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        self._interrupted = False
        self._last_status: str | None = None
        next_msg: asyncio.Future | None = None
        # The turn's ONE terminal event, held rather than yielded the moment
        # `_translate` produces it -- see the terminal harvest after the loop.
        terminal: AgentEvent | None = None
        try:
            client = await self._ensure_client()
            await client.query(text)
            # Race the next message against the hook/tool-callback queue:
            # while an AskUserQuestion is pending, receive_response() yields
            # nothing at all, so a plain `async for` would never let a
            # queued `questions` event reach the SSE stream. Poll the queue
            # on a short timeout instead of blocking indefinitely on the
            # next message.
            agen = client.receive_response().__aiter__()
            next_msg = asyncio.ensure_future(agen.__anext__())
            while True:
                assert next_msg is not None  # loop invariant (narrows Optional)
                done, _ = await asyncio.wait({next_msg}, timeout=0.05)
                # CALL SITE 1 -- the normal turn's per-poll relay. This is the
                # one that runs thousands of times a build and the one the
                # reproduction hit: a MultiEdit burst queues three
                # `file_changed` events, the SSE client disconnects after the
                # first, and the batch pop took the other two down with the
                # frame. `_relay_queue` leaves them owned instead.
                async for ev in self._relay_queue():
                    yield ev
                if not done:
                    continue
                try:
                    msg = next_msg.result()
                except StopAsyncIteration:
                    break
                for ev in self._translate(msg):
                    if ev.kind == "done":
                        # HELD, not yielded here. `done` used to go out the
                        # moment the ResultMessage was translated, which put it
                        # AHEAD of the post-loop queue relay (old call site 4)
                        # -- and sse.ts:29 closes the EventSource on `done`, so
                        # every event that relay yielded afterwards was dropped
                        # client-side. The terminal harvest below drains the
                        # queue first and emits this last.
                        terminal = ev
                        continue
                    yield ev
                if terminal is not None:
                    # The SDK's receive_response() returns right after the
                    # ResultMessage, so re-arming would only buy a
                    # StopAsyncIteration on an already-finished iterator.
                    #
                    # What this `break` gives up, since the original re-armed
                    # `__anext__()` specifically to drive that generator to its
                    # `return`: `agen` is left SUSPENDED rather than finalized,
                    # so its cleanup runs at GC / loop `shutdown_asyncgens()`
                    # instead of here. Measured and accepted -- no
                    # "async generator ignored GeneratorExit", no "Task was
                    # destroyed but it is pending", no leaked tasks -- because
                    # the object that actually owns the subprocess and the anyio
                    # streams is the CLIENT, and `disconnect()` closes the query
                    # independently of this iterator. Driving the generator one
                    # more step to reach its `return` would mean awaiting a
                    # message that never comes, which is the stall this poll loop
                    # exists to avoid.
                    break
                next_msg = asyncio.ensure_future(agen.__anext__())
        except asyncio.CancelledError:
            # interrupt() cancels the pending-question future; that
            # cancellation surfaces here via next_msg.result(). It is OUR
            # deliberate interrupt, not the consumer cancelling us -- so the
            # stream must still end with a proper terminal event (the UI
            # otherwise shows a dead connection for a user-initiated stop).
            # A genuine external cancellation (consumer task cancelled) has
            # _interrupted unset and must propagate untouched.
            if not self._interrupted:
                raise
            # CALL SITE 2 -- the interrupt path. Relayed BEFORE the terminal
            # pair for the same sse.ts:29 reason as everywhere else: whatever
            # the agent wrote just before the user hit stop is real work, and
            # anything emitted after `done` never reaches onEvent.
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="status", text="interrupted")
            yield AgentEvent(kind="done")
            return
        except Exception:
            _log.exception("sdk turn failed")
            # CALL SITE 3 -- the error path. Order matters here specifically:
            # sse.ts:29 closes the EventSource on `error` as well as on `done`,
            # so a queue relayed after the terminal event is silently dropped
            # client-side and the artifact list ends the turn disagreeing with
            # what the agent actually wrote.
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            self._turn_active = False
            # The consumer may abandon this generator mid-stream (SSE client
            # disconnect -> aclose() -> GeneratorExit): without this cancel
            # the in-flight __anext__ future outlives the generator and
            # asyncio logs "Task was destroyed but it is pending!".
            if next_msg is not None and not next_msg.done():
                next_msg.cancel()
        # CALL SITE 4 -- the terminal exit, reached only on a NORMAL loop exit
        # (both `except` arms return, so this is the success path only).
        #
        # What actually changed here vs. the old shape: the old code yielded
        # `done` EAGERLY, the moment `_translate` produced it, and then drained
        # the queue below -- i.e. after the terminal event. sse.ts:29 /
        # prototypes.ts:164 close the EventSource on `done`, so those events were
        # written by the backend and dropped by the client. Holding `terminal`
        # and emitting it last is that fix.
        #
        # HONEST NOTE on the drain below: it is DEFENSIVE, not load-bearing, and
        # measured to be so -- instrumented across the whole suite, this line is
        # reached 13 times and `self._queue` is empty every single time. That is
        # structural rather than lucky: the loop's own relay (call site 1) runs
        # at the top of every pass and drains the queue to exhaustion, and
        # between that last drain and the `break` there is no suspension point
        # for a hook to append through (`_translate` is synchronous, and a
        # ResultMessage translates to the terminal event alone, so no `yield`
        # intervenes). Mutating this drain to run AFTER the terminal event
        # therefore changes no observable behavior. It stays because it costs
        # nothing and it keeps the invariant true under future edits that DO add
        # a suspension there -- but no test pins its ordering, because no test
        # honestly can.
        while self._queue:
            async for ev in self._relay_queue():
                yield ev
        if self._interrupted:
            # Moved here from inside `_translate`'s loop along with `done`, so
            # the interrupt marker stays immediately before the terminal event
            # rather than being emitted at translate time and separated from it
            # by the harvest above.
            yield AgentEvent(kind="status", text="interrupted")
        # Exactly one terminal event, always LAST. `terminal` is None only when
        # the message stream ended with no ResultMessage at all; that yielded
        # ZERO terminal events before this change, which hangs the SSE client
        # forever (sse.ts only closes on done/error/transport failure).
        yield terminal if terminal is not None else AgentEvent(kind="done")

    async def interrupt(self) -> None:
        if self._client is None or not self._turn_active:
            return  # idempotent no-op
        self._interrupted = True
        # A pending question cannot survive an interrupt: _on_can_use_tool's
        # await is abandoned along with the rest of this turn, so leaving
        # _pending_payload set would make pending() report a question that
        # can never be answered, and a later submit_answers() would resolve
        # a future nobody is listening on anymore (returns True but nothing
        # continues). Clear it before touching the client, so our state is
        # consistent even if client.interrupt() raises.
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._pending_payload = None
        self._pending_question = None
        self._pending_iid = None
        self._drop_dead_question_cards()
        await self._client.interrupt()

    def _drop_dead_question_cards(self) -> None:
        """Drop any `questions` event still OWNED by `self._queue`.

        A consequence of the delivery-then-pop ownership rule, and one of the two
        places it needs a counterweight (the other is `_drop_answered_question_card`
        below -- same idea, different trigger). If a turn was abandoned while its
        `questions` event was being delivered, that event correctly stays at the
        head of the queue for the next turn to relay -- which is what stops the
        loss. But once the question's future has been cancelled (interrupt) or its
        subprocess torn down (disconnect), no `submit_answers` can ever resolve
        it: relaying it later hands the user a card that looks live, and
        answering it gets a 409 from routes/prototypes.py because the session's
        `_pending_interrupt_id` no longer matches.

        So it is dropped exactly where the thing that could ANSWER it dies --
        never merely because a consumer walked away. Mirrors the same two lines
        in claude_driver.disconnect (claude_driver.py:1242), which is where this
        hole was found on the Discovery side.

        Unconditional on kind (not keyed by interrupt id) because both callers
        are killing the subprocess or the whole turn: every question the builder
        is holding becomes unanswerable at once, not just one round's.
        """
        self._queue[:] = [ev for ev in self._queue if ev.kind != "questions"]

    def _drop_answered_question_card(self, interrupt_id: str) -> None:
        """Drop the owned `questions` event for the round just ANSWERED, but only
        when no live turn is left to deliver it.

        The third drop point, ported from claude_driver.py:1181-1183 (its
        `run_answers`). Without it, a successfully answered question can be
        re-shown -- a regression this ownership change introduced, since the old
        batch pop had already destroyed the event.

        Reproduced end-to-end: turn 1 delivers the card and the SSE stream dies at
        that very `yield`, leaving it owned (correct -- that is the fix). The
        pending future is NOT dead, because `can_use_tool` runs on a detached SDK
        task (claude_agent_sdk/_internal/query.py:231 `spawn_detached`), not on
        the abandoned generator -- so the user answering from the still-open tab
        reaches `submit_answers`, it returns True, and the turn genuinely
        continues. But the queue still owned the card, so the next turn relayed
        it: a question the user already answered, and answering it again returns
        False -> 409 (routes/prototypes.py:212) -> "답변을 제출하지 못했습니다".

        THE `_turn_active` GUARD IS NOT OPTIONAL, and this is where the port had
        to diverge from the reference rather than copy it. Discovery's `_pump`
        TERMINATES on a `questions` event (module docstring: yield questions, then
        done, then return), so by the time its `run_answers` drops the card there
        is provably no generator left that could deliver it. builder.py is the
        opposite: it keeps ONE stream open across the whole answer round trip, so
        the common case is a LIVE `run()` still parked in its poll loop that has
        not relayed the card yet -- answers can arrive that fast, and
        test_question_roundtrip drives exactly that. Dropping unconditionally
        there retires the card out from under the live generator and the user never
        sees the question at all (measured: consumer received `['done']`, the
        question silently vanished). Caught by that pre-existing test, which was
        the authority.

        So: `_turn_active` False means the turn that owned this card is gone and
        nothing will ever relay it -> retire it. True means a live generator will
        deliver it on its next pass -> leave it alone; it is not stale yet, and
        the SSE stream the user is watching is where it belongs.

        Keyed by interrupt id, unlike `_drop_dead_question_cards`: only THIS round
        has been fulfilled. A different round's card still owned here is still
        answerable and must survive -- dropping it would be the original
        message-loss defect wearing a different hat.

        Not a loss: the answers this call carries are that event's entire purpose,
        so it has been fulfilled rather than discarded.
        """
        if self._turn_active:
            return
        self._queue[:] = [
            ev for ev in self._queue
            if not (ev.kind == "questions"
                    and _interrupt_id_of(ev) == interrupt_id)]

    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool:
        if (self._pending_question is None
                or getattr(self, "_pending_iid", None) != interrupt_id
                or self._pending_question.done()):
            return False
        # Before resolving: this round's card is now fulfilled, so it must not be
        # relayed to the user again. See `_drop_answered_question_card`.
        self._drop_answered_question_card(interrupt_id)
        self._pending_question.set_result(answers)
        return True

    async def pending(self) -> str | None:
        return self._pending_payload

    async def disconnect(self) -> None:
        """Tear down the claude subprocess. Idempotent -- close() and the idle
        timer can both reach here.

        Also clears the pending question and drops any `questions` event still
        owned by the queue: the future that would resolve it dies with the
        subprocess, so a later turn relaying that card would be advertising a
        question nothing can answer. Same reasoning as interrupt() above --
        see `_drop_dead_question_cards`.
        """
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._pending_payload = None
        self._pending_question = None
        self._pending_iid = None
        self._drop_dead_question_cards()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("builder disconnect failed")
