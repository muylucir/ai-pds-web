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
        # Whether start() restored a prior transcript. first_prompt() branches
        # on this: a resumed agent already has its plan and its half-built
        # files in context, so re-sending the from-scratch planning order
        # would send it back to square one.
        self._resumed = False
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
        self._resumed = resume

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
        """The auto-spoken opening turn. Two shapes, chosen by `_resumed`.

        Both end the same way -- AskUserQuestion, then wait -- because that
        tool is the ONE whose permission callback we intercept, so asking is
        also what suspends the turn and surfaces the choice to the UI
        (builder._on_can_use_tool -> `questions` SSE event). And the wording
        is the only brake there is: the builder runs under
        `bypassPermissions`, so Write/Edit are auto-approved and nothing
        outside this text can stop an agent that decides to just start.

        Fresh -> plan it, don't build yet.
        Resumed -> the transcript and the half-built files are already in
        context; ask what to continue with instead of re-planning.
        """
        if self._resumed:
            return self._resume_prompt()
        return self._plan_prompt()

    def _plan_prompt(self) -> str:
        spec_key = self._spec_key()
        proxy_path = f"/api/proto/{self.project_id}/{self.slug}/"
        return (
            f"`{spec_key}` 파일을 읽고, 프로토타입 구현 계획을 세워줘.\n"
            "**이번 턴에서는 계획만 세우고 빌드는 시작하지 마.**\n\n"
            "진행 방식:\n"
            f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악해줘.\n"
            "2. 그다음 구현 계획을 제시해줘. 기술 스택, 만들 화면/기능 목록, "
            "파일 구조, 작업 순서를 포함하고, 스펙에서 애매했던 부분과 네가 임의로 "
            "가정한 내용도 함께 밝혀줘.\n"
            "3. 계획을 제시한 뒤 **반드시 AskUserQuestion으로 이 계획대로 실행할지, "
            "수정할 부분이 있는지 물어보고 내 답을 기다려줘.** 승인 없이 다음 단계로 "
            "넘어가면 안 돼.\n"
            "4. 계획 단계에서는 파일을 만들거나 수정하지 마(Write/Edit 금지). "
            "스펙을 읽는 것 외에는 아무것도 건드리지 말고, 계획은 메시지 본문으로만 "
            "보여줘.\n"
            "5. 내가 승인한 뒤에 빌드를 시작해줘. 빌드 중에도 불확실하거나 결정이 "
            "필요한 사항이 있으면 마음대로 넘기지 말고 AskUserQuestion으로 먼저 "
            "물어봐줘.\n\n"
            "빌드 단계에서 지킬 것(승인 후 적용):\n"
            "- 완성물은 반드시 작업 디렉토리 아래 `prototype/`에 두고, 빌드 방법과 "
            "실행 방법을 설명하는 README를 함께 작성해줘.\n"
            f"- 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
            "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
            "구현해줘(절대 경로 하드코딩 금지).\n"
            "- 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
            "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
            "환경변수로 받도록 구현해줘.\n"
        )

    def _resume_prompt(self) -> str:
        """Deliberately short. The agent already has the prior transcript and
        whatever it built, so restating the spec or the build rules would only
        compete with what it can already see. All this turn has to do is stop
        it from picking a direction on its own."""
        return (
            "이전 빌드 세션을 이어서 진행한다.\n"
            "**아직 아무것도 빌드하거나 수정하지 마.**\n\n"
            "1. 지금까지 진행한 내용과 남은 작업을 짧게 정리해줘.\n"
            "2. 그다음 **AskUserQuestion으로 이번에 무엇을 진행할지 물어보고 내 답을 "
            "기다려줘.** 남은 작업을 이어서 할지, 다른 것을 먼저 할지 내가 고를 수 "
            "있게 선택지를 제시해줘.\n"
            "3. 내가 고른 뒤에 작업을 시작해줘.\n"
        )
