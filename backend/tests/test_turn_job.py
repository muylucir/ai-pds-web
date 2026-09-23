# backend/tests/test_turn_job.py — 턴 작업(aipds/turn_job.py)의 계약.
#
# 이 파일이 지키는 불변식은 하나다: **턴의 수명은 보는 쪽과 무관하다.** 구독자가
# 몇 번 붙고 떨어지든, 도중에 취소되든 턴은 끝까지 돌고 러너의 종결 경로(S3 sync)를
# 지난다. 라우트 테스트(test_routes_turns.py)는 TestClient가 스트림 도중의 끊김을
# 만들 수 없어 이 경우를 여기서 본다.
import asyncio
from pathlib import Path

import pytest

from aipds.models import AgentEvent
from aipds.runner import AgentRunner
from aipds.turn_job import (FAILED_TEXT, INTERRUPTED_TEXT, TurnBusy, TurnJobs,
                            subscribe)
from fakes.in_memory_s3 import FakeS3Store

SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-2",
           "prefix": "sessions"}


def _events(*items, gate: asyncio.Event | None = None, after: int = 1):
    """`items`를 내는 generator 팩토리. `gate`가 있으면 `after`개를 낸 뒤 멈춘다."""
    async def gen():
        for i, ev in enumerate(items):
            if gate is not None and i == after:
                await gate.wait()
            yield ev
    return gen


async def _collect(job, after=0):
    return [(s, e) async for s, e in subscribe(job, after)]


def _msg(text):
    return AgentEvent(kind="message", text=text)


DONE = AgentEvent(kind="done")


async def test_a_late_subscriber_replays_the_whole_turn():
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE))
    await job.task
    got = await _collect(job)
    assert [s for s, _ in got] == [1, 2, 3]
    assert [e.text for _, e in got[:2]] == ["a", "b"]
    assert job.state == "done"


async def test_after_skips_what_the_subscriber_already_has():
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE))
    await job.task
    got = await _collect(job, after=2)
    assert [(s, e.kind) for s, e in got] == [(3, "done")]


async def test_a_subscriber_follows_a_running_turn_to_its_end():
    gate = asyncio.Event()
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE, gate=gate))
    watcher = asyncio.create_task(_collect(job))
    await asyncio.sleep(0)
    gate.set()
    got = await watcher
    assert [e.kind for _, e in got] == ["message", "message", "done"]


async def test_two_subscribers_see_the_same_sequence():
    """두 번째 탭이 첫 번째 탭의 이벤트를 가져가지 않는다 — 로그는 읽기만 된다."""
    gate = asyncio.Event()
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE, gate=gate))
    one = asyncio.create_task(_collect(job))
    two = asyncio.create_task(_collect(job))
    await asyncio.sleep(0)
    gate.set()
    assert await one == await two


async def test_a_cancelled_subscriber_does_not_stop_the_turn():
    """sse_starlette는 끊김을 구독 태스크의 취소로 전한다. 턴은 그와 무관하다."""
    gate = asyncio.Event()
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE, gate=gate))
    watcher = asyncio.create_task(_collect(job))
    await asyncio.sleep(0.01)
    watcher.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watcher
    gate.set()
    await job.task
    assert job.state == "done"
    assert job.log.last_seq == 3


async def test_only_one_turn_runs_at_a_time():
    gate = asyncio.Event()
    jobs = TurnJobs()
    first = jobs.start("message", _events(_msg("a"), DONE, gate=gate))
    made = []

    def second():
        made.append(True)
        return _events(DONE)()

    with pytest.raises(TurnBusy) as busy:
        jobs.start("message", second)
    assert busy.value.job is first
    # 거절된 턴의 generator는 만들어지지도 않는다.
    assert made == []
    gate.set()
    await first.task
    await jobs.start("message", _events(DONE)).task


async def test_a_raising_turn_ends_as_an_error_event():
    async def boom():
        yield _msg("a")
        raise RuntimeError("driver blew up")

    jobs = TurnJobs()
    job = jobs.start("message", boom)
    await job.task
    kinds = [(e.kind, e.text) for _, e in await _collect(job)]
    assert kinds == [("message", "a"), ("error", FAILED_TEXT)]
    assert job.state == "error"


async def test_cancel_marks_the_turn_interrupted_and_closes_its_generator():
    gate = asyncio.Event()
    closed = []

    async def gen():
        try:
            yield _msg("a")
            await gate.wait()
        finally:
            closed.append(True)

    jobs = TurnJobs()
    job = jobs.start("message", gen)
    await asyncio.sleep(0.01)
    await jobs.cancel()
    assert job.state == "interrupted"
    assert closed == [True]
    last = job.log.events_after(0)[-1][1]
    assert (last.kind, last.text) == ("error", INTERRUPTED_TEXT)
    assert jobs.current() is None


async def test_finished_turns_expire_after_the_retention_window():
    now = [0.0]
    jobs = TurnJobs(retention=10, clock=lambda: now[0])
    job = jobs.start("message", _events(DONE))
    await job.task
    assert jobs.get(job.id) is job and jobs.latest() is job
    now[0] = 11
    assert jobs.get(job.id) is None and jobs.latest() is None


async def test_state_changes_are_reported_in_order():
    seen = []

    async def hook(job):
        # 시작 알림이 늦게 끝나도 종결 알림을 앞지르지 못해야 한다.
        if not seen:
            await asyncio.sleep(0.01)
        seen.append(job.state)

    jobs = TurnJobs(on_state=hook)
    job = jobs.start("message", _events(DONE))
    await job.task
    await jobs.drain_hooks()
    assert len(seen) == 2 and seen[-1] == "done"


# ---- 실제 러너와 함께 ----

class GatedDriver:
    """파일을 쓰고, 첫 메시지를 낸 뒤 gate에서 멈추는 드라이버."""

    def __init__(self, workspace: Path, gate: asyncio.Event):
        self._workspace = workspace
        self._gate = gate

    async def run(self, text, session):
        path = self._workspace / "aiplc-docs" / "draft.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("초안", encoding="utf-8")
        yield _msg("쓰는 중")
        await self._gate.wait()
        yield DONE

    async def pending(self, session):
        return None


async def test_the_runner_syncs_outputs_even_after_the_watcher_left(tmp_path):
    """보던 화면이 떠난 뒤에 끝난 턴의 산출물도 S3에 오른다.

    소비자가 곧 실행 주체이던 구조에서는 이 sync가 취소된 스코프에서 돌았다 —
    `finally` 안의 `await`가 다시 취소되어 Bash로 쓴 파일이 로컬에만 남았다.
    """
    gate = asyncio.Event()
    root = tmp_path / "ws"
    s3 = FakeS3Store()
    runner = AgentRunner(project_id="p1", driver=GatedDriver(root, gate), s3=s3,
                         local_root=root, session=SESSION)
    jobs = TurnJobs()
    job = jobs.start("message", lambda: runner.send_message("go"))
    watcher = asyncio.create_task(_collect(job))
    await asyncio.sleep(0.05)
    watcher.cancel()
    gate.set()
    await job.task
    assert job.state == "done"
    assert s3.blobs["aiplc-docs/draft.md"] == "초안"
