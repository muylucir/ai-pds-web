# backend/tests/test_proto_session.py — PrototypeSession over an in-process
# builder. VM scenarios (boot/token-mint/stop) are gone with vm.py; the
# question-ownership, status-transition, idle-timer and close-idempotency
# scenarios carried over unchanged in intent.
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aipds.models import AgentEvent
from aipds.proto.limits import BuildSemaphore
from aipds.proto.session import PrototypeSession
from aipds.proto.store import completed_digest, output_digest

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
PROJECT_ID = "proj-1"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
SESSION_KEY = f"prototypes/{SLUG}/session.json"
HANDOFF_KEY = f"prototypes/{SLUG}/handoff.json"


HISTORY_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/change-history.md"
HISTORY_LOCAL = HISTORY_KEY


def _complete_event(summary="할 일 앱", remaining="다크 모드", changes="없음"):
    return AgentEvent(kind="build_complete", payload=json.dumps(
        {"summary": summary, "remaining": remaining, "changes": changes},
        ensure_ascii=False))


class FakeBuilder:
    def __init__(self):
        self.queries: list[str] = []
        self.answer_calls: list[tuple[str, dict]] = []
        self.interrupt_calls = 0
        self.disconnect_calls = 0
        self.submit_result = True
        self._script: list[AgentEvent] = []
        self.fetch_hosts: set[str] = set()

    def script(self, events: list[AgentEvent]) -> None:
        self._script = events

    async def run(self, text: str):
        self.queries.append(text)
        for ev in self._script:
            yield ev

    async def submit_answers(self, interrupt_id: str, answers: dict) -> bool:
        self.answer_calls.append((interrupt_id, answers))
        return self.submit_result

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def pending(self) -> str | None:
        return None

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def allow_fetch_hosts(self, hosts) -> None:
        self.fetch_hosts.update(hosts)


def _session(s3, tmp_path, builder, semaphore=None, idle_seconds=1800,
             design_profiles=None):
    calls: list[bool] = []

    def factory(session_id: str, resume: bool):
        calls.append(resume)
        return builder

    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=s3,
        build_root=tmp_path / "protos",
        builder_factory=factory,
        semaphore=semaphore or BuildSemaphore(max_concurrent=2),
        idle_seconds=idle_seconds,
        design_profiles=design_profiles,
    )
    session._test_resume_calls = calls  # type: ignore[attr-defined]
    return session


# ---- start(): session id persistence + resume decision ----

async def test_start_generates_and_persists_a_uuid_session_id(tmp_path):
    import uuid

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    saved = json.loads(s3.blobs[SESSION_KEY])
    uuid.UUID(saved["session_id"])          # must be a REAL uuid: SDK rejects others
    assert session._test_resume_calls == [False]   # first start: nothing to resume
    assert session.status == "ready"


async def test_start_reuses_the_saved_session_id_and_resumes(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "11111111-2222-3333-4444-555555555555"})
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert session._test_resume_calls == [True]
    assert json.loads(s3.blobs[SESSION_KEY])["session_id"] == \
        "11111111-2222-3333-4444-555555555555"


async def test_start_regenerates_when_the_saved_id_is_not_a_uuid(tmp_path):
    """A hand-edited or legacy session.json must not wedge the session: the
    SDK would reject a non-UUID resume value outright."""
    import uuid

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps({"session_id": "proj-1-todo-app"})
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    uuid.UUID(json.loads(s3.blobs[SESSION_KEY])["session_id"])
    assert session._test_resume_calls == [False]


async def test_start_raises_file_not_found_when_spec_missing(tmp_path):
    s3 = FakeS3Store()
    session = _session(s3, tmp_path, FakeBuilder())
    with pytest.raises(FileNotFoundError):
        await session.start()


async def test_start_creates_the_build_directory(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert session.build_dir().is_dir()
    assert session.build_dir() == tmp_path / "protos" / PROJECT_ID / SLUG


async def test_start_writes_the_spec_into_the_build_directory(tmp_path):
    """The agent reads the spec with its own file tools from cwd, so the spec
    must exist on local disk -- the VM era pushed it over HTTP instead."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec body"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert (session.build_dir() / SPEC_KEY).read_text(encoding="utf-8") == "# spec body"


# ---- start(): build instructions (Path A.1) ----
#
# Path A.1's Discovery leaves build-instructions.md next to the spec — the builder's
# input that combines the spec with the design-context answers. Back when only the spec
# was planted, Step 2's design direction and device answers never reached the builder,
# and even when the agent went looking for that file it was not in cwd.

SINGLE_SPEC_KEY = "aiplc-docs/discovery/prototype/prototype-spec.md"
SINGLE_INSTRUCTIONS_KEY = "aiplc-docs/discovery/prototype/build-instructions.md"


def _single_session(s3, tmp_path):
    return PrototypeSession(
        project_id=PROJECT_ID, slug="prototype", s3=s3,
        build_root=tmp_path / "protos",
        builder_factory=lambda session_id, resume: FakeBuilder(),
        semaphore=BuildSemaphore(max_concurrent=2),
    )


async def test_start_plants_the_build_instructions_and_the_plan_names_them(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SINGLE_SPEC_KEY] = "# spec"
    s3.blobs[SINGLE_INSTRUCTIONS_KEY] = "# build instructions"
    session = _single_session(s3, tmp_path)

    await session.start()

    assert (session.build_dir() / SINGLE_INSTRUCTIONS_KEY).read_text(
        encoding="utf-8") == "# build instructions"
    prompt = session.first_prompt()
    assert SINGLE_SPEC_KEY in prompt
    assert SINGLE_INSTRUCTIONS_KEY in prompt


async def test_start_without_build_instructions_names_only_the_spec(tmp_path):
    """Path B (and A.1 before Step 3) has no instructions. Pointing at a missing file
    makes the agent go looking for it outside cwd (observed in `_missing_output_prompt`)."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert "build-instructions.md" not in session.first_prompt()


async def test_start_removes_a_build_instructions_copy_gone_from_s3(tmp_path):
    """As with the spec, S3 is the source of truth on every start — if a stale copy of
    deleted instructions stays in cwd, the prompt does not mention it but the agent can read it."""
    s3 = FakeS3Store()
    s3.blobs[SINGLE_SPEC_KEY] = "# spec"
    session = _single_session(s3, tmp_path)
    stale = session.build_dir() / SINGLE_INSTRUCTIONS_KEY
    stale.parent.mkdir(parents=True)
    stale.write_text("# old", encoding="utf-8")

    await session.start()

    assert not stale.exists()
    assert "build-instructions.md" not in session.first_prompt()


# ---- start(): brand profile sync (design_profiles) ----
#
# The profile store is a separate store at the bucket root, distinct from the
# project store (s3) the session reads the spec from -- do not mix the two FakeS3Stores.

async def test_start_plants_the_brand_profile(tmp_path):
    from aipds.design_profile import DesignProfileStore
    from aipds.proto.design_sync import DESIGN_FILENAME, THEME_FILENAME

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    profiles = DesignProfileStore(FakeS3Store())
    await profiles.save(filename="acme.md", uploaded_by="admin@x",
                        markdown="```tokens\nprimary: #5b2ea6\n```\n## 톤\n넉넉히.\n")

    session = _session(s3, tmp_path, FakeBuilder(), design_profiles=profiles)
    await session.start()

    build_dir = session.build_dir()
    assert "--primary: #5b2ea6;" in (build_dir / THEME_FILENAME).read_text()
    assert "넉넉히" in (build_dir / DESIGN_FILENAME).read_text()
    assert "aipds:design:start" in (build_dir / "CLAUDE.md").read_text()


async def test_start_without_a_profile_plants_nothing(tmp_path):
    from aipds.proto.design_sync import DESIGN_FILENAME, THEME_FILENAME

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())  # design_profiles not given

    await session.start()

    build_dir = session.build_dir()
    # The counterpart test_start_plants_the_brand_profile asserts that all three
    # artifacts (theme, DESIGN.md, CLAUDE.md section) are created. Here all three
    # must be absent to prove "plants nothing" -- checking only the theme would
    # miss a later change that makes the no-profile path unconditionally write
    # DESIGN.md or the CLAUDE.md section.
    assert not (build_dir / THEME_FILENAME).exists()
    assert not (build_dir / DESIGN_FILENAME).exists()
    claude_md = build_dir / "CLAUDE.md"
    assert not claude_md.is_file() or "aipds:design:start" not in claude_md.read_text()


async def test_start_refreshes_a_changed_profile(tmp_path):
    from aipds.design_profile import DesignProfileStore
    from aipds.proto.design_sync import THEME_FILENAME

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    profiles = DesignProfileStore(FakeS3Store())
    await profiles.save(filename="a.md", uploaded_by="x",
                        markdown="```tokens\nprimary: #5b2ea6\n```\n")
    session = _session(s3, tmp_path, FakeBuilder(), design_profiles=profiles)
    await session.start()

    await profiles.save(filename="b.md", uploaded_by="x",
                        markdown="```tokens\nprimary: #111111\n```\n")
    await session.start()

    css = (session.build_dir() / THEME_FILENAME).read_text()
    assert "#111111" in css and "#5b2ea6" not in css


async def test_start_survives_a_broken_profile_store(tmp_path):
    """Session start must not die from a brand-application failure. DesignProfileStore.load()
    itself downgrades every S3 exception to None, but this guards a second time in case
    that promise breaks for any reason -- it pairs with the same try/except in the
    hosting route (start_host)."""
    class _BoomProfiles:
        async def load(self):
            raise RuntimeError("s3 unreachable")

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder(), design_profiles=_BoomProfiles())

    await session.start()  # If it raises, the test fails on this line.

    assert session.status == "ready"


# ---- turn relay: status transitions + question ownership ----

async def test_send_message_relays_events_and_returns_to_ready(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="message", text="building..."),
                    AgentEvent(kind="done")])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["message", "done"]
    assert session.status == "ready"


async def test_send_message_sets_waiting_input_on_questions_event(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    builder.script([AgentEvent(kind="questions", payload=payload)])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["questions"]
    assert session.status == "waiting_input"


async def test_send_answers_consumes_pending_interrupt_id(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    builder.script([AgentEvent(kind="questions", payload=payload)])
    [ev async for ev in session.send_message("go")]

    assert await session.send_answers({"1": "A"}) is True
    assert builder.answer_calls == [("iid-1", {"1": "A"})]
    assert session.status == "building"
    assert await session.send_answers({"1": "B"}) is False   # consumed


async def test_send_answers_false_when_nothing_pending(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    assert await session.send_answers({"1": "A"}) is False
    assert builder.answer_calls == []


# ---- Completion declaration: state transition + handoff record ----

async def test_build_complete_sets_the_complete_status(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "complete"


async def test_the_done_after_a_completion_does_not_revert_to_ready(tmp_path):
    """done always follows build_complete (run()'s terminal held discipline).
    If the done branch flipped status back to ready, the whole _DEAD_STATUSES
    mechanism would be defeated — hosting would return 409 again and no improvement session could be opened."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "complete"      # NOT "ready"


async def test_build_complete_writes_the_handoff(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event("할 일 앱을 만들었다", "다크 모드"),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    saved = json.loads(s3.blobs[HANDOFF_KEY])
    assert saved["summary"] == "할 일 앱을 만들었다"
    assert saved["remaining"] == "다크 모드"
    assert saved["completed_at"]            # ISO 8601 timestamp


async def test_the_build_complete_event_still_reaches_the_consumer(tmp_path):
    """If the observer swallows the event, the frontend cannot draw the completion card."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in events] == ["build_complete", "done"]


async def test_a_malformed_completion_payload_is_ignored(tmp_path):
    """Same fail-soft discipline as _interrupt_id_from — a broken payload is downgraded
    to being ignored rather than raising, and the idle timer cleans up as usual."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="build_complete", payload="{not json"),
                    AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"        # not treated as complete
    assert HANDOFF_KEY not in s3.blobs
    assert [e.kind for e in events] == ["build_complete", "done"]


async def test_a_completion_payload_without_a_summary_is_ignored(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="build_complete", payload=json.dumps({})),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "ready"
    assert HANDOFF_KEY not in s3.blobs


async def test_a_handoff_write_failure_does_not_fail_the_session(tmp_path):
    """An S3 failure must not make a finished build look failed.

    If _write_handoff's exception were not swallowed, send_message's except Exception
    would catch it and go to status="failed" + slot release (session.py:191-200) — the
    exact opposite of the decision "completion proceeds even if handoff fails".
    """
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    async def boom(key, content):
        if key == HANDOFF_KEY:
            raise RuntimeError("s3 down")
        return None

    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()
    s3.put = boom   # type: ignore[method-assign]

    builder.script([_complete_event(), AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert session.status == "complete"                  # NOT "failed"
    assert sem.snapshot()["active_builds"] == 1          # the slot was not released
    assert [e.kind for e in events] == ["build_complete", "done"]


# ---- mid-turn failure: the slot must not be burned permanently ----

class RaisingBuilder(FakeBuilder):
    """A builder whose run() dies mid-stream -- e.g. the claude subprocess
    died or hit a transport error. Yields whatever was scripted first, then
    raises instead of completing normally."""

    async def run(self, text: str):
        self.queries.append(text)
        for ev in self._script:
            yield ev
        raise RuntimeError("claude subprocess died")


async def test_send_message_mid_turn_raise_releases_the_slot(tmp_path):
    """Regression: nothing else releases on this path -- the route's retry
    logic just pops the dict entry, discarding the session object outright.
    Without a release inside send_message's except, the slot is gone until
    process restart."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = RaisingBuilder()
    sem = BuildSemaphore(max_concurrent=1)
    assert sem.try_acquire() is True          # route acquires before start()
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([AgentEvent(kind="message", text="building...")])
    with pytest.raises(RuntimeError):
        [ev async for ev in session.send_message("go")]

    assert session.status == "failed"
    assert sem.snapshot()["active_builds"] == 0   # slot returned, not wedged


async def test_send_message_mid_turn_raise_then_close_releases_only_once(tmp_path):
    """The route never gets a chance to call close() on this exact session
    (it evicts it from the dict), but if something DID call close() after a
    mid-turn failure -- or the idle timer fires first -- it must not release
    a slot a second time. A second release would wrongly free a slot held by
    some OTHER session, which is worse than the original bug."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = RaisingBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True   # this session's slot
    assert sem.try_acquire() is True   # another team's slot -- must survive
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([])
    with pytest.raises(RuntimeError):
        [ev async for ev in session.send_message("go")]

    await session.close()

    assert sem.snapshot()["active_builds"] == 1   # only the other holder's slot remains


async def test_idle_timer_after_mid_turn_raise_releases_only_once(tmp_path):
    """The idle timer re-arms on every send_message and still fires after a
    failed turn; it must honor the same already-released guard as close()."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = RaisingBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True   # this session's slot
    assert sem.try_acquire() is True   # another team's slot -- must survive
    session = _session(s3, tmp_path, builder, semaphore=sem, idle_seconds=0.05)
    await session.start()

    builder.script([])
    with pytest.raises(RuntimeError):
        [ev async for ev in session.send_message("go")]

    assert sem.snapshot()["active_builds"] == 1   # already released by the raise

    await asyncio.sleep(0.2)   # let the idle timer fire close()

    assert session.status == "closed"
    assert sem.snapshot()["active_builds"] == 1   # still just the other holder's slot


# ---- close(): disconnect + semaphore release, NOT a context wipe ----

async def test_close_disconnects_and_releases_the_slot(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=1)
    assert sem.try_acquire() is True          # route acquires before start()
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    await session.close()

    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 0
    assert session.status == "closed"


async def test_close_keeps_the_build_directory_and_session_id(tmp_path):
    """Closing must NOT reset context: the transcript id and the built files
    are what a later resume stands on."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    (session.build_dir() / "prototype").mkdir(parents=True, exist_ok=True)
    (session.build_dir() / "prototype" / "app.js").write_text("x", encoding="utf-8")

    await session.close()

    assert (session.build_dir() / "prototype" / "app.js").is_file()
    assert SESSION_KEY in s3.blobs


async def test_close_is_idempotent_and_releases_only_once(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    sem.try_acquire()
    sem.try_acquire()
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    await session.close()
    await session.close()

    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 1   # the OTHER holder still counts


async def test_close_releases_the_slot_even_if_disconnect_fails(tmp_path):
    """A wedged subprocess must not permanently consume a build slot."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class BoomBuilder(FakeBuilder):
        async def disconnect(self):
            raise RuntimeError("subprocess wedged")

    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    session = _session(s3, tmp_path, BoomBuilder(), semaphore=sem)
    await session.start()

    await session.close()      # must NOT raise

    assert sem.snapshot()["active_builds"] == 0
    assert session.status == "failed"


# ---- idle timer ----

async def test_idle_timer_auto_closes_and_frees_the_slot(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    session = _session(s3, tmp_path, builder, semaphore=sem, idle_seconds=0.05)

    await session.start()
    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 0


async def test_idle_timer_resets_on_send_message(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.1)
    await session.start()

    await asyncio.sleep(0.06)
    builder.script([AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.06)
    assert session.status == "ready"      # would be "closed" without the reset

    await asyncio.sleep(0.12)
    assert session.status == "closed"


# ---- Idle timer: "since the last sign of life", not "since entering the turn" ----

async def test_a_long_turn_is_not_killed_while_events_still_flow(tmp_path):
    """The old timer was re-armed only on turn entry — build turns longer than 30 minutes
    died mid-progress. The session must stay alive while events are flowing."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class SlowBuilder(FakeBuilder):
        async def run(self, text: str):
            self.queries.append(text)
            # Total duration is longer than the idle budget (0.1), but each gap is shorter.
            for _ in range(4):
                await asyncio.sleep(0.06)
                yield AgentEvent(kind="status", text="working")
            yield AgentEvent(kind="done")

    builder = SlowBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.1)
    await session.start()

    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"          # not closed by timeout
    assert [e.kind for e in events][-1] == "done"


async def test_the_idle_budget_restarts_when_a_question_is_relayed(tmp_path):
    """If the user deliberated for a long time with a question card open, the session closed and
    the answer submission returned 409. The budget must restart from the moment the card appears."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.15)
    await session.start()

    await asyncio.sleep(0.1)      # the question arrives after most of the budget is spent
    builder.script([AgentEvent(kind="questions", payload=json.dumps(
        {"interrupt_id": "iid-1", "questions": {"questions": []}}))])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.1)      # without re-arming it would already have closed here

    assert session.status == "waiting_input"
    assert builder.disconnect_calls == 0


# ---- After the completion declaration the session closes itself ----

async def test_a_completed_session_closes_itself(tmp_path, monkeypatch):
    """The backend owns session shutdown — the contrast with the frontend calling
    DELETE /session is the point. The slot is reclaimed even on refresh or tab close."""
    import aipds.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True   # this session's slot
    assert sem.try_acquire() is True   # another team's slot -- must survive
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 1   # only the other team's slot remains


async def test_the_done_after_a_completion_does_not_extend_the_grace(tmp_path, monkeypatch):
    """If the delay were a caller argument, done would reset it to the default 30 minutes and
    the session would not close. Since done always follows build_complete, this is not a
    possibility but certain behavior — deriving the delay from state removes that window."""
    import aipds.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    # The default idle is effectively infinite -- if the session closes, it is due to the grace period.
    session = _session(s3, tmp_path, builder, idle_seconds=3600)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"


async def test_a_completed_session_releases_its_slot_exactly_once(tmp_path, monkeypatch):
    """Even if the user's DELETE /session and the grace-period close overlap, release happens once.
    BuildSemaphore.release() only clamps at 0 and cannot detect over-release,
    so releasing twice gives away another session's slot for free."""
    import aipds.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is True
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await session.close()          # the user closes first
    await asyncio.sleep(0.2)       # then the grace period expires

    assert sem.snapshot()["active_builds"] == 1
    assert builder.disconnect_calls == 1


async def test_send_message_after_completion_is_refused_without_raising(tmp_path):
    """Today _DEAD_STATUSES in routes/prototypes.py blocks this call with a 404
    before it arrives, but the session object itself must also reject turns after
    completion -- if a caller bypasses the route (tests, some future entry point),
    the turn relay would run as is, flip status back to "building", and trample the completed state.

    It must yield an error event rather than raise: the `except Exception` at the end
    of send_message turns a mid-turn failure into a "failed" session and releases the
    build slot (equivalent to reclassifying a normal completion as a failure). A completed
    session is a normal end that has finished its work, so this guard must return an error
    like an ordinary error turn, not raise, and leave the state untouched."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]
    assert session.status == "complete"

    run_calls_before = len(builder.queries)
    events = [ev async for ev in session.send_message("한 번 더 해줘")]

    assert [e.kind for e in events] == ["error"]
    assert session.status == "complete"                    # does not revert
    assert len(builder.queries) == run_calls_before         # run() was not called again
    assert sem.snapshot()["active_builds"] == 1             # the slot was not released


# ---- first_prompt(): directives, now without the /workspace path ----

def test_first_prompt_covers_the_build_directives(tmp_path):
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())

    prompt = session.first_prompt()

    # What this prompt owns is only the session flow and this build's runtime values.
    # The shared technical contract (artifact location, README, Bedrock credentials,
    # model ID, basePath, sampling parameters) moved to `proto-config/CLAUDE.md` —
    # test_proto_prompts.test_the_plan_prompt_does_not_restate_the_shared_contract and
    # test_build_agent_contract pin that division from both sides.
    assert SPEC_KEY in prompt
    assert "AskUserQuestion" in prompt
    assert f"/api/proto/{PROJECT_ID}/{SLUG}/" in prompt


def test_first_prompt_no_longer_names_the_vm_absolute_path(tmp_path):
    """The VM's /workspace/ mount is gone; cwd is the build directory."""
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())
    assert "/workspace/" not in session.first_prompt()


def test_first_prompt_asks_for_a_plan_before_any_building(tmp_path):
    """The first turn must stop after planning and put the plan up for review
    via AskUserQuestion, instead of reading the spec and building straight
    through. Without an explicit stop the agent treats the plan as a preamble
    and keeps going, so there is nothing left to approve."""
    prompt = _session(FakeS3Store(), tmp_path, FakeBuilder()).first_prompt()

    plan_at = prompt.find("계획")
    build_at = prompt.find("빌드")
    assert plan_at != -1, "no planning instruction at all"
    # The planning instruction has to come before the build instruction, or the
    # agent reads "build it" first and plans as an afterthought.
    assert plan_at < build_at, prompt

    # The stop must be explicit and tied to the approval tool.
    assert "AskUserQuestion" in prompt
    assert "승인" in prompt or "실행할지" in prompt


def test_first_prompt_forbids_writing_files_before_approval(tmp_path):
    """`bypassPermissions` auto-approves Write/Edit -- nothing outside the
    prompt can stop the agent from scaffolding the whole prototype while it
    'plans'. The no-write-yet rule has to be stated in the prompt itself."""
    prompt = _session(FakeS3Store(), tmp_path, FakeBuilder()).first_prompt()
    assert "Write" in prompt or "파일을 만들지" in prompt or "생성하지" in prompt


# ---- first_prompt() on a RESUMED session ----

async def _started(tmp_path, *, saved_session_id=None):
    """A session that has been through start(), optionally resuming a saved id."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    if saved_session_id is not None:
        s3.blobs[SESSION_KEY] = json.dumps({"session_id": saved_session_id})
    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    return session


def _build_output(session) -> None:
    """Create a state where artifacts exist under `prototype/` — the shape of a completed build.

    `start()` plants only the spec .md, so without this the build tree is empty, and
    the resume/improvement prompts split off to "rebuild it" (has_build_output).
    """
    proto = session.build_dir() / "prototype"
    proto.mkdir(parents=True, exist_ok=True)
    (proto / "package.json").write_text("{}", encoding="utf-8")


async def test_a_resumed_session_asks_what_to_do_instead_of_replanning(tmp_path):
    """Reopening a session (backend redeploy, or a closed/failed session) re-fires
    the __first__ sentinel, so first_prompt() goes out AGAIN -- but now the agent
    has the full prior transcript. Re-sending "plan it, don't build yet" tells a
    half-finished build to go back to square one. Ask what to continue with."""
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    assert session._test_resume_calls == [True]  # sanity: this IS the resume path

    prompt = session.first_prompt()

    # It must NOT re-issue the from-scratch planning order.
    assert "이번 턴에서는 계획만 세우고" not in prompt
    # It must ask, and wait, rather than pick up building on its own.
    assert "AskUserQuestion" in prompt
    assert "이어서" in prompt or "이전" in prompt


async def test_a_resumed_session_does_not_start_building_on_its_own(tmp_path):
    """The complaint that started this: every resume just started building."""
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    # An ordinary resume with artifacts still present. Without them it goes to the "rebuild it"
    # branch, whose purpose is to carry build instructions, so it is not the subject of this assertion.
    _build_output(session)
    prompt = session.first_prompt()
    # No bare build order anywhere in the resume prompt.
    assert "빌드해줘" not in prompt
    assert "빌드를 시작해줘" not in prompt


# ---- Resume after the artifacts are gone: rebuild, do not search ----

async def test_resume_without_local_output_says_rebuild_not_search(tmp_path):
    """The S3 session is alive but the local `prototype/` is missing — this really
    happens after an instance replacement or a reset.

    The agent's context (transcript) still says the code it wrote exists, so unless
    it is told the state, it scans the filesystem for code that is not there. Observed:
    on a reset prototype it searched from `/opt/aipds/protos/...` to `/opt/aipds/frontend`
    and even other prototype directories, burning more than 19 seconds. That search can
    never succeed — the tree was deleted. It has to be told to rebuild.
    """
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    assert session._test_resume_calls == [True]      # sanity: this is the resume path

    prompt = session.first_prompt()

    # The prompt must state that the artifacts are missing.
    assert "prototype/" in prompt
    # And the instruction must be to rebuild, not to search.
    assert "처음부터" in prompt or "다시 만들" in prompt
    # It must not tell the agent to look around — that was the symptom of this bug.
    assert "찾아" not in prompt


async def test_resume_with_local_output_keeps_the_continue_prompt(tmp_path):
    """An ordinary resume with live artifacts must keep the existing wording — if the
    rebuild instruction leaks here, it throws a finished build back to scratch."""
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    _build_output(session)

    prompt = session.first_prompt()

    assert "이어서" in prompt or "이전" in prompt
    assert "처음부터" not in prompt and "다시 만들" not in prompt


async def test_handoff_without_local_output_says_rebuild_too(tmp_path):
    """The handoff branch has the same hole: it asserts "the build is already complete"
    and tells the agent to look at `prototype/`, but that directory may not exist."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    prompt = session.first_prompt()

    assert "처음부터" in prompt or "다시 만들" in prompt
    # It must not tell the agent to look at something that does not exist.
    assert "살펴보고 현재 상태를 파악" not in prompt


async def test_handoff_with_local_output_keeps_the_improve_prompt(tmp_path):
    """An improvement session with artifacts carries the summary and has the agent look at the current state."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    _build_output(session)
    prompt = session.first_prompt()

    assert "할 일 앱을 만들었다" in prompt
    assert "처음부터" not in prompt and "다시 만들" not in prompt


async def test_a_fresh_session_still_gets_the_planning_prompt(tmp_path):
    """The resume branch must not swallow the first-build behaviour."""
    session = await _started(tmp_path)
    assert session._test_resume_calls == [False]
    prompt = session.first_prompt()
    assert "이번 턴에서는 계획만 세우고" in prompt


def test_first_prompt_before_start_assumes_a_fresh_build(tmp_path):
    """first_prompt() is reachable without start() (tests, and any future caller);
    default to the safer planning prompt rather than raising."""
    prompt = _session(FakeS3Store(), tmp_path, FakeBuilder()).first_prompt()
    assert "이번 턴에서는 계획만 세우고" in prompt


async def test_the_plan_prompt_asks_for_an_explicit_completion_declaration(tmp_path):
    """The completion declaration is a tool call, but the prompt is the only place that tells the agent to call it."""
    session = await _started(tmp_path)
    prompt = session.first_prompt()
    assert "build_complete" in prompt


# ---- Completion marker: an improvement session treats the current tree as "the last completion" ----

def _seed_built_tree(tmp_path) -> Path:
    build_dir = tmp_path / "protos" / PROJECT_ID / SLUG
    (build_dir / "prototype").mkdir(parents=True)
    (build_dir / "prototype" / "index.html").write_text("built")
    return build_dir


async def test_a_handoff_start_marks_the_current_tree_as_the_last_completion(tmp_path):
    """Prototypes completed before the marker existed also get the no-change rejection from the first
    improvement session (proto/tools.py). If a handoff exists, the current tree is the last completion's tree."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})
    build_dir = _seed_built_tree(tmp_path)

    await _session(s3, tmp_path, FakeBuilder()).start()

    assert completed_digest(build_dir) == output_digest(build_dir)


async def test_a_resumed_session_keeps_its_unfinished_work_completable(tmp_path):
    """A session that died without completing must carry the files it already wrote into the
    completion — using the current tree as the baseline would reject that completion as no-change."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    build_dir = _seed_built_tree(tmp_path)

    await _session(s3, tmp_path, FakeBuilder()).start()

    assert completed_digest(build_dir) is None


# ---- Improvement session: with a handoff, a new session_id + summary injection ----

async def test_a_handoff_starts_a_fresh_session_id(tmp_path):
    """Keeps improvement work from carrying the full transcript. A full resume loads
    the entire build context even for a request to change one button color."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    old_id = "99999999-8888-7777-6666-555555555555"
    s3.blobs[SESSION_KEY] = json.dumps({"session_id": old_id})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱", "remaining": "다크 모드"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [False]      # not a resume
    saved = json.loads(s3.blobs[SESSION_KEY])["session_id"]
    assert saved != old_id                            # switched to a new id


async def test_a_handoff_survives_start_and_is_deleted_once_the_first_turn_runs(tmp_path):
    """If a handoff that was used once remains, the next start also gets the improvement prompt and
    cannot continue session B's conversation. But it must not be deleted in start() -- if the session
    closes before a turn runs, the saved new id has no transcript and no handoff, so the next start
    would --resume a nonexistent conversation (observed: "No conversation found", permanently)."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})
    builder = FakeBuilder()

    session = _session(s3, tmp_path, builder)
    await session.start()
    assert HANDOFF_KEY in s3.blobs           # no turn has run yet

    builder.script([AgentEvent(kind="message", payload="hi"),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert HANDOFF_KEY not in s3.blobs


async def test_a_session_closed_before_its_first_turn_leaves_the_next_start_a_handoff(tmp_path):
    """Reproduction of the failure above: start() -> close() (with no turn) -> start(). The second start
    must be a handoff again, not a resume."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})

    first = _session(s3, tmp_path, FakeBuilder())
    await first.start()
    await first.close()

    second = _session(s3, tmp_path, FakeBuilder())
    await second.start()

    assert second._prompt_kind == "handoff"
    assert second._test_resume_calls == [False]


async def test_an_error_as_the_first_event_does_not_consume_the_handoff(tmp_path):
    """A turn that never even connected did not create a transcript for the new id."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="error", payload="boom")])
    [ev async for ev in session.send_message("go")]

    assert HANDOFF_KEY in s3.blobs


async def test_the_handoff_prompt_carries_the_summary(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드가 남았다"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    # A state where there really are artifacts to improve — without them, "rebuild it" rather than
    # the summary is the correct output (test_handoff_without_local_output_says_rebuild_too).
    _build_output(session)
    prompt = session.first_prompt()

    assert "할 일 앱을 만들었다" in prompt
    assert "다크 모드가 남았다" in prompt
    # Not an instruction to plan from scratch.
    assert "이번 턴에서는 계획만 세우고" not in prompt
    # It must ask rather than start on its own.
    assert "AskUserQuestion" in prompt


async def test_a_session_that_died_without_declaring_completion_still_resumes(tmp_path):
    """A session that died without a completion declaration (idle timeout, backend restart) is still
    correctly a real resume. The two paths represent different events."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    # no handoff

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]
    assert "이어서" in session.first_prompt() or "이전" in session.first_prompt()


async def test_a_malformed_handoff_falls_back_to_resume(tmp_path):
    """A broken handoff must not block the improvement path — a full resume is heavy but
    an accurate degradation."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = "{not json"

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]


async def test_a_handoff_without_a_saved_session_id_still_plans(tmp_path):
    """A combination of a handoff with no session.json (e.g. a partial failure during initialization)
    falls back to fresh — there was never a session to continue."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "뭔가", "remaining": ""})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [False]
    assert "이번 턴에서는 계획만 세우고" in session.first_prompt()


# ---- purge_session_state ----

async def test_purge_session_state_removes_session_transcript_and_bundle():
    """Everything under prototypes/{slug}/ that this module owns. The bundle/
    prefix is legacy (the deleted MicroVM wrote it) but old projects still
    carry one, so purge has to cover it.

    The sibling is deliberately `{SLUG}-2`, a PREFIX-COLLIDING slug, and that
    choice is the whole isolation guarantee. `delete_prefix` is a string-prefix
    match, so dropping the trailing slash from `prototypes/{slug}/` also
    matches `prototypes/{slug}-2/...` -- and `todo-app` / `todo-app-2` is the
    normal shape of an iterated workshop prototype, not an exotic name. With a
    non-colliding sibling (`other`) that mutation went unnoticed by 90 tests
    while it deleted a neighbour's real survey answers, so this test seeds the
    one thing that cannot be recovered: a submitted response."""
    from aipds.proto.session import purge_session_state
    s3 = FakeS3Store()
    s3.blobs[f"prototypes/{SLUG}/session.json"] = '{"session_id": "x"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/00000001.jsonl"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    # Must survive: the NEXT ITERATION of this same prototype, whose slug
    # shares this one's entire string as a prefix.
    sibling = f"{SLUG}-2"
    s3.blobs[f"prototypes/{sibling}/session.json"] = '{"session_id": "y"}'
    s3.blobs[f"prototypes/{sibling}/survey/responses/r1.json"] = \
        '{"response_id": "r1", "submitted_at": "2026-01-01T00:00:00Z", "answers": {}}'

    await purge_session_state(s3, SLUG)

    assert [k for k in s3.blobs if k.startswith(f"prototypes/{SLUG}/")] == []
    assert f"prototypes/{sibling}/session.json" in s3.blobs
    # A real respondent's answer, in the prototype next door.
    assert f"prototypes/{sibling}/survey/responses/r1.json" in s3.blobs


async def test_purge_session_state_leaves_the_spec_alone():
    """The spec lives under aiplc-docs/, not prototypes/{slug}/ — but assert it
    explicitly: deleting it would remove the card from the list entirely
    (routes/prototypes.py scans specs to build the list), turning a reset into
    a disappearance."""
    from aipds.proto.session import purge_session_state
    s3 = FakeS3Store()
    spec = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
    s3.blobs[spec] = "# PROTOTYPE"

    await purge_session_state(s3, SLUG)

    assert s3.blobs[spec] == "# PROTOTYPE"


async def test_purge_session_state_is_idempotent():
    """Deleting nothing is success, not a raise: most prototypes have no
    session state, and the reset route retries after a partial failure — so the
    second pass, which finds even less, must not turn a converged reset into a
    502."""
    from aipds.proto.session import purge_session_state
    s3 = FakeS3Store()
    await purge_session_state(s3, SLUG)
    await purge_session_state(s3, SLUG)


def test_first_prompt_is_english_for_an_english_project(tmp_path):
    """An English project's opening turn must be in English.

    The brake ("only make a plan, do not build") must be present in both languages —
    if the English version drops that instruction, the build starts without approval.
    """
    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=FakeS3Store(),
        build_root=tmp_path / "protos",
        builder_factory=lambda sid, resume: FakeBuilder(),
        semaphore=BuildSemaphore(max_concurrent=2),
        language="en",
    )
    prompt = session.first_prompt()
    assert "do not start building" in prompt.lower()
    assert "AskUserQuestion" in prompt
    assert not any("가" <= c <= "힣" for c in prompt)


def test_first_prompt_defaults_to_korean(tmp_path):
    # Callers that pass no language (old code, tests) keep the existing behavior.
    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=FakeS3Store(),
        build_root=tmp_path / "protos",
        builder_factory=lambda sid, resume: FakeBuilder(),
        semaphore=BuildSemaphore(max_concurrent=2),
    )
    assert "빌드는 시작하지 마" in session.first_prompt()


# ---- Send the user's request along with the opening turn ----
#
# Someone who pressed "수정하기" (Edit) already knows what they want to fix, but the opening
# prompt asked them back, and while that question was up the input box was disabled, so they
# could not even type their own request (rationale in proto/prompts.handoff_prompt). So **the
# session's first message is the opening turn**, and if that message is a user request, it is
# carried in the opening prompt.
#
# The point is to base the decision on `opened`, not on a UI flag: "the opening turn has not run
# yet" is a fact the session knows, and if the frontend guesses, it goes wrong on refresh, races,
# and already-open sessions.

async def test_a_fresh_session_has_not_opened_yet(tmp_path):
    session = await _started(tmp_path)
    assert session.opened is False


async def test_the_first_message_marks_the_session_opened(tmp_path):
    session = await _started(tmp_path)
    session._builder.script([AgentEvent(kind="done")])

    async for _ in session.send_message("아무 말"):
        pass

    assert session.opened is True


async def test_a_handoff_session_carries_the_users_request_into_its_opening_turn(tmp_path):
    """An improvement session starts with a **new session_id**, so there is no transcript. Sending only
    the request would start the agent without knowing what the previous build left behind."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "투두 앱을 만들었다", "remaining": "정렬"}, ensure_ascii=False)
    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    _build_output(session)   # without artifacts it is the "rebuild it" branch (test below)

    prompt = session.first_prompt(request="장바구니 버튼을 오른쪽 위로")

    assert "장바구니 버튼을 오른쪽 위로" in prompt
    assert "투두 앱을 만들었다" in prompt      # the new session has no other context
    assert "AskUserQuestion" not in prompt     # does not ask back


async def test_a_request_survives_the_output_is_gone_branch(tmp_path):
    """If the artifacts are gone, a rebuild rather than an improvement is correct (`_missing_output_prompt`),
    and that branch keeps asking back — you cannot fix what does not exist.

    Even so, what the user typed must be carried along. Dropping it silently leaves that request
    nowhere, and the user has no way of knowing that what they said was ignored."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "투두 앱", "remaining": "정렬"}, ensure_ascii=False)
    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    # Does not create artifacts.

    prompt = session.first_prompt(request="장바구니 버튼을 오른쪽 위로")

    assert "장바구니 버튼을 오른쪽 위로" in prompt
    assert "찾지 마" in prompt   # the rebuild branch remains intact


async def test_no_request_keeps_the_asking_behaviour(tmp_path):
    """On auto-open (`__first__`) the user has said nothing — asking at that point is
    correct behavior, and this change must not remove it."""
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    _build_output(session)
    assert "AskUserQuestion" in session.first_prompt()


async def test_a_resumed_session_carries_the_request_too(tmp_path):
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    _build_output(session)

    prompt = session.first_prompt(request="로그인 화면부터 마쳐줘")

    assert "로그인 화면부터 마쳐줘" in prompt
    assert "AskUserQuestion" not in prompt


# ---- A build turn is a server job of the session (aipds/turn_job.py) ----

class GatedBuilder(FakeBuilder):
    """A builder that stops after asking a question until the answer arrives, then continues to the end once
    answered — like the real thing, **one turn continues past the question** (proto/builder.py)."""

    def __init__(self):
        super().__init__()
        self.answered = asyncio.Event()

    async def run(self, text: str):
        self.queries.append(text)
        yield AgentEvent(kind="questions", payload=json.dumps(
            {"interrupt_id": "iid-1", "questions": {"name": "q"}}))
        await self.answered.wait()
        yield AgentEvent(kind="message", text="반영했습니다")
        yield AgentEvent(kind="done")

    async def submit_answers(self, interrupt_id, answers):
        await super().submit_answers(interrupt_id, answers)
        self.answered.set()
        return True


async def test_an_answer_is_recorded_in_the_turn_between_the_question_and_the_reply(tmp_path):
    """This is why a reattached screen restores the answer bubble after the question card and closes the card."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = GatedBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()
    job = session.turns.start("message", lambda: session.send_message("go"))
    await asyncio.sleep(0.01)
    assert session.status == "waiting_input"
    assert await session.send_answers({"1": "A"}) is True
    await job.task
    kinds = [e.kind for _, e in job.log.events_after(0)]
    assert kinds == ["questions", "answers", "message", "done"]
    answers = job.log.events_after(0)[1][1]
    assert json.loads(answers.payload) == {"answers": {"1": "A"}}


async def test_a_turn_nobody_watches_still_reaches_completion(tmp_path):
    """Even if the watching screen leaves, the turn runs to the end and the session knows it completed — while the
    consumer was itself the executor, a refresh meant nobody read build_complete and the session stayed stuck
    at "building"."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    builder.script([AgentEvent(kind="message", text="만드는 중"),
                    _complete_event(), AgentEvent(kind="done")])
    session = _session(s3, tmp_path, builder)
    await session.start()
    job = session.turns.start("message", lambda: session.send_message("go"))
    await job.task
    assert session.status == "complete"
    assert job.state == "done"


async def test_close_lets_a_finishing_turn_end_on_its_own(tmp_path):
    """When closing on the completion grace period, all that is left in the turn is `done` — cancelling immediately
    appends "중단됨" (Stopped) after the completion declaration. Once the builder goes down the turn ends by itself, so wait briefly."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    builder.script([AgentEvent(kind="message", text="끝"), AgentEvent(kind="done")])
    session = _session(s3, tmp_path, builder)
    await session.start()
    job = session.turns.start("message", lambda: session.send_message("go"))
    await asyncio.sleep(0)          # closes after the turn has started running
    await session.close()
    assert job.state == "done"
    assert all(e.text != "turn interrupted" for _, e in job.log.events_after(0))


# ---- Change history: changes after the spec accumulate across sessions ----

async def test_build_complete_appends_to_the_change_history(tmp_path):
    """handoff.json is a one-shot record of only the previous session and disappears once consumed. The history
    must accumulate without being erased so the Nth improvement session knows the changes of the (N-1)th."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(changes="로그인 화면 제외"),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    history = s3.blobs[HISTORY_KEY]
    assert history.startswith("# ")                 # header appears once
    assert "로그인 화면 제외" in history


async def test_the_change_history_accumulates_across_sessions(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    for changes in ("A 기능 제거", "B 기능 추가"):
        builder = FakeBuilder()
        session = _session(s3, tmp_path, builder)
        await session.start()
        builder.script([_complete_event(changes=changes), AgentEvent(kind="done")])
        [ev async for ev in session.send_message("go")]

    history = s3.blobs[HISTORY_KEY]
    assert history.index("A 기능 제거") < history.index("B 기능 추가")
    assert history.count("# 수정 이력") == 1


async def test_a_change_history_write_failure_does_not_fail_the_session(tmp_path):
    """Same discipline as handoff -- a history write failure does not make a finished build look failed."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    real_put = s3.put

    async def put(key, *a, **kw):
        if key == HISTORY_KEY:
            raise RuntimeError("s3 down")
        return await real_put(key, *a, **kw)

    s3.put = put
    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "complete"


async def test_start_plants_the_history_and_the_prompt_says_it_wins(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "요약", "remaining": "없음"})
    s3.blobs[HISTORY_KEY] = "# 수정 이력\n\n## x\n\n**변경**\nA 기능 제거\n"

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    _build_output(session)
    prompt = session.first_prompt()

    assert (session.build_dir() / HISTORY_LOCAL).read_text(
        encoding="utf-8").endswith("A 기능 제거\n")
    assert HISTORY_LOCAL in prompt
    assert "이겨" in prompt


async def test_rebuild_from_scratch_also_points_at_the_history(tmp_path):
    """A rebuild after the artifacts are gone cannot restore previous changes from the spec alone."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "요약", "remaining": "없음"})
    s3.blobs[HISTORY_KEY] = "# 수정 이력\n"

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    prompt = session.first_prompt()        # no local artifacts

    assert HISTORY_LOCAL in prompt


async def test_without_a_history_the_prompt_mentions_none(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "요약", "remaining": "없음"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    _build_output(session)

    assert "change-history" not in session.first_prompt()
    assert not (session.build_dir() / HISTORY_LOCAL).exists()


async def test_reset_removes_the_change_history_but_not_the_spec(tmp_path):
    """The history lives next to the spec -- if a reset deletes the directory by prefix, the card disappears."""
    from aipds.proto.session import purge_session_state
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[HISTORY_KEY] = "# 수정 이력\n"
    s3.blobs[HANDOFF_KEY] = "{}"

    await purge_session_state(s3, SLUG)

    assert HISTORY_KEY not in s3.blobs
    assert HANDOFF_KEY not in s3.blobs
    assert SPEC_KEY in s3.blobs


# ---- WebFetch allowance: only from what the user wrote ----

async def test_only_the_users_own_words_open_a_site(tmp_path):
    """The opening turn's `text` is the prompt wrapped by the server — it contains URLs from the
    model-written handoff summary, so the allowance is extracted only from `user_text`."""
    builder = FakeBuilder()
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, builder)
    await session.start()

    [ev async for ev in session.send_message(
        "요약: https://summary.example.net 을 참고했다\n내 메시지: https://store.example.com/ranks",
        user_text="https://store.example.com/ranks 판매자 정보는?")]

    assert builder.fetch_hosts == {"store.example.com"}


async def test_a_url_typed_into_a_question_card_also_opens(tmp_path):
    builder = FakeBuilder()
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, builder)
    await session.start()
    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    builder.script([AgentEvent(kind="questions", payload=payload)])
    [ev async for ev in session.send_message("go")]

    await session.send_answers({"1": "D: https://shop.example.org/item/1 이거 참고"})

    assert builder.fetch_hosts == {"shop.example.org"}
