# backend/tests/test_turn_job.py — the contract of turn jobs (aipds/turn_job.py).
#
# This file guards one invariant: **a turn's lifetime is independent of who is watching.** No matter
# how many times subscribers attach and detach, or whether one is cancelled midway, the turn runs to
# completion and passes through the runner's finalization path (S3 sync). Route tests (test_routes_turns.py)
# cannot produce a mid-stream disconnect with TestClient, so this case is covered here.
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
    """Generator factory yielding `items`. With `gate`, it pauses after yielding `after` items."""
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
    """The second tab does not take events away from the first — the log is read-only."""
    gate = asyncio.Event()
    jobs = TurnJobs()
    job = jobs.start("message", _events(_msg("a"), _msg("b"), DONE, gate=gate))
    one = asyncio.create_task(_collect(job))
    two = asyncio.create_task(_collect(job))
    await asyncio.sleep(0)
    gate.set()
    assert await one == await two


async def test_a_cancelled_subscriber_does_not_stop_the_turn():
    """sse_starlette conveys a disconnect as cancellation of the subscription task. The turn is unaffected."""
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
    # The generator of a rejected turn is never even created.
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
        # Even if the start notification finishes late, it must not overtake the end notification.
        if not seen:
            await asyncio.sleep(0.01)
        seen.append(job.state)

    jobs = TurnJobs(on_state=hook)
    job = jobs.start("message", _events(DONE))
    await job.task
    await jobs.drain_hooks()
    assert len(seen) == 2 and seen[-1] == "done"


# ---- with the real runner ----

class GatedDriver:
    """A driver that writes a file, emits the first message, then pauses at the gate."""

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
    """Artifacts of a turn that finished after the watching screen left still reach S3.

    In the design where the consumer was itself the executor, this sync ran in a cancelled scope —
    the `await` inside `finally` was cancelled again, and files written via Bash stayed only local.
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
