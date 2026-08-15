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
from typing import AsyncIterator, Callable, Literal, Protocol, TYPE_CHECKING

from pathfinder.models import AgentEvent
from pathfinder.proto import prompts
from pathfinder.proto.design_sync import sync_design
from pathfinder.s3store import S3StoreLike

if TYPE_CHECKING:
    # 타입 힌트만을 위한 지연 import. design_profile.py는 session.py를 쓰지
    # 않아 순환은 아니지만, 이 모듈이 굳이 DesignProfileStore를 값으로
    # 들고 다닐 일은 없다 -- 세션은 저장소를 opaque하게 받아 load()만 부른다.
    from pathfinder.design_profile import DesignProfileStore

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        # 에이전트가 build_complete로 완료를 선언한 상태.
                        # "ready"와 다른 이유: ready는 "또 다른 턴을 받을 수
                        # 있다"이고 complete는 "이 세션은 할 일을 마쳤다"다.
                        # routes/prototypes.py의 _DEAD_STATUSES가 이 구분에
                        # 달려 있다.
                        "complete",
                        "failed", "closed"]

#: first_prompt()가 고르는 세 가지 개시 프롬프트.
#:   plan    -- 처음부터. 계획만 세우고 빌드하지 않는다.
#:   resume  -- 완료 선언 없이 죽은 세션을 이어받는다(트랜스크립트 전액).
#:   handoff -- 완료된 빌드를 개선한다(새 세션 + 요약만).
PromptKind = Literal["plan", "resume", "handoff"]


def has_build_output(build_dir: Path) -> bool:
    """빌드 디렉토리 아래 `prototype/`에 산출물이 있는가.

    "빌드됐다"의 단일 정의다. 세 곳이 이 질문을 하고, 전부 여기를 거쳐야
    한다 -- `first_prompt()`(무엇을 지시할지), `build_complete`
    도구(완료 선언을 받아줄지), 목록 라우트(카드를 built로 보일지). 기준이
    갈라지면 도구는 완료를 받아들이는데 목록은 built로 보이지 않는(또는 그
    반대) 상태가 된다.

    `prototype/`을 보고 빌드 디렉토리 자체를 보지 않는 것이 요점이다.
    `start()`가 에이전트보다 먼저 스펙 .md를 심고, 이전 호스팅 시도가
    `.proto-host.log`/`.pid`를 남길 수 있어서 -- 빌드 디렉토리가 있다는 건
    세션이 시작됐다는 뜻일 뿐 무언가 만들어졌다는 뜻이 아니다.

    직속 자식만 확인하고 재귀하지 않는다: node_modules/.next가 생긴 뒤에도
    매 목록 호출에서 싸게 유지된다.
    """
    proto_dir = build_dir / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


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
        language: str = "ko",
        idle_seconds: int | float = 1800,
        design_profiles: "DesignProfileStore | None" = None,
    ):
        self.project_id = project_id
        self.slug = slug
        self._s3 = s3
        self._build_root = Path(build_root)
        self._builder_factory = builder_factory
        self._semaphore = semaphore
        # 이 프로젝트의 생성물 언어. 개시 프롬프트와 build_complete 도구
        # 텍스트를 이 값으로 고른다(proto/prompts.py).
        self._language = language
        self._idle_seconds = idle_seconds
        # 프로필 저장소는 프로젝트 밖(버킷 루트)에 있어서 self._s3와 다른
        # 스토어다. None이면 브랜드 없이 돈다 -- 기능 전체가 opt-in이다.
        self._design_profiles = design_profiles

        self.status: SessionStatus = "starting"
        self._builder: BuilderLike | None = None
        self._session_id: str | None = None
        # first_prompt()가 고를 프롬프트 종류. 종전의 `_resumed` 불리언을
        # 대체한다 -- 분기가 셋이 되어 불리언으로 표현할 수 없다.
        self._prompt_kind: PromptKind = "plan"
        # handoff 분기일 때 프롬프트에 실을 내용({"summary","remaining"}).
        self._handoff: dict | None = None
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

    async def _resolve_session_id(self) -> tuple[str, bool, PromptKind]:
        """(session_id, resume, prompt_kind)를 돌려준다.

        세 분기가 있고, 각각 다른 사건을 표현한다:

          저장 없음          -> 새 id, resume 안 함, "plan"
          저장 있음, handoff 없음 -> 저장된 id resume, "resume"
          저장 있음 + handoff    -> 새 id, resume 안 함, "handoff"

        세 번째가 이 설계의 요점이다. 완료된 빌드를 개선할 때 전체
        트랜스크립트를 지고 가면 버튼 색 하나 바꾸는 요청에도 빌드 전체
        맥락이 실린다. 요약만 싣고 새로 시작한다.

        두 번째가 남는 이유: 완료 선언 **없이** 죽은 세션(유휴 타임아웃,
        백엔드 재시작)은 여전히 진짜 resume이 맞다. 그 세션은 할 일을
        마치지 않았고, 이어받을 맥락이 요약으로 대체될 수 없다.

        비-UUID 저장값은 없는 것으로 취급한다 -- SDK가 non-UUID resume을
        거부하므로, 레거시/손편집 값이 세션을 영구히 막지 못하게 한다.
        """
        try:
            saved = json.loads(await self._s3.get(self._session_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = None

        if not (isinstance(saved, dict) and _is_uuid(saved.get("session_id"))):
            new_id = str(uuid.uuid4())
            await self._s3.put(self._session_key(),
                               json.dumps({"session_id": new_id}))
            return new_id, False, "plan"

        handoff = await self._read_handoff()
        if handoff is None:
            return saved["session_id"], True, "resume"

        # 개선 세션: 새 id로 갈아타고 handoff를 소비한다.
        #
        # 순서가 중요하다 -- session.json 쓰기 먼저, handoff 삭제 나중.
        # 그 사이에서 실패하면 handoff가 남아 다음 시작이 다시 이 분기를
        # 타는데, session.json에는 이미 새(빈) id가 있으므로 개선
        # 프롬프트로 새로 시작한다: 같은 결과다. 반대 순서는 handoff를
        # 지운 뒤 id 쓰기가 실패하면 요약을 잃고 옛 세션을 전액 resume한다.
        # 손실 있는 방향을 피한다.
        self._handoff = handoff
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        # 단일 키 삭제에 delete_prefix를 쓴다 -- S3StoreLike에 단일 키
        # delete가 없고, 이것이 확립된 관례다(agent/pending_store.py:69,
        # survey/store.py:334).
        await self._s3.delete_prefix(self._handoff_key())
        return new_id, False, "handoff"

    async def _read_handoff(self) -> dict | None:
        """handoff.json -> {"summary","remaining"} 또는 None.

        _completion_from과 같은 fail-soft 규율이다. 깨진 handoff가 개선
        경로를 막아서는 안 된다 -- None으로 강등되면 두 번째 분기(전액
        resume)로 떨어지고, 그것은 무겁지만 정확한 degradation이다.
        """
        try:
            data = json.loads(await self._s3.get(self._handoff_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        remaining = data.get("remaining")
        return {"summary": summary,
                "remaining": remaining if isinstance(remaining, str) else ""}

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

        self._session_id, resume, self._prompt_kind = await self._resolve_session_id()

        # The agent reads the spec with its own file tools from cwd, so it has
        # to exist on local disk (the VM era pushed it over HTTP instead).
        # Refreshed on every start so a spec edited in Discovery is picked up.
        build_dir = self.build_dir()
        spec_path = build_dir / self._spec_key()
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_md, encoding="utf-8")

        # 브랜드 프로필을 워크스페이스에 반영한다. spec과 같은 이유로 매
        # start마다 새로 쓴다 -- admin이 고친 값이 이 세션부터 반영된다.
        profile = (await self._design_profiles.load()
                   if self._design_profiles is not None else None)
        sync_design(build_dir, profile, self._language)

        self._builder = self._builder_factory(self._session_id, resume)
        self.status = "ready"
        self._arm_idle_timer()

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        assert self._builder is not None, "start() must be called before send_message()"
        # 완료 선언된 세션은 새 턴을 받지 않는다. 오늘은 routes/prototypes.py의
        # _DEAD_STATUSES가 이 호출 전에 404로 막아주지만, 이 객체가 자기를
        # 지켜주는 호출자에게 의존해서는 안 된다 -- 라우트 우회(테스트, 미래의
        # 다른 진입점)가 있으면 아래 turn relay가 그대로 돌아 status를
        # "building"으로 되돌리고 완료 상태를 짓뭉갠다.
        #
        # self.status가 아니라 self._completion으로 가드하는 이유는 이
        # 모듈의 다른 모든 곳과 같다 -- _completion은 완료 선언이라는 사실
        # 자체이고 이 필드 외에는 아무도 되돌리지 않는다. status는 여러
        # 경로(예: 바로 이 메서드의 turn relay)가 다시 쓰는 가변 값이라 같은
        # 목적의 가드로 쓰면 이 가드 자신이 지키려는 바로 그 대입에 의해
        # 무력화된다.
        #
        # raise가 아니라 yield로 끝내는 이유: 이 메서드 끝의
        # `except Exception`은 mid-turn 실패를 세션 "failed"로 만들고 빌드
        # 슬롯을 놓아준다 -- 즉 그 경로는 "이 세션은 더 못 쓴다"는 뜻이다.
        # 완료된 세션은 정반대다: 할 일을 다 마친 정상 종료이고, 슬롯은 이미
        # 완료 처리 때 짧은 유예로 회수 절차에 들어가 있다. 여기서 raise하면
        # 정상 종료를 실패로 재분류하고 슬롯을 이중 해제(또는 남의 슬롯 해제)
        # 시도로 몰아간다. 그래서 빌더의 error 이벤트와 같은 모양의
        # 턴-레벨 오류를 yield하고 그냥 반환한다 -- 호출자(SSE 제너레이터)는
        # 평소 오류 턴과 똑같이 받고, 세션 상태는 "complete"로 그대로 남는다.
        if self._completion is not None:
            yield AgentEvent(
                kind="error",
                text=prompts.session_already_complete(self._language),
            )
            return
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
                        # 유예로 재무장한다. 인자를 넘기지 않는 것이 요점이다
                        # -- 지연은 _arm_idle_timer가 self._completion에서
                        # 파생하므로, 이 호출은 방금 세운 완료 상태를 읽어
                        # 짧은 유예를 집는다. handoff 쓰기 뒤에 두는 이유:
                        # 쓰기가 진행 중인 동안 유예 타이머가 먼저 만료돼
                        # close()가 끼어드는 경쟁을 피한다. 이 호출이 없으면
                        # send_message 진입 때 무장된 기본 유휴 타이머가
                        # 그대로 남아 세션이 30분간 닫히지 않는다(실측: 유예
                        # 테스트 2개 실패).
                        self._arm_idle_timer()
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
                # 생존 신호. 타이머의 의미가 "턴 진입 이후"에서 "마지막
                # 이벤트 이후"로 바뀌는 지점이다. 종전에는 30분을 넘는 빌드
                # 턴이 진행 중에 죽고, 질문 카드를 띄운 채 30분이 지나면
                # 답변 제출이 409가 됐다.
                #
                # 완료 유예를 되돌리지 않는다 -- _arm_idle_timer가 지연을
                # self._completion에서 파생시키므로, 완료 후의 done도 짧은
                # 유예를 유지한다.
                #
                # 비용: TimerHandle.cancel() + call_later 한 쌍이 빌드 한 번에
                # 수천 번 일어난다. 둘 다 힙 연산 하나짜리라 실질 비용은
                # 없지만, 이벤트마다 부르는 형태라는 점은 알고 있어야 한다.
                self._arm_idle_timer()
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
        """자동 발화되는 개시 턴. 세 가지 모양이고 `_prompt_kind`가 고른다.

        셋 다 같은 방식으로 끝난다 -- AskUserQuestion, 그리고 대기. 그
        도구만이 permission 콜백을 우리가 가로채는 유일한 도구여서, 질문하는
        것이 턴을 멈추고 선택지를 UI에 올리는 방법이기도 하다
        (builder._on_can_use_tool -> `questions` SSE 이벤트). 그리고 이
        문구가 유일한 브레이크다: 빌더는 bypassPermissions로 돌아 Write/Edit이
        자동 승인되므로, 그냥 시작해 버리는 에이전트를 이 텍스트 밖에서 막을
        방법이 없다.

        plan    -> 계획만 세워라, 아직 빌드하지 마라.
        resume  -> 트랜스크립트와 반쯤 만든 파일이 이미 맥락에 있다. 다시
                   계획하지 말고 무엇을 이어갈지 물어라.
        handoff -> 빌드는 끝났고 맥락은 요약뿐이다. 무엇을 개선할지 물어라.
        """
        if self._prompt_kind == "handoff" and self._handoff is not None:
            return self._handoff_prompt(self._handoff)
        if self._prompt_kind == "resume":
            return self._resume_prompt()
        return self._plan_prompt()

    def _plan_prompt(self) -> str:
        """문장 자체는 proto/prompts.py가 언어별로 갖고 있다."""
        return prompts.plan_prompt(
            self._language,
            spec_key=self._spec_key(),
            proxy_path=f"/api/proto/{self.project_id}/{self.slug}/")

    def _resume_prompt(self) -> str:
        """Deliberately short. The agent already has the prior transcript and
        whatever it built, so restating the spec or the build rules would only
        compete with what it can already see. All this turn has to do is stop
        it from picking a direction on its own.

        Unless the build tree is GONE, which the transcript cannot tell it --
        see `_missing_output_prompt`.

        문장 자체는 proto/prompts.py가 언어별로 갖고 있다.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.resume_prompt(self._language)

    def _missing_output_prompt(self) -> str:
        """산출물이 사라진 뒤의 개시 턴 — 찾지 말고 다시 만들라고 말한다.

        재개·개선 프롬프트는 둘 다 에이전트가 만든 코드가 아직 거기 있다고
        전제한다. 그 전제가 깨지는 경로가 둘 있고, 둘 다 정상 운영이다:
        프로토타입 리셋(로컬 트리를 rmtree한다)과 호스팅 인스턴스 교체(빌드
        트리는 EBS에 있고 S3 세션만 살아남는다).

        그때 상태를 알려주지 않으면 에이전트는 트랜스크립트를 믿고 없는 코드를
        찾아 나선다. 실측: 리셋된 프로토타입에서 작업 디렉토리 → 다른 프로토타입
        디렉토리 → `/opt/pathfinder/frontend` → 파일시스템 전체로 탐색을 넓히며
        19초 이상을 태웠고, 성공할 수 없는 탐색이었다 -- 트리는 삭제됐다.

        스펙을 다시 읽히는 것이 요점이다. 트랜스크립트의 기억은 요약이 아니라
        대화 기록이고, 거기서 코드를 복원할 수는 없다. 스펙은 S3에 살아 있고
        `start()`가 매번 로컬에 새로 심는다 -- 처음 빌드와 같은 입력이다.

        문장 자체는 proto/prompts.py가 언어별로 갖고 있다.
        """
        return prompts.missing_output_prompt(self._language,
                                             spec_key=self._spec_key())

    def _handoff_prompt(self, handoff: dict) -> str:
        """완료된 빌드를 개선하는 새 세션의 개시 턴.

        `_resume_prompt`보다도 짧다. 파일 트리를 넘기지 않는 것이 의도적이다
        -- 에이전트가 자기 파일 도구로 cwd를 읽는 편이 스냅샷보다 정확하고,
        그게 이미 스펙을 읽는 방식이다. 여기서 할 일은 이전 빌드가 무엇을
        남겼는지 알려주고, 마음대로 손대지 않게 막는 것뿐이다.

        단, 남긴 것이 실제로 없을 수 있다. handoff.json은 S3에 있고 빌드
        트리는 로컬 디스크에 있어서 -- 인스턴스가 교체되면 요약만 살아남는다.
        "이미 빌드가 완료됐다"고 말하면서 없는 `prototype/`을 살펴보게 하는
        것이 정확히 그 탐색을 유발한다(`_missing_output_prompt` 참조).

        문장 자체는 proto/prompts.py가 언어별로 갖고 있다.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.handoff_prompt(
            self._language,
            spec_key=self._spec_key(),
            summary=handoff["summary"],
            remaining=handoff.get("remaining")
            or prompts.missing_remaining_note(self._language))


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
