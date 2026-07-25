# backend/pathfinder/proto/session.py — PrototypeSession: one prototype build
# session's orchestration.
#
# Post-MicroVM shape: no boot, no HTTP file push, no VM stop. What remains is
# (1) resolving the durable session id so context resumes, (2) making sure the
# build directory and the spec exist on local disk for the agent's own file
# tools, (3) relaying turns, (4) the idle timer -- which now reclaims a ~300-
# 500MB subprocess and a build slot rather than a VM.
#
# Closing a session no longer destroys context: the transcript lives in S3 and
# the build directory stays on disk, so the next start() resumes.
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Literal, Protocol

from pathfinder.models import AgentEvent
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        "failed", "closed"]


class BuilderLike(Protocol):
    def run(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool: ...
    async def interrupt(self) -> None: ...
    async def pending(self) -> str | None: ...
    async def disconnect(self) -> None: ...


class SemaphoreLike(Protocol):
    def try_acquire(self) -> bool: ...
    def release(self) -> None: ...
    def snapshot(self) -> dict[str, int]: ...


def _interrupt_id_from(payload: str | None) -> str | None:
    """Parse the interrupt id out of a questions payload. Mirrors runner.py --
    a malformed/contract-drifted payload must degrade (None) rather than blow
    up the turn relay."""
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class PrototypeSession:
    """One prototype's build session: owns the durable session id, the build
    directory, the turn relay, the questions interrupt id, and the idle timer.
    """

    def __init__(
        self,
        project_id: str,
        slug: str,
        s3: S3StoreLike,
        build_root: Path,
        builder_factory: Callable[[str, bool], BuilderLike],
        semaphore: SemaphoreLike,
        idle_seconds: int | float = 1800,
    ):
        self.project_id = project_id
        self.slug = slug
        self._s3 = s3
        self._build_root = Path(build_root)
        self._builder_factory = builder_factory
        self._semaphore = semaphore
        self._idle_seconds = idle_seconds

        self.status: SessionStatus = "starting"
        self._builder: BuilderLike | None = None
        self._session_id: str | None = None
        self._pending_interrupt_id: str | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._closed = False
        # A mid-turn raise releases the slot immediately in send_message's
        # except below (nothing else would -- the caller sees the exception
        # and abandons the session without ever calling close()). This flag
        # is the guard against a LATER close() or idle-timeout releasing the
        # same slot a second time, which would wrongly free a slot some
        # OTHER session is holding (BuildSemaphore.release() clamps at 0, so
        # it can't detect an over-release itself).
        self._slot_released = False

    # ---- path/key helpers ----

    def _spec_key(self) -> str:
        return f"aiplc-docs/discovery/prototypes/{self.slug}/PROTOTYPE-{self.slug}.md"

    def _session_key(self) -> str:
        return f"prototypes/{self.slug}/session.json"

    def build_dir(self) -> Path:
        return self._build_root / self.project_id / self.slug

    # ---- durable session id ----

    async def _resolve_session_id(self) -> tuple[str, bool]:
        """Return (session_id, resume). A saved id means resume; a missing or
        non-UUID one means start fresh -- the SDK rejects a non-UUID resume
        value outright, so a legacy/hand-edited value must not wedge the
        session."""
        try:
            saved = json.loads(await self._s3.get(self._session_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = None
        if isinstance(saved, dict) and _is_uuid(saved.get("session_id")):
            return saved["session_id"], True
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        return new_id, False

    # ---- idle timer ----

    def _arm_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(self._idle_seconds, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    # ---- start ----

    async def start(self) -> None:
        spec_md = await self._s3.get(self._spec_key())  # FileNotFoundError -> route 404

        self._session_id, resume = await self._resolve_session_id()

        # The agent reads the spec with its own file tools from cwd, so it has
        # to exist on local disk (the VM era pushed it over HTTP instead).
        # Refreshed on every start so a spec edited in Discovery is picked up.
        build_dir = self.build_dir()
        spec_path = build_dir / self._spec_key()
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_md, encoding="utf-8")

        self._builder = self._builder_factory(self._session_id, resume)
        self.status = "ready"
        self._arm_idle_timer()

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        assert self._builder is not None, "start() must be called before send_message()"
        self._arm_idle_timer()
        self.status = "building"
        try:
            async for event in self._builder.run(text):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                        self.status = "waiting_input"
                elif event.kind == "done":
                    self.status = "ready"
                elif event.kind == "error":
                    # Sanitized turn-level error: session stays usable and
                    # retryable -- NOT a session failure.
                    self.status = "ready"
                yield event
        except Exception:
            self.status = "failed"
            # The caller (routes/prototypes.py) sees this exception propagate
            # out of the SSE generator and never gets a session to close --
            # the retry path evicts the dict entry outright. Without
            # releasing here, the slot is gone until process restart.
            if not self._slot_released:
                self._slot_released = True
                self._semaphore.release()
            raise

    async def send_answers(self, answers: dict[str, str]) -> bool:
        assert self._builder is not None, "start() must be called before send_answers()"
        if self._pending_interrupt_id is None:
            return False
        interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
        ok = await self._builder.submit_answers(interrupt_id, answers)
        if not ok:
            return False
        self._arm_idle_timer()
        self.status = "building"
        return True

    async def interrupt(self) -> None:
        if self._builder is not None:
            await self._builder.interrupt()

    # ---- close: disconnect + release the slot. Context is NOT discarded. ----

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

        ok = True
        if self._builder is not None:
            try:
                await self._builder.disconnect()
            except Exception:
                # A wedged subprocess must not keep the build slot forever --
                # log it, mark the session failed, and still release below.
                _log.exception("builder disconnect failed: %s/%s",
                               self.project_id, self.slug)
                ok = False
            self._builder = None

        # A prior mid-turn failure in send_message already released this
        # session's slot -- releasing again would free a slot that belongs to
        # some OTHER session (the semaphore's clamp-at-zero only guards
        # against going negative, not against crediting the wrong holder).
        if not self._slot_released:
            self._slot_released = True
            self._semaphore.release()
        self.status = "closed" if ok else "failed"

    # ---- first turn's auto-spoken prompt ----

    def first_prompt(self) -> str:
        spec_key = self._spec_key()
        proxy_path = f"/api/proto/{self.project_id}/{self.slug}/"
        return (
            f"`{spec_key}` 파일을 읽고, 그 내용에 따라 프로토타입을 빌드해줘.\n\n"
            "지침:\n"
            f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악한 뒤 빌드를 시작해줘.\n"
            "2. 진행 중 불확실하거나 결정이 필요한 사항이 있으면 마음대로 넘기지 말고, "
            "AskUserQuestion으로 나에게 먼저 물어봐줘.\n"
            "3. 완성물은 반드시 작업 디렉토리 아래 `prototype/`에 두고, 빌드 방법과 "
            "실행 방법을 설명하는 README를 함께 작성해줘.\n"
            f"4. 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
            "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
            "구현해줘(절대 경로 하드코딩 금지).\n"
            "5. 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
            "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
            "환경변수로 받도록 구현해줘.\n"
        )
