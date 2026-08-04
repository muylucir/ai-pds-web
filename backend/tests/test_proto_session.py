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
HANDOFF_KEY = f"prototypes/{SLUG}/handoff.json"


def _complete_event(summary="할 일 앱", remaining="다크 모드"):
    return AgentEvent(kind="build_complete", payload=json.dumps(
        {"summary": summary, "remaining": remaining}, ensure_ascii=False))


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


# ---- 완료 선언: 상태 전이 + handoff 기록 ----

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
    """build_complete 다음에는 반드시 done이 온다(run()의 terminal held 규율).
    done 분기가 status를 ready로 되돌리면 _DEAD_STATUSES 기구 전체가
    무력해진다 — 호스팅이 다시 409가 되고 개선 세션을 열 수 없다."""
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
    assert saved["completed_at"]            # ISO 8601 타임스탬프


async def test_the_build_complete_event_still_reaches_the_consumer(tmp_path):
    """관찰이 이벤트를 삼키면 프론트가 완료 카드를 그릴 수 없다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in events] == ["build_complete", "done"]


async def test_a_malformed_completion_payload_is_ignored(tmp_path):
    """_interrupt_id_from과 같은 fail-soft 규율 — 깨진 payload는 예외가 아니라
    무시로 강등되고, 유휴 타이머가 평소대로 정리한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="build_complete", payload="{not json"),
                    AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"        # 완료로 처리되지 않는다
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
    """S3 실패가 완성된 빌드를 실패로 보이게 만들면 안 된다.

    _write_handoff의 예외를 삼키지 않으면 send_message의 except Exception이
    잡아 status="failed" + 슬롯 release로 간다(session.py:191-200) — "handoff
    실패에도 완료는 진행한다"는 결정과 정반대다.
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
    assert sem.snapshot()["active_builds"] == 1          # 슬롯을 풀지 않았다
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


# ---- 유휴 타이머: "턴 진입 이후"가 아니라 "마지막 생존 신호 이후" ----

async def test_a_long_turn_is_not_killed_while_events_still_flow(tmp_path):
    """종전 타이머는 턴 진입에서만 재무장됐다 — 30분을 넘는 빌드 턴은 진행
    중에 죽었다. 이벤트가 흐르는 동안은 살아 있어야 한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class SlowBuilder(FakeBuilder):
        async def run(self, text: str):
            self.queries.append(text)
            # 유휴 예산(0.1)보다 총 소요가 길지만, 각 간격은 그보다 짧다.
            for _ in range(4):
                await asyncio.sleep(0.06)
                yield AgentEvent(kind="status", text="working")
            yield AgentEvent(kind="done")

    builder = SlowBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.1)
    await session.start()

    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"          # 타임아웃으로 닫히지 않았다
    assert [e.kind for e in events][-1] == "done"


async def test_the_idle_budget_restarts_when_a_question_is_relayed(tmp_path):
    """질문 카드를 띄운 채 사용자가 오래 고민하면 세션이 닫히고, 답변 제출이
    409가 됐다. 카드가 뜬 순간부터 예산이 새로 시작해야 한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.15)
    await session.start()

    await asyncio.sleep(0.1)      # 예산의 대부분을 소진한 뒤 질문이 온다
    builder.script([AgentEvent(kind="questions", payload=json.dumps(
        {"interrupt_id": "iid-1", "questions": {"questions": []}}))])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.1)      # 재무장이 없었다면 여기서 이미 닫혔다

    assert session.status == "waiting_input"
    assert builder.disconnect_calls == 0


# ---- 완료 선언 뒤 세션이 스스로 닫힌다 ----

async def test_a_completed_session_closes_itself(tmp_path, monkeypatch):
    """세션 종료는 백엔드가 소유한다 — 프론트가 DELETE /session을 부르는
    방식과의 차이가 요점이다. 새로고침·탭 닫기에도 슬롯이 회수된다."""
    import pathfinder.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True   # 이 세션의 슬롯
    assert sem.try_acquire() is True   # 다른 팀의 슬롯 -- 살아남아야 한다
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 1   # 다른 팀 슬롯만 남는다


async def test_the_done_after_a_completion_does_not_extend_the_grace(tmp_path, monkeypatch):
    """지연 값이 호출자 인자였다면 done이 기본 30분으로 되돌려 세션이 닫히지
    않는다. build_complete 다음에는 반드시 done이 오므로 이것은 가능성이
    아니라 확정된 동작이다 — 지연을 상태에서 파생시켜 그 창을 없앤다."""
    import pathfinder.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    # 기본 유휴는 사실상 무한 -- 세션이 닫힌다면 그것은 유예 때문이다.
    session = _session(s3, tmp_path, builder, idle_seconds=3600)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"


async def test_a_completed_session_releases_its_slot_exactly_once(tmp_path, monkeypatch):
    """사용자의 DELETE /session과 유예 종료가 겹쳐도 release는 한 번이다.
    BuildSemaphore.release()는 0에서 클램프할 뿐 과다 release를 감지하지
    못하므로, 두 번 풀면 다른 세션의 슬롯을 공짜로 내준다."""
    import pathfinder.proto.session as session_module
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

    await session.close()          # 사용자가 먼저 닫는다
    await asyncio.sleep(0.2)       # 그 다음 유예가 만료된다

    assert sem.snapshot()["active_builds"] == 1
    assert builder.disconnect_calls == 1


async def test_send_message_after_completion_is_refused_without_raising(tmp_path):
    """routes/prototypes.py의 _DEAD_STATUSES가 오늘은 이 호출 전에 404로
    막아주지만, 세션 객체 스스로도 완료 후 턴을 거부해야 한다 -- 라우트를
    우회하는 호출자(테스트, 미래의 다른 진입점)가 있으면 turn relay가 그대로
    돌아 status를 "building"으로 되돌리고 완료 상태를 짓뭉갤 수 있다.

    raise가 아니라 error 이벤트를 yield해야 한다: send_message 끝의
    `except Exception`은 mid-turn 실패를 세션 "failed"로 만들고 빌드 슬롯을
    풀어준다(정상적인 완료를 실패로 재분류하는 것과 같다). 완료된 세션은
    할 일을 다 마친 정상 종료이므로, 이 가드는 raise가 아니라 평범한 오류
    턴처럼 error를 돌려주고 상태를 그대로 둬야 한다."""
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
    assert session.status == "complete"                    # 되돌아가지 않는다
    assert len(builder.queries) == run_calls_before         # run()이 다시 불리지 않았다
    assert sem.snapshot()["active_builds"] == 1             # 슬롯을 풀지 않았다


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
    """`prototype/` 아래에 산출물이 있는 상태를 만든다 — 완료된 빌드의 모양.

    `start()`는 스펙 .md만 심으므로 이것 없이는 빌드 트리가 비어 있고,
    재개·개선 프롬프트가 "다시 만들어라"로 갈린다(has_build_output).
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
    # 산출물이 남아 있는 평범한 재개. 없으면 "다시 만들어라" 분기로 가는데,
    # 그쪽은 빌드 지시를 담는 것이 목적이라 이 단정의 대상이 아니다.
    _build_output(session)
    prompt = session.first_prompt()
    # No bare build order anywhere in the resume prompt.
    assert "빌드해줘" not in prompt
    assert "빌드를 시작해줘" not in prompt


# ---- 산출물이 사라진 뒤의 재개: 찾지 말고 다시 만든다 ----

async def test_resume_without_local_output_says_rebuild_not_search(tmp_path):
    """S3 세션은 살아 있는데 로컬 `prototype/`이 없는 상태 — 인스턴스 교체나
    리셋 뒤에 실제로 일어난다.

    에이전트의 맥락(트랜스크립트)에는 자기가 만든 코드가 있다고 남아 있으므로,
    상태를 알려주지 않으면 없는 코드를 찾아 파일시스템을 훑는다. 실측: 리셋된
    프로토타입에서 `/opt/pathfinder/protos/...`부터 `/opt/pathfinder/frontend`,
    다른 프로토타입 디렉토리까지 뒤지며 19초 이상을 태웠다. 그 탐색은 절대
    성공할 수 없다 — 트리가 삭제됐기 때문이다. 다시 만들라고 말해야 한다.
    """
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    assert session._test_resume_calls == [True]      # sanity: resume 경로다

    prompt = session.first_prompt()

    # 산출물이 없다는 사실을 프롬프트가 말해야 한다.
    assert "prototype/" in prompt
    # 그리고 찾는 게 아니라 다시 만드는 것이 지시여야 한다.
    assert "처음부터" in prompt or "다시 만들" in prompt
    # 찾아보라고 시키면 안 된다 — 그게 이 버그의 증상이었다.
    assert "찾아" not in prompt


async def test_resume_with_local_output_keeps_the_continue_prompt(tmp_path):
    """산출물이 살아 있는 평범한 재개는 기존 문구 그대로여야 한다 — 다시
    만들라는 지시가 여기로 새면 완성된 빌드를 처음부터 되돌린다."""
    session = await _started(tmp_path,
                             saved_session_id="99999999-8888-7777-6666-555555555555")
    _build_output(session)

    prompt = session.first_prompt()

    assert "이어서" in prompt or "이전" in prompt
    assert "처음부터" not in prompt and "다시 만들" not in prompt


async def test_handoff_without_local_output_says_rebuild_too(tmp_path):
    """handoff 분기도 같은 구멍이 있다: "이미 빌드가 완료됐다"고 단정하고
    `prototype/`을 살펴보라고 시키는데, 그 디렉토리가 없을 수 있다."""
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
    # 없는 것을 살펴보라고 시키면 안 된다.
    assert "살펴보고 현재 상태를 파악" not in prompt


async def test_handoff_with_local_output_keeps_the_improve_prompt(tmp_path):
    """산출물이 있는 개선 세션은 요약을 싣고 현재 상태를 살펴보게 한다."""
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
    """완료 선언은 도구 호출이지만, 그것을 부르라고 말하는 곳은 프롬프트뿐이다."""
    session = await _started(tmp_path)
    prompt = session.first_prompt()
    assert "build_complete" in prompt


# ---- 개선 세션: handoff가 있으면 새 session_id + 요약 주입 ----

async def test_a_handoff_starts_a_fresh_session_id(tmp_path):
    """개선 작업이 전체 트랜스크립트를 지고 가지 않게 한다. 전액 resume은
    버튼 색 하나 바꾸는 요청에도 빌드 전체 맥락을 싣는다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    old_id = "99999999-8888-7777-6666-555555555555"
    s3.blobs[SESSION_KEY] = json.dumps({"session_id": old_id})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱", "remaining": "다크 모드"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [False]      # resume이 아니다
    saved = json.loads(s3.blobs[SESSION_KEY])["session_id"]
    assert saved != old_id                            # 새 id로 갈아탔다


async def test_a_handoff_is_deleted_after_it_is_consumed(tmp_path):
    """한 번 쓴 handoff가 남으면 다음 시작도 개선 프롬프트를 받아, 세션 B의
    대화를 이어받지 못한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert HANDOFF_KEY not in s3.blobs


async def test_the_handoff_prompt_carries_the_summary(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드가 남았다"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    # 개선할 산출물이 실제로 있는 상태 — 없으면 요약이 아니라 "다시 만들어라"가
    # 나가는 것이 맞다(test_handoff_without_local_output_says_rebuild_too).
    _build_output(session)
    prompt = session.first_prompt()

    assert "할 일 앱을 만들었다" in prompt
    assert "다크 모드가 남았다" in prompt
    # 처음부터 계획하라는 지시가 아니다.
    assert "이번 턴에서는 계획만 세우고" not in prompt
    # 마음대로 시작하지 말고 물어봐야 한다.
    assert "AskUserQuestion" in prompt


async def test_a_session_that_died_without_declaring_completion_still_resumes(tmp_path):
    """완료 선언 없이 죽은 세션(유휴 타임아웃, 백엔드 재시작)은 여전히 진짜
    resume이 맞다. 두 경로는 다른 사건을 표현한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    # handoff 없음

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]
    assert "이어서" in session.first_prompt() or "이전" in session.first_prompt()


async def test_a_malformed_handoff_falls_back_to_resume(tmp_path):
    """깨진 handoff가 개선 경로를 막아서는 안 된다 — 전액 resume은 무겁지만
    정확한 degradation이다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = "{not json"

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]


async def test_a_handoff_without_a_saved_session_id_still_plans(tmp_path):
    """handoff만 있고 session.json이 없는 조합(초기화 중 부분 실패 등)은
    fresh로 떨어진다 — 이어갈 세션이 애초에 없다."""
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
    from pathfinder.proto.session import purge_session_state
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
    from pathfinder.proto.session import purge_session_state
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
    from pathfinder.proto.session import purge_session_state
    s3 = FakeS3Store()
    await purge_session_state(s3, SLUG)
    await purge_session_state(s3, SLUG)


def test_first_prompt_is_english_for_an_english_project(tmp_path):
    """영어 프로젝트의 개시 턴이 영어여야 한다.

    브레이크("계획만 세우고 빌드하지 마")가 두 언어 모두에 있어야 한다 —
    영어 판에서 그 지시가 빠지면 승인 없이 빌드가 시작된다.
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
    # 언어를 안 주는 호출부(구 코드, 테스트)가 기존 동작을 유지한다.
    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=FakeS3Store(),
        build_root=tmp_path / "protos",
        builder_factory=lambda sid, resume: FakeBuilder(),
        semaphore=BuildSemaphore(max_concurrent=2),
    )
    assert "빌드는 시작하지 마" in session.first_prompt()
