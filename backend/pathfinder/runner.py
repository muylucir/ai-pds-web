# backend/pathfinder/runner.py — 턴 오케스트레이션(in-process 에이전트, VM 없음).
from __future__ import annotations
import asyncio
import json
import logging
import shutil
from pathlib import Path
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
        for prefix in self._RESTORE_PREFIXES:
            for key in await self._s3.list(prefix):
                p = self._local_path(key)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(await self._s3.get(key), encoding="utf-8")

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

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            async for event in self._driver.run(text, self._session):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                yield event
        finally:
            self._turn_active = False

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._turn_active = True
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
                yield event
        finally:
            self._turn_active = False

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
        """로컬 워크스페이스 정리. S3(durable)는 건드리지 않는다 — 삭제는
        projects.py의 delete_project_data가 담당."""
        await asyncio.to_thread(shutil.rmtree, self._local_root, ignore_errors=True)


def _glob_prefix(glob: str) -> str:
    """글롭의 선행 정적(와일드카드 없는) 디렉토리 부분 = S3 list prefix.
    'aiplc-docs/**/*-q.md' -> 'aiplc-docs/', 'aiplc-docs/audit.md' -> 그 자체."""
    from pathlib import PurePosixPath
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
