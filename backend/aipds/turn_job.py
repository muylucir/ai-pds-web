# backend/aipds/turn_job.py — 턴을 서버가 소유하는 작업으로 돌린다.
#
# **턴의 수명은 HTTP 연결의 것이 아니다.** 턴 하나는 2.5~5.6분이고, 그 사이에
# 브라우저는 새로고침되고, 절전에 들어가고, 탭이 두 개가 된다. 턴을 SSE 응답의
# generator가 직접 돌리면 소비자가 떠나는 순간 그 generator의 `finally`가 취소된
# 스코프에서 돈다 — sse_starlette는 끊김을 task group 취소로 전하고, anyio의 취소는
# 수준 기반이라 `finally` 안의 `await`(S3 sync, 트랜스크립트 flush)도 다시 취소된다.
# 요청 안에서 턴을 끝까지 소비하는 라우트는 프록시 타임아웃(CloudFront 60초)에 걸린다.
#
# 그래서 턴은 여기의 `asyncio.Task`가 **유일한 소비자**로서 끝까지 읽는다. 떠나지 않는
# 소비자가 하나뿐이므로 러너·드라이버의 generator는 언제나 정상 경로로 닫힌다. 화면은
# 구독자다: `TurnLog`에 seq가 붙어 쌓인 이벤트를 `after`부터 재생하고 이어서 받는다.
# 구독자가 몇 번 붙고 떨어지든 턴에는 아무 영향이 없다. 턴을 멈추는 것은 중단
# (`/interrupt`)뿐이다.
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

#: 끝난 턴을 늦게 온 구독자가 재생할 수 있는 시간. 절전 복귀의 일반적인 범위를
#: 덮고, 그 뒤로는 `GET /history`(트랜스크립트)가 정본이다.
RETENTION_SECONDS = 600.0

#: 백엔드 종료로 끊긴 턴의 종결 이벤트 문구. 화면은 이 값을 오류 코드로 읽는다.
INTERRUPTED_TEXT = "turn interrupted"
#: 러너 generator가 예외로 끝났을 때의 종결 이벤트 — 드라이버의 계약 문구와 같다.
FAILED_TEXT = "agent turn failed"


class TurnBusy(Exception):
    """이 프로젝트에 이미 도는 턴이 있다. `job`이 그 턴이다 — 호출부는 거기에 붙는다."""

    def __init__(self, job: "TurnJob") -> None:
        super().__init__(job.id)
        self.job = job


class TurnLog:
    """한 턴의 이벤트를 seq(1부터)와 함께 쌓는다. 구독자 수와 무관하다."""

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
        # 기다리던 구독자를 모두 깨우고 다음 대기용으로 새 이벤트를 건다 — 단일
        # 이벤트 루프라 set과 교체 사이에 끼어드는 대기자가 없다.
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def events_after(self, seq: int) -> list[tuple[int, AgentEvent]]:
        # seq는 1부터 빈틈없이 붙으므로 인덱스가 곧 위치다.
        return self._events[max(seq, 0):]

    async def wait_beyond(self, seq: int) -> None:
        """`seq` 뒤에 이벤트가 생기거나 로그가 닫힐 때까지 기다린다."""
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
    task: asyncio.Task | None = field(default=None, repr=False)

    def summary(self) -> dict:
        return {"turn_id": self.id, "kind": self.kind, "state": self.state,
                "last_seq": self.log.last_seq}


async def subscribe(job: TurnJob, after: int = 0
                    ) -> AsyncIterator[tuple[int, AgentEvent]]:
    """`after` 뒤의 이벤트를 재생하고, 턴이 도는 동안 이어서 흘린다.

    구독자가 이 generator를 버려도 턴에는 아무 일이 없다 — 이것은 읽기만 한다.
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
    """프로젝트 하나의 턴 작업들. 동시에 도는 턴은 하나다."""

    def __init__(self, *, retention: float = RETENTION_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 on_state: Callable[[TurnJob], Awaitable[None]] | None = None,
                 ) -> None:
        self._jobs: dict[str, TurnJob] = {}
        self._latest: TurnJob | None = None
        self._retention = retention
        self._clock = clock
        #: 상태 전이(시작·종결)를 알린다. 재시작 뒤 "중단됨"을 보이기 위한 S3
        #: 표식(turn_marker.py)이 이것을 쓴다. 턴과 따로 돌고, 실패해도 턴을
        #: 막지 않는다.
        self._on_state = on_state
        self._hook_tasks: set[asyncio.Task] = set()
        # 알림은 **순서대로** 돈다. 시작 알림이 종결 알림보다 늦게 끝나면 표식이
        # `running`으로 남고, 재시작 뒤 끝난 턴이 "중단됨"으로 보인다. asyncio.Lock의
        # 대기열은 FIFO이고 태스크는 만든 순서로 처음 실행되므로 이것으로 충분하다.
        self._hook_lock = asyncio.Lock()

    def current(self) -> TurnJob | None:
        """도는 턴, 없으면 None."""
        job = self._latest
        return job if job is not None and job.state == "running" else None

    def latest(self) -> TurnJob | None:
        """마지막 턴(끝났어도). 보존 시간이 지났으면 None."""
        self._prune()
        return self._latest

    def get(self, turn_id: str) -> TurnJob | None:
        self._prune()
        return self._jobs.get(turn_id)

    def start(self, kind: str,
              events: Callable[[], AsyncIterator[AgentEvent]]) -> TurnJob:
        """턴을 시작한다. `events`는 턴의 이벤트를 내는 generator를 만드는 함수다.

        함수로 받는 이유: 도는 턴이 있으면 generator를 **만들지도 않고** 거절해야
        한다. 만들어 두고 버리면 러너의 슬롯 검사가 그 generator 안에서 돌 기회조차
        없이 사라진다.
        """
        running = self.current()
        if running is not None:
            raise TurnBusy(running)
        self._prune()
        job = TurnJob(id=uuid.uuid4().hex, kind=kind, log=TurnLog(),
                      started_at=self._clock())
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
            # 백엔드 종료 또는 프로젝트 삭제. generator는 `aclose`로 정상 경로를
            # 닫게 하고(러너의 finally가 백스톱 sync를 한다), 화면에는 끊긴 사실을
            # 종결 이벤트로 남긴다.
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
        """도는 턴을 취소하고 끝날 때까지 기다린다(종료·삭제 경로)."""
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
        # 참조를 쥐어 둔다 — 버리면 GC가 도중에 태스크를 거둘 수 있다.
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)

    async def _run_hook(self, job: TurnJob) -> None:
        try:
            async with self._hook_lock:
                await self._on_state(job)
        except Exception:
            _log.exception("turn state hook failed for %s", job.id)

    async def drain_hooks(self) -> None:
        """진행 중인 상태 알림을 기다린다(테스트와 종료 경로)."""
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
