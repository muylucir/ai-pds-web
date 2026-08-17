# backend/pathfinder/runner.py — 턴 오케스트레이션(in-process 에이전트, VM 없음).
from __future__ import annotations
import asyncio
import json
import logging
import shutil
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from pathfinder.models import AgentEvent
from pathfinder.globmatch import matches_glob
from pathfinder.pathsafe import reject_unsafe
from pathfinder.s3store import S3StoreLike
from pathfinder.parsers.redaction import redact_credentials

_log = logging.getLogger(__name__)


def _interrupt_id_from(payload: str | None) -> str | None:
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


class AgentRunner:
    """프로젝트당 턴 실행기. 파일 계약 ops는 durable S3 직접(부팅 없음). 턴은
    S3 → 로컬 워크스페이스 restore, in-process 에이전트 실행, done/error 시
    로컬 → S3 sync. VM/부팅 상태기계는 없다 — 로컬 디렉토리는 휘발이며 매 턴
    시작 시 S3에서 재구성된다(S3 = source of truth)."""

    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")

    def __init__(self, project_id, driver, s3: S3StoreLike, local_root: Path, session: dict):
        self.project_id = project_id
        self._driver = driver
        self._s3 = s3
        self._local_root = Path(local_root)
        self._session = session
        self._turn_active = False
        self._pending_interrupt_id: str | None = None
        self.input_holder: str | None = None

    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder

    # ---- file-as-contract ops: durable S3 직접 ----

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        return await self._s3.get(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        await self._s3.put(rel_path, content)

    async def write_file_if_absent(self, rel_path: str, content: str) -> bool:
        """Upload path only: never silently replace an existing key."""
        reject_unsafe(rel_path)
        return await self._s3.put_if_absent(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if matches_glob(k, glob))

    # ---- workspace <-> S3 ----

    def _local_path(self, key: str) -> Path:
        reject_unsafe(key)
        return self._local_root / key

    async def _restore_workspace_from_s3(self) -> None:
        """durable 워크스페이스(S3 = source of truth)를 로컬 FS로 복사한다.
        S3가 무조건 이긴다; 푸시는 멱등."""
        # **목록도, 본문도 병렬로.** 이 함수는 **매 턴 시작**에 돌므로 순차 왕복이
        # 그대로 답변 지연이 된다 — 실측(2026-08-17, 배포 인스턴스): S3 왕복 1회
        # 30ms, 산출물 6개에 0.247초. 산출물 수에 선형이므로 50개면 ~3초다.
        # (session_store.load_transcript와 같은 판단, 선례는 project_store.)
        #
        # 실패는 삼키지 않는다: 여기가 실패하면 워크스페이스가 불완전한 상태로
        # 턴이 돌고 에이전트가 없는 파일을 못 찾는다. 그건 조용한 오작동이므로
        # 예외를 그대로 올려 턴을 실패시킨다(sync의 fail-closed와 같은 규율).
        key_lists = await asyncio.gather(
            *(self._s3.list(prefix) for prefix in self._RESTORE_PREFIXES))
        keys = [k for group in key_lists for k in group]
        bodies = await asyncio.gather(*(self._s3.get(k) for k in keys))
        for key, body in zip(keys, bodies):
            p = self._local_path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")

    async def _sync_workspace_to_s3(self) -> None:
        """턴 출력(방법론 산출물 + 프로토타입 소스 서브트리)을 로컬에서 durable
        S3로 끌어올린다. audit.md는 저장 시 redaction(direct S3 reader 노출 차단)."""
        for glob in self._SYNC_GLOBS:
            for path in self._local_root.rglob("*"):
                if not path.is_file():
                    continue
                key = path.relative_to(self._local_root).as_posix()
                if not matches_glob(key, glob):
                    continue
                reject_unsafe(key)  # fail-closed: 안전하지 않은 키는 sync 전체 중단
                content = path.read_text(encoding="utf-8", errors="replace")
                if key == "aiplc-docs/audit.md":
                    content = redact_credentials(content)
                await self._s3.put(key, content)

    async def _sync_abandoned_turn(self) -> None:
        """Best-effort sync for a turn that never produced a terminal event.

        Deliberately swallows errors, unlike the fail-closed sync on the
        done/error path. This runs inside `finally`, where the turn is already
        being torn down for one of two reasons: the consumer walked away (an
        exception here would surface during generator cleanup, at a caller that
        is no longer listening) or the driver raised (an exception here would
        REPLACE that root-cause traceback with an S3 error). Neither is worth
        losing; the log entry is.
        """
        try:
            await self._sync_workspace_to_s3()
        except Exception:
            _log.exception("post-turn workspace sync failed: %s", self.project_id)

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        synced = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            async for event in self._driver.run(text, self._session):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    # Sync BEFORE yielding the terminal event: a client that
                    # reads a doc the moment it sees `done` must not race the
                    # upload (fail-closed — a sync error surfaces instead of
                    # the terminal event).
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._turn_active = False
            # Backstop for every path that never reached the terminal event:
            # an SSE client disconnecting, a proxy timeout, the user navigating
            # away (GeneratorExit here), or the driver raising mid-turn. Without
            # this, files the agent already wrote stayed in the VOLATILE local
            # workspace only -- the doc panel showed an empty document and the
            # next refresh dropped it from the list, because S3 (the source of
            # truth for both) never received it.
            if not synced:
                await self._sync_abandoned_turn()

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._turn_active = True
        synced = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
            async for event in self._driver.run_answers(interrupt_id, answers, self._session):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._turn_active = False
            if not synced:
                await self._sync_abandoned_turn()  # see send_message's note

    async def interrupt(self) -> None:
        """진행 중인 턴을 끊는다. 드라이버로 위임한다.

        턴 슬롯은 건드리지 않는다 — 돌고 있는 run()이 종결 이벤트를 내며
        스스로 놓는다. 여기서 함께 놓으면 이중 해제가 된다.
        """
        interrupt = getattr(self._driver, "interrupt", None)
        if interrupt is None:
            return  # 계약 밖의 선택 메서드다 — 없으면 no-op(여전히 멱등)
        await interrupt()

    async def pending(self) -> str | None:
        try:
            payload = await self._driver.pending(self._session)
        except Exception:
            _log.exception("pending probe failed")
            return None
        got = _interrupt_id_from(payload)
        if got:
            self._pending_interrupt_id = got
        return payload

    async def stop(self) -> None:
        """로컬 워크스페이스 정리 + 드라이버 종료. S3(durable)는 건드리지 않는다
        — 삭제는 projects.py의 delete_project_data가 담당.

        드라이버의 disconnect()는 계약(run/run_answers/pending) 밖의 **선택적**
        메서드다. ClaudeDriver는 claude 서브프로세스를 붙들고 있으므로 이걸 안
        부르면 프로젝트를 삭제할 때마다 그 프로세스가 backend 수명 내내
        샌다(~300-500MB). getattr로 존재 여부만 확인하고 없으면 조용히
        건너뛴다 — 드라이버가 하나뿐이 된 뒤에도 이 방어를 남기는 이유는
        계약(runner.py가 쓰는 세 메서드)과 구현 편의를 섞지 않기 위해서다."""
        disconnect = getattr(self._driver, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except Exception:
                _log.exception("driver disconnect failed for %s", self.project_id)
        await asyncio.to_thread(shutil.rmtree, self._local_root, ignore_errors=True)


def _glob_prefix(glob: str) -> str:
    """글롭의 선행 정적(와일드카드 없는) 디렉토리 부분 = S3 list prefix.
    'aiplc-docs/**/*-q.md' -> 'aiplc-docs/', 'aiplc-docs/audit.md' -> 그 자체."""
    parts = PurePosixPath(glob).parts
    static: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    prefix = "/".join(static)
    if not static:
        return ""
    if len(static) == len(parts):
        return prefix
    return prefix + "/"
