# backend/aipds/turn_job.py — runs a turn as a server-owned job.
#
# **A turn's lifetime does not belong to the HTTP connection.** One turn takes 2.5-5.6
# minutes, and in the meantime the browser gets refreshed, goes to sleep, or ends up
# with two tabs. If the SSE response's generator drove the turn directly, the moment
# the consumer left, that generator's `finally` would run in a cancelled scope --
# sse_starlette conveys a disconnect as task group cancellation, and anyio's
# cancellation is level-based, so the `await`s inside `finally` (S3 sync, transcript
# flush) get cancelled again. A route that consumes the whole turn within the request
# hits the proxy timeout (CloudFront 60 seconds).
#
# So the turn is read to the end by the `asyncio.Task` here as its **sole consumer**.
# With exactly one consumer that never leaves, the runner's and driver's generators
# always close on the normal path. The screen is a subscriber: it replays the events
# accumulated with seq numbers in `TurnLog` from `after` and keeps receiving. However
# many times subscribers attach and detach, the turn is unaffected. The only thing
# that stops a turn is an interrupt (`/interrupt`).
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Literal

from aipds.models import AgentEvent

_log = logging.getLogger(__name__)

TurnState = Literal["running", "done", "error", "interrupted"]

#: How long a late subscriber can replay a finished turn. Covers the typical range of
#: waking from sleep; after that, `GET /history` (the transcript) is the source of truth.
RETENTION_SECONDS = 600.0

#: Terminal event text for a turn cut off by backend shutdown. The screen reads this
#: value as an error code.
INTERRUPTED_TEXT = "turn interrupted"
#: Terminal event when the runner generator ends with an exception -- same as the
#: driver's contract text.
FAILED_TEXT = "agent turn failed"


class TurnBusy(Exception):
    """This project already has a running turn. `job` is that turn -- the caller attaches to it."""

    def __init__(self, job: "TurnJob") -> None:
        super().__init__(job.id)
        self.job = job


class TurnLog:
    """Accumulates one turn's events with a seq (starting at 1). Independent of subscriber count."""

    def __init__(self) -> None:
        self._events: list[tuple[int, AgentEvent]] = []
        self._changed = asyncio.Event()
        self.closed = False

    @property
    def last_seq(self) -> int:
        return self._events[-1][0] if self._events else 0

    def append(self, event: AgentEvent) -> int:
        seq = self.last_seq + 1
        self._events.append((seq, event))
        self._wake()
        return seq

    def close(self) -> None:
        self.closed = True
        self._wake()

    def _wake(self) -> None:
        # Wake every waiting subscriber and install a new event for the next wait --
        # with a single event loop, no waiter can slip in between set and replace.
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def events_after(self, seq: int) -> list[tuple[int, AgentEvent]]:
        # seq is assigned from 1 with no gaps, so the index is the position.
        return self._events[max(seq, 0):]

    async def wait_beyond(self, seq: int) -> None:
        """Wait until an event appears after `seq` or the log is closed."""
        changed = self._changed
        if self.last_seq > seq or self.closed:
            return
        await changed.wait()


@dataclass
class TurnJob:
    id: str
    kind: str
    log: TurnLog
    started_at: float
    state: TurnState = "running"
    finished_at: float | None = None
    #: What the user sent (to be restored on screen as the user's bubble). None for auto-started turns.
    input_text: str | None = None
    #: Wall-clock start time (epoch seconds). `started_at` is a monotonic clock and
    #: cannot be compared with transcript timestamps -- this is what the prototype
    #: history uses to cut off "from this turn on, the session replays it"
    #: (proto/history.py).
    started_wall: float = field(default_factory=time.time)
    task: asyncio.Task | None = field(default=None, repr=False)

    def summary(self) -> dict:
        return {"turn_id": self.id, "kind": self.kind, "state": self.state,
                "last_seq": self.log.last_seq, "input": self.input_text}


async def subscribe(job: TurnJob, after: int = 0
                    ) -> AsyncIterator[tuple[int, AgentEvent]]:
    """Replay events after `after`, then keep streaming while the turn runs.

    Nothing happens to the turn if a subscriber drops this generator -- it only reads.
    """
    seq = after
    while True:
        for s, ev in job.log.events_after(seq):
            yield s, ev
            seq = s
        if job.log.closed and seq >= job.log.last_seq:
            return
        await job.log.wait_beyond(seq)


class TurnJobs:
    """Turn jobs for one project. Only one turn runs at a time."""

    def __init__(self, *, retention: float = RETENTION_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 on_state: Callable[[TurnJob], Awaitable[None]] | None = None,
                 ) -> None:
        self._jobs: dict[str, TurnJob] = {}
        self._latest: TurnJob | None = None
        self._retention = retention
        self._clock = clock
        #: Announces state transitions (start, end). The S3 marker (turn_marker.py)
        #: used to show "중단됨" ("Interrupted") after a restart uses this. It runs
        #: separately from the turn and does not block the turn even if it fails.
        self._on_state = on_state
        self._hook_tasks: set[asyncio.Task] = set()
        # Notifications run **in order**. If the start notification finished after the
        # end notification, the marker would stay `running` and a finished turn would
        # show as "중단됨" ("Interrupted") after a restart. asyncio.Lock's wait queue is FIFO and
        # tasks first run in creation order, so this is sufficient.
        self._hook_lock = asyncio.Lock()

    def current(self) -> TurnJob | None:
        """The running turn, or None if there is none."""
        job = self._latest
        return job if job is not None and job.state == "running" else None

    def latest(self) -> TurnJob | None:
        """The last turn (even if finished). None if the retention period has passed."""
        self._prune()
        return self._latest

    def get(self, turn_id: str) -> TurnJob | None:
        self._prune()
        return self._jobs.get(turn_id)

    def all(self) -> list[TurnJob]:
        """All retained turns, in start order."""
        self._prune()
        return list(self._jobs.values())   # dict preserves insertion order

    def start(self, kind: str,
              events: Callable[[], AsyncIterator[AgentEvent]],
              input_text: str | None = None) -> TurnJob:
        """Start a turn. `events` is a function that creates the generator producing the turn's events.

        Why it takes a function: if a turn is running, the request must be rejected
        **without even creating** the generator. If one were created and then
        discarded, the runner's slot check inside that generator would vanish without
        ever getting a chance to run.
        """
        running = self.current()
        if running is not None:
            raise TurnBusy(running)
        self._prune()
        job = TurnJob(id=uuid.uuid4().hex, kind=kind, log=TurnLog(),
                      started_at=self._clock(), input_text=input_text)
        self._jobs[job.id] = job
        self._latest = job
        job.task = asyncio.create_task(self._consume(job, events()),
                                       name=f"turn:{kind}:{job.id}")
        self._notify(job)
        return job

    async def _consume(self, job: TurnJob,
                       events: AsyncIterator[AgentEvent]) -> None:
        state: TurnState = "done"
        try:
            async for ev in events:
                job.log.append(ev)
                if ev.kind == "error":
                    state = "error"
                elif ev.kind == "done":
                    state = "done"
        except asyncio.CancelledError:
            # Backend shutdown or project deletion. Let the generator close on the
            # normal path via `aclose` (the runner's finally does the backstop sync), and
            # leave the cutoff on screen as a terminal event.
            state = "interrupted"
            job.log.append(AgentEvent(kind="error", text=INTERRUPTED_TEXT))
            await _aclose_quietly(events)
            raise
        except Exception:
            _log.exception("turn %s (%s) failed", job.id, job.kind)
            state = "error"
            job.log.append(AgentEvent(kind="error", text=FAILED_TEXT))
        finally:
            job.state = state
            job.finished_at = self._clock()
            job.log.close()
            self._notify(job)

    async def cancel(self) -> None:
        """Cancel the running turn and wait until it ends (shutdown and deletion paths)."""
        job = self.current()
        if job is None or job.task is None:
            return
        job.task.cancel()
        try:
            await job.task
        except asyncio.CancelledError:
            pass

    def _prune(self) -> None:
        now = self._clock()
        for jid, job in list(self._jobs.items()):
            if (job.finished_at is not None
                    and now - job.finished_at > self._retention):
                del self._jobs[jid]
                if self._latest is job:
                    self._latest = None

    def _notify(self, job: TurnJob) -> None:
        if self._on_state is None:
            return
        task = asyncio.create_task(self._run_hook(job))
        # Hold a reference -- if dropped, the GC could collect the task midway.
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)

    async def _run_hook(self, job: TurnJob) -> None:
        try:
            async with self._hook_lock:
                await self._on_state(job)
        except Exception:
            _log.exception("turn state hook failed for %s", job.id)

    async def drain_hooks(self) -> None:
        """Wait for in-flight state notifications (tests and the shutdown path)."""
        while self._hook_tasks:
            await asyncio.gather(*list(self._hook_tasks))


async def _aclose_quietly(events: AsyncIterator[AgentEvent]) -> None:
    aclose = getattr(events, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        _log.exception("closing an interrupted turn's generator failed")
