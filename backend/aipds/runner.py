# backend/aipds/runner.py — 턴 오케스트레이션(in-process 에이전트, VM 없음).
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from aipds.models import AgentEvent
from aipds.globmatch import matches_glob
from aipds.pathsafe import reject_unsafe
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.workspace_sync import SYNC_GLOBS, content_for_s3

_log = logging.getLogger(__name__)
_SYNC_CONCURRENCY = 8


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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

    #: workspace_sync가 소유한다 — 쓰기 직후 게시(claude_driver의 PostToolUse
    #: 훅)와 이 배치 sync가 **같은 집합**을 올려야 한다. 두 벌로 두면 한쪽에만
    #: 있는 파일이 "있다가 없어지는 문서"로 보인다.
    _SYNC_GLOBS = SYNC_GLOBS
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")

    def __init__(self, project_id, driver, s3: S3StoreLike, local_root: Path, session: dict):
        self.project_id = project_id
        self._driver = driver
        self._s3 = s3
        self._local_root = Path(local_root)
        self._session = session
        self._turn_active = False
        #: 슬롯을 쥔 턴의 신원. `reattach`가 선점할 수 있으므로 플래그만으로는
        #: 부족하다 — 선점된 턴의 finally가 나중에 닫히며 플래그를 내려놓으면
        #: 재접속이 스트리밍하는 동안 새 턴이 끼어든다. 드라이버가 같은 이유로
        #: 같은 규율을 갖는다(ClaudeDriver._acquire_turn/_release_turn).
        self._turn_token: object | None = None
        self._pending_interrupt_id: str | None = None
        self._remote_etags: dict[str, str | None] | None = None
        self._synced_hashes: dict[str, str] = {}
        self.input_holder: str | None = None
        set_callback = getattr(driver, "set_file_published_callback", None)
        if set_callback is not None:
            set_callback(self._record_published_file)

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

    async def list_files_newest_first(self, glob: str) -> list[str]:
        """`list_files`와 같은 글롭, 최종 수정 시각 내림차순.

        `list_objects_v2`가 이미 주는 `LastModified`를 쓴다(S3Store.list_with_times) —
        추가 호출이 없다. 알파벳 순이 필요한 호출부는 `list_files`를 그대로 쓴다.
        """
        reject_unsafe(glob)
        pairs = await self._s3.list_with_times(_glob_prefix(glob))
        matched = [(k, t) for k, t in pairs if matches_glob(k, glob)]
        # 2차 키로 경로를 쓴다 — 같은 시각인 항목의 순서가 실행마다 흔들리지 않게.
        matched.sort(key=lambda item: (-item[1], item[0]))
        return [k for k, _ in matched]

    # ---- workspace <-> S3 ----

    def _local_path(self, key: str) -> Path:
        reject_unsafe(key)
        return self._local_root / key

    async def _list_with_etags(
        self, prefix: str,
    ) -> list[tuple[str, str | None]]:
        list_with_etags = getattr(self._s3, "list_with_etags", None)
        if list_with_etags is not None:
            return await list_with_etags(prefix)
        # Compatibility for small test/application adapters that implement the
        # older protocol. A missing ETag deliberately forces a GET every turn.
        return [(key, None) for key in await self._s3.list(prefix)]

    def _record_published_file(
        self, key: str, content: str, etag: str | None,
    ) -> None:
        """Remember a successful PostToolUse/turn-end upload."""
        self._synced_hashes[key] = _content_hash(content)
        if etag is not None and self._remote_etags is not None:
            self._remote_etags[key] = etag

    async def _restore_workspace_from_s3(self) -> None:
        """durable 워크스페이스(S3 = source of truth)를 로컬 FS로 복사한다.
        첫 턴은 전부 받고, 이후에는 ETag가 달라진 파일만 받는다."""
        started = time.perf_counter()
        cold = self._remote_etags is None
        metadata_groups = await asyncio.gather(
            *(self._list_with_etags(prefix)
              for prefix in self._RESTORE_PREFIXES))
        remote = {
            key: etag
            for group in metadata_groups
            for key, etag in group
        }
        previous = self._remote_etags or {}
        removed = set(previous) - set(remote)
        for key in removed:
            path = self._local_path(key)
            if path.is_file():
                path.unlink()
            self._synced_hashes.pop(key, None)

        keys = [
            key for key, etag in remote.items()
            if cold
            or etag is None
            or previous.get(key) != etag
            or not self._local_path(key).is_file()
        ]
        bodies = await asyncio.gather(*(self._s3.get(key) for key in keys))
        for key, body in zip(keys, bodies):
            p = self._local_path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            self._synced_hashes[key] = _content_hash(body)
        self._remote_etags = remote
        log_performance(
            _log,
            str(self.project_id),
            "restore",
            started,
            cold=str(cold).lower(),
            listed=len(remote),
            downloaded=len(keys),
            removed=len(removed),
        )

    async def _sync_workspace_to_s3(self) -> None:
        """턴 출력(방법론 산출물 + 프로토타입 소스 서브트리)을 로컬에서 durable
        S3로 끌어올린다. audit.md는 저장 시 redaction(direct S3 reader 노출 차단)."""
        started = time.perf_counter()
        scanned = 0
        changed: list[tuple[str, str]] = []
        for path in self._local_root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(self._local_root).as_posix()
            if not any(matches_glob(key, glob) for glob in self._SYNC_GLOBS):
                continue
            scanned += 1
            reject_unsafe(key)
            content = content_for_s3(
                key, path.read_text(encoding="utf-8", errors="replace"))
            if self._synced_hashes.get(key) != _content_hash(content):
                changed.append((key, content))

        semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)
        uploaded = 0

        async def upload(key: str, content: str) -> None:
            nonlocal uploaded
            async with semaphore:
                etag = await self._s3.put(key, content)
            self._record_published_file(key, content, etag)
            uploaded += 1

        try:
            results = await asyncio.gather(
                *(upload(key, content) for key, content in changed),
                return_exceptions=True,
            )
            failure = next(
                (result for result in results
                 if isinstance(result, BaseException)),
                None,
            )
            if failure is not None:
                raise failure
        finally:
            log_performance(
                _log,
                str(self.project_id),
                "sync",
                started,
                scanned=scanned,
                changed=len(changed),
                uploaded=uploaded,
                concurrency=_SYNC_CONCURRENCY,
            )

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

    def _claim_turn(self, *, preempt: bool = False) -> object | None:
        """턴 슬롯을 쥔다. 이미 쥐어져 있으면 None(`preempt`면 빼앗는다).

        토큰을 발급하는 이유는 해제를 홀더에게만 허용하기 위해서다 —
        `_finish_turn`이 토큰을 확인하므로, 선점된 턴이 뒤늦게 닫히며 해제를
        시도해도 새 홀더의 슬롯은 풀리지 않는다.
        """
        if self._turn_active and not preempt:
            return None
        self._turn_active = True
        self._turn_token = object()
        return self._turn_token

    def _finish_turn(self, token: object | None) -> None:
        """이 토큰이 아직 홀더면 슬롯을 놓는다. 아니면 no-op(선점당한 경우)."""
        if self._turn_token is token:
            self._turn_active = False
            self._turn_token = None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        token = self._claim_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        synced = False
        turn_started = time.perf_counter()
        first_event = False
        first_text = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            async for event in self._driver.run(text, self._session):
                if not first_event:
                    log_performance(
                        _log,
                        str(self.project_id), "first_agent_event", turn_started,
                        kind=event.kind)
                    first_event = True
                if not first_text and event.kind == "message" and event.text:
                    log_performance(
                        _log,
                        str(self.project_id), "first_text", turn_started)
                    first_text = True
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
            self._finish_turn(token)
            # Backstop for every path that never reached the terminal event:
            # an SSE client disconnecting, a proxy timeout, the user navigating
            # away (GeneratorExit here), or the driver raising mid-turn. Without
            # this, files the agent already wrote stayed in the VOLATILE local
            # workspace only -- the doc panel showed an empty document and the
            # next refresh dropped it from the list, because S3 (the source of
            # truth for both) never received it.
            if not synced:
                await self._sync_abandoned_turn()
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="message", synced=str(synced).lower())

    async def reattach(self) -> AsyncIterator[AgentEvent]:
        """Relay a turn whose SSE consumer went away (sleep, screensaver, proxy).

        **워크스페이스를 복원하지 않는다.** 다른 두 경로는 턴을 **시작**하므로
        S3가 이겨야 하지만, 여기서는 에이전트가 지금 그 디렉터리에 쓰고 있다 —
        복원하면 방금 쓴 파일을 S3의 옛 사본으로 덮는다. 이 경로가 하는 일은
        보는 것뿐이다.

        드라이버가 `run_live`를 갖고 있지 않으면(Strands 시절 계약, 테스트 더블)
        `done` 하나로 끝낸다 — 재접속은 편의이고, 없다는 이유로 화면을 깨지
        않는다. 호출부는 그때 `GET /history`로 떨어진다.

        **진행 중이라고 거부하지 않는다.** 예전에는 다른 세 메서드에서 복사한
        `if self._turn_active:` 가드가 맨 앞에 있었는데, 그 플래그의 의미는
        "소비자가 있다"이고(`ClaudeDriver.has_live_turn`의 docstring이 그 의미와
        "재접속한 브라우저가 튕기지 않아야 한다"를 함께 적어 뒀다) 그것은 재접속이
        **견뎌야 하는** 조건이다. 나머지 세 메서드에서는 맞는 가드다 — 그쪽은 턴을
        시작한다.

        실측(2026-08-19, 배포 인스턴스): 그 가드 때문에 사용자 화면에 에이전트
        발화로 `turn already in progress`가 떴다. 계측 결과 이 가드가 가장 먼저
        발화하고 드라이버의 `has_live_turn()`은 조회조차 되지 않았다.

        **토큰으로 슬롯을 쥔다.** 선점된 `send_message`의 finally가 나중에 닫히며
        플래그를 내려놓으면, 재접속이 스트리밍하는 동안 새 턴이 끼어든다. 드라이버가
        같은 이유로 같은 규율을 갖는다(`_acquire_turn`/`_release_turn`).
        """
        run_live = getattr(self._driver, "run_live", None)
        if run_live is None:
            yield AgentEvent(kind="done")
            return
        token = self._claim_turn(preempt=True)
        synced = False
        turn_started = time.perf_counter()
        try:
            async for event in run_live():
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    # 종결 전에 올린다 — `done`을 본 클라이언트가 곧바로 문서를
                    # 읽으므로(send_message와 같은 규율).
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._finish_turn(token)
            if not synced:
                await self._sync_abandoned_turn()
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="reattach", synced=str(synced).lower())

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        token = self._claim_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        synced = False
        turn_started = time.perf_counter()
        first_event = False
        first_text = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
            async for event in self._driver.run_answers(interrupt_id, answers, self._session):
                if not first_event:
                    log_performance(
                        _log,
                        str(self.project_id), "first_agent_event", turn_started,
                        kind=event.kind)
                    first_event = True
                if not first_text and event.kind == "message" and event.text:
                    log_performance(
                        _log,
                        str(self.project_id), "first_text", turn_started)
                    first_text = True
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._finish_turn(token)
            if not synced:
                await self._sync_abandoned_turn()  # see send_message's note
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="answers", synced=str(synced).lower())

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
