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
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Callable, Literal, Protocol

from pathfinder.models import AgentEvent
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        # 에이전트가 build_complete로 완료를 선언한 상태.
                        # "ready"와 다른 이유: ready는 "또 다른 턴을 받을 수
                        # 있다"이고 complete는 "이 세션은 할 일을 마쳤다"다.
                        # routes/prototypes.py의 _DEAD_STATUSES가 이 구분에
                        # 달려 있다.
                        "complete",
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


#: 완료 선언 뒤 세션이 스스로 닫히기까지의 유예. 0이 아닌 이유: terminal
#: 이벤트가 제너레이터 체인(_relay_queue -> run -> send_message -> gen)을
#: 빠져나갈 여유가 필요하다.
_COMPLETION_GRACE_SECONDS = 5


def _completion_from(payload: str | None) -> dict | None:
    """build_complete payload -> {"summary","remaining"} 또는 None.

    _interrupt_id_from과 같은 fail-soft 규율이다 — 깨진 payload는 예외가
    아니라 None으로 강등된다. 완료 처리가 일어나지 않으면 유휴 타이머가
    평소대로 정리하므로, 잘못 선언된 완료보다 안전한 방향이다.
    """
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary:
        return None
    remaining = data.get("remaining")
    return {"summary": summary,
            "remaining": remaining if isinstance(remaining, str) else ""}


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
        # 완료 선언의 내용({"summary","remaining"}) 또는 None. 두 가지를
        # 동시에 뜻한다: (1) 이 세션은 할 일을 마쳤다, (2) 유휴 타이머는
        # 짧은 유예를 써야 한다(_arm_idle_timer 참조).
        self._completion: dict | None = None
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

    def _handoff_key(self) -> str:
        return f"prototypes/{self.slug}/handoff.json"

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
        """유휴 타이머를 재무장한다. 지연 값은 호출자가 아니라 여기서
        결정한다 -- 그것이 이 설계에서 가장 틀리기 쉬운 부분이다.

        호출자가 인자로 넘기는 형태였다면, 완료 선언이 짧은 유예로 무장한
        직후 뒤따르는 done이 기본 30분으로 되돌려 세션이 닫히지 않는다.
        build_complete 다음에는 **반드시** done이 오므로(run()의 terminal
        held 규율) 이것은 가능성이 아니라 확정된 동작이다. 지연을 상태에서
        파생시키면 그 창이 존재하지 않는다.
        """
        delay = (_COMPLETION_GRACE_SECONDS if self._completion is not None
                 else self._idle_seconds)
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(delay, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    async def _write_handoff(self, completion: dict) -> None:
        """다음 세션이 읽을 핸드오프. 개선 작업이 전체 트랜스크립트를 지고
        가지 않아도 되게 하는 유일한 근거다(_resolve_session_id의 세 번째
        분기).

        completed_at은 진단용이다 -- 어느 분기를 탔는지 로그에서 읽을 수
        있게 한다.
        """
        await self._s3.put(self._handoff_key(), json.dumps({
            **completion,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))

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
                elif event.kind == "build_complete":
                    completion = _completion_from(event.payload)
                    if completion is not None:
                        self._completion = completion
                        self.status = "complete"
                        # 완료 선언 이 순간에 유예로 재무장한다. 이 호출이
                        # 없으면 이번 턴 맨 앞에서 무장된 기존 유휴 지연
                        # (수십 분)이 그대로 남아, 뒤따르는 done이 세션을
                        # 절대 닫지 못한다 -- _arm_idle_timer는 호출될 때만
                        # 지연을 다시 계산하므로, 상태가 바뀐 지금 다시
                        # 불러줘야 한다.
                        self._arm_idle_timer()
                        # 예외를 반드시 삼킨다. 그러지 않으면 아래의
                        # `except Exception`이 잡아 status="failed" + 슬롯
                        # release로 가는데, 그것은 "handoff 실패에도 완료는
                        # 진행한다"는 결정과 정반대다. S3 실패가 완성된
                        # 빌드를 실패로 보이게 만들면 안 된다.
                        try:
                            await self._write_handoff(completion)
                        except Exception:
                            _log.exception("handoff write failed: %s/%s",
                                           self.project_id, self.slug)
                elif event.kind in ("done", "error"):
                    # 완료를 선언한 세션은 ready로 돌아가지 않는다.
                    # build_complete 다음에는 반드시 done이 오므로, 이 가드가
                    # 없으면 status가 되돌아가 _DEAD_STATUSES 기구 전체가
                    # 무력해진다(호스팅이 다시 409, 개선 세션을 열 수 없다).
                    #
                    # error도 같이 묶는 이유: 완료 선언 뒤 error가 온다면
                    # 그것도 이 세션을 ready로 만들 근거가 아니다. 완료
                    # 전이라면 종전대로 재시도 가능한 상태로 남는다.
                    if self._completion is None:
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
            "- 프로토타입이 완성되면 **`build_complete` 도구로 완료를 선언해줘.** "
            "무엇을 만들었는지 요약(summary)과, 남은 작업이나 알려진 한계가 있으면 "
            "remaining에 적어줘. 이 선언 뒤 빌드 세션이 종료되니, 아직 작업이 "
            "남았으면 선언하지 말고 계속 진행해줘.\n"
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


async def purge_session_state(s3, slug: str) -> None:
    """Delete the S3 state this module owns for one prototype: the durable
    session id, the build transcript, and the legacy bundle/ backup.

    A module function, not a method: once a build finishes the session is
    evicted from `proto_sessions` (the normal resting state), so anything
    hanging off an instance could not reach the very prototypes that most need
    resetting.

    Scoped to `prototypes/{slug}/` and therefore never touches the spec, which
    lives under aiplc-docs/ -- deleting that would remove the card from the
    list instead of resetting it. Idempotent: absent keys are a no-op.

    Callers MUST run SurveyStore.purge() BEFORE this: the survey tree lives
    under this same prefix, and reclaiming its token indexes requires reading
    the questionnaires that this call would delete.
    """
    await s3.delete_prefix(f"prototypes/{slug}/")
