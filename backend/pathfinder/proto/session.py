# backend/pathfinder/proto/session.py — PrototypeSession: one prototype build
# session's orchestration (VM boot -> file push -> turn relay -> idle timer ->
# S3 bundle sync + VM stop on close). No AWS/HTTP here directly -- this class
# only calls the controller/harness/S3 seams it is given (all duck-typed via
# the *Like Protocols below), so it is fully unit-testable with fakes.
from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Callable, Literal, Protocol

from pathfinder.models import AgentEvent
from pathfinder.proto.vm import BootSpec, VMHandle
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                         "failed", "closed"]

# Path/prefix contract (spec §4): PROTOTYPE-*.md lives under the project's
# aiplc-docs subtree; the built bundle is pulled to prototypes/{slug}/bundle/;
# the build-rule detail file is baked into the harness image under
# aiplc-rules/ but this session pushes it explicitly too (rebuild-safe, and
# lets the rule content be updated without a new image).
_RULE_REL = Path("aws-aiplc-rule-details") / "discovery" / "prototype-building.md"
_RULE_PUSH_PATH = "aiplc-rules/aws-aiplc-rule-details/discovery/prototype-building.md"
_PROTOTYPE_PREFIX = "prototype/"
# Build-artifact directories never round-trip through S3 -- they're huge,
# reproducible from source, and would blow up bundle storage/restore time.
_EXCLUDED_SEGMENTS = {"node_modules", ".next", ".git"}


class MicroVMControllerLike(Protocol):
    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle: ...
    async def stop(self, handle: VMHandle) -> None: ...
    async def status(self, handle: VMHandle) -> str: ...


class HarnessClientLike(Protocol):
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def send_answers(self, interrupt_id: str, answers: dict[str, str]) -> bool: ...
    async def interrupt(self) -> None: ...
    async def pending(self) -> str | None: ...
    async def read_file(self, rel_path: str) -> str: ...
    async def write_file(self, rel_path: str, content: str) -> None: ...
    async def list_files(self, glob: str) -> list[str]: ...
    async def heartbeat(self) -> bool: ...


def _interrupt_id_from(payload: str | None) -> str | None:
    """Parse the interrupt id out of a questions payload. Mirrors
    runner.py:19-26 -- a malformed/contract-drifted payload must degrade
    (None) rather than blow up the turn relay."""
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _is_excluded(rel_path: str) -> bool:
    return any(seg in _EXCLUDED_SEGMENTS for seg in PurePosixPath(rel_path).parts)


class PrototypeSession:
    """One prototype's build session: boots a MicroVM, relays chat turns
    through its harness, and owns the idle timer + final S3 bundle sync.

    The session owns the questions/answers interrupt id (never the caller) --
    same pattern as AgentRunner (runner.py:99-116): a `questions` event's
    payload is captured as it passes through send_message, and consumed by
    send_answers.
    """

    def __init__(
        self,
        project_id: str,
        slug: str,
        s3: S3StoreLike,
        controller: MicroVMControllerLike,
        spec: BootSpec,
        harness_factory: Callable[[str, dict], HarnessClientLike],
        rules_dir: Path,
        idle_seconds: int | float = 1800,
        token_minter: Callable[[str], dict] | None = None,
    ):
        self.project_id = project_id
        self.slug = slug
        self._s3 = s3
        self._controller = controller
        self._spec = spec
        self._harness_factory = harness_factory
        self._rules_dir = Path(rules_dir)
        self._idle_seconds = idle_seconds
        self._token_minter = token_minter

        self.status: SessionStatus = "starting"
        self._handle: VMHandle | None = None
        self._harness: HarnessClientLike | None = None
        self._pending_interrupt_id: str | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._closed = False

    # ---- path/prefix helpers ----

    def _spec_key(self) -> str:
        return f"aiplc-docs/discovery/prototypes/{self.slug}/PROTOTYPE-{self.slug}.md"

    def _bundle_prefix(self) -> str:
        return f"prototypes/{self.slug}/bundle/"

    # ---- idle timer: re-armed on turn start + answers submission ----

    def _arm_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(self._idle_seconds, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    async def _mint_headers(self, vm_id: str) -> dict[str, str]:
        # No minter configured -> no auth header (FakeMicroVMController's
        # "fake-*" handles hit this path; mirrors the historical app.py
        # _harness_token_provider's "never mint for fake-* vm_ids" rule).
        if self._token_minter is None:
            return {}
        # mint_harness_token is a sync, blocking boto3 call -- run off-loop.
        return await asyncio.to_thread(self._token_minter, vm_id)

    # ---- start: boot -> push spec/rule/bundle -> ready ----

    async def start(self) -> None:
        spec_key = self._spec_key()
        spec_md = await self._s3.get(spec_key)  # FileNotFoundError propagates (-> route 404)

        self._handle = await self._controller.boot(self.project_id, self._spec)
        headers = await self._mint_headers(self._handle.vm_id)
        self._harness = self._harness_factory(self._handle.base_url, headers)

        await self._harness.write_file(spec_key, spec_md)

        rule_path = self._rules_dir / _RULE_REL
        rule_body = rule_path.read_text(encoding="utf-8")
        await self._harness.write_file(_RULE_PUSH_PATH, rule_body)

        bundle_prefix = self._bundle_prefix()
        for key in await self._s3.list(bundle_prefix):
            rel = key[len(bundle_prefix):]
            content = await self._s3.get(key)
            await self._harness.write_file(f"{_PROTOTYPE_PREFIX}{rel}", content)

        self.status = "ready"
        self._arm_idle_timer()

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        assert self._harness is not None, "start() must be called before send_message()"
        self._arm_idle_timer()
        self.status = "building"
        try:
            async for event in self._harness.send_message(text):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                        self.status = "waiting_input"
                elif event.kind == "done":
                    self.status = "ready"
                elif event.kind == "error":
                    # Sanitized turn-level error: session stays usable/retryable
                    # (spec §6 "세션 유지, 재시도 가능") -- NOT a session failure.
                    self.status = "ready"
                yield event
        except Exception:
            # Anything raised out of the relay itself (vs. a yielded `error`
            # event) means the turn never reached a clean terminal event --
            # e.g. a dead VM/transport -- so the session is not retryable
            # as-is (spec §6 "턴 중 VM 죽음/네트워크 단절 -> 세션 failed").
            self.status = "failed"
            raise

    async def send_answers(self, answers: dict[str, str]) -> bool:
        assert self._harness is not None, "start() must be called before send_answers()"
        if self._pending_interrupt_id is None:
            return False
        interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
        ok = await self._harness.send_answers(interrupt_id, answers)
        if not ok:
            return False
        self._arm_idle_timer()
        self.status = "building"
        return True

    async def interrupt(self) -> None:
        if self._harness is not None:
            await self._harness.interrupt()

    # ---- close: pull prototype/** -> S3 bundle/, exclude build artifacts, stop VM ----

    async def _sync_bundle_to_s3(self) -> None:
        assert self._harness is not None
        bundle_prefix = self._bundle_prefix()
        for path in await self._harness.list_files(f"{_PROTOTYPE_PREFIX}**/*"):
            if not path.startswith(_PROTOTYPE_PREFIX):
                continue
            rel = path[len(_PROTOTYPE_PREFIX):]
            if _is_excluded(rel):
                continue
            content = await self._harness.read_file(path)
            await self._s3.put(f"{bundle_prefix}{rel}", content)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

        sync_ok = True
        if self._harness is not None:
            try:
                await self._sync_bundle_to_s3()
            except Exception:
                _log.exception("prototype bundle sync failed: %s/%s",
                               self.project_id, self.slug)
                sync_ok = False

        if self._handle is not None:
            await self._controller.stop(self._handle)

        self.status = "closed" if sync_ok else "failed"

    # ---- first turn's auto-spoken prompt (spec §4's five directives) ----

    def first_prompt(self) -> str:
        spec_key = self._spec_key()
        proxy_path = f"/api/proto/{self.project_id}/{self.slug}/"
        return (
            f"`{spec_key}` 파일을 읽고, 그 내용에 따라 프로토타입을 빌드해줘.\n\n"
            "지침:\n"
            f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악한 뒤 빌드를 시작해줘.\n"
            "2. 진행 중 불확실하거나 결정이 필요한 사항이 있으면 마음대로 넘기지 말고, "
            "AskUserQuestion으로 나에게 먼저 물어봐줘.\n"
            "3. 완성물은 반드시 `/workspace/prototype/` 아래에 두고, 빌드 방법과 실행 "
            "방법을 설명하는 README를 함께 작성해줘.\n"
            f"4. 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
            "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
            "구현해줘(절대 경로 하드코딩 금지).\n"
            "5. 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
            "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
            "환경변수로 받도록 구현해줘.\n"
        )
