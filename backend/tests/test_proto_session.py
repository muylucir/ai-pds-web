# backend/tests/test_proto_session.py — PrototypeSession over an in-process
# builder. VM scenarios (boot/token-mint/stop) are gone with vm.py; the
# question-ownership, status-transition, idle-timer and close-idempotency
# scenarios carried over unchanged in intent.
from __future__ import annotations

import asyncio
import json

import pytest

from pathfinder.models import AgentEvent
from pathfinder.proto.limits import BuildSemaphore
from pathfinder.proto.session import PrototypeSession

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
PROJECT_ID = "proj-1"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
SESSION_KEY = f"prototypes/{SLUG}/session.json"


class FakeBuilder:
    def __init__(self):
        self.queries: list[str] = []
        self.answer_calls: list[tuple[str, dict]] = []
        self.interrupt_calls = 0
        self.disconnect_calls = 0
        self.submit_result = True
        self._script: list[AgentEvent] = []

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


def _session(s3, tmp_path, builder, semaphore=None, idle_seconds=1800):
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


# ---- first_prompt(): directives, now without the /workspace path ----

def test_first_prompt_covers_the_build_directives(tmp_path):
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())

    prompt = session.first_prompt()

    assert SPEC_KEY in prompt
    assert "AskUserQuestion" in prompt
    assert "prototype/" in prompt
    assert "README" in prompt
    assert f"/api/proto/{PROJECT_ID}/{SLUG}/" in prompt
    assert "basePath" in prompt or "상대 경로" in prompt
    assert "Bedrock" in prompt
    assert "하드코딩" in prompt


def test_first_prompt_no_longer_names_the_vm_absolute_path(tmp_path):
    """The VM's /workspace/ mount is gone; cwd is the build directory."""
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())
    assert "/workspace/" not in session.first_prompt()
