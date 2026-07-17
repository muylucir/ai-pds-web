# backend/pathfinder/sandbox/microvm_control.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

VMStatus = Literal["booting", "ready", "suspended", "stopped", "expired"]

@dataclass
class BootSpec:
    """Everything needed to boot a Claude Code MicroVM (spec §1, §6).

    Backed by the AWS Lambda MicroVMs API: `image_id` -> --image-identifier,
    `exec_role_arn` -> --execution-role-arn, idle_policy() -> --idle-policy.
    Auth is via the MicroVM IAM execution role (CLAUDE_CODE_USE_BEDROCK); there
    are NO long-lived keys. `anthropic_model` pins Claude Code to Sonnet 5 via
    the Bedrock inference-profile id (confirmed "global.anthropic.claude-sonnet-5"
    in ap-northeast-1 on 2026-07-17); injected from env, re-verified in Task 7,
    never hardcoded here.
    """
    region: str = "ap-northeast-1"
    image_id: str | None = None
    exec_role_arn: str | None = None
    anthropic_model: str | None = None
    max_idle_seconds: int = 300
    suspended_duration_seconds: int = 1800
    auto_resume: bool = True

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": self.region,
        }
        if self.anthropic_model:
            env["ANTHROPIC_MODEL"] = self.anthropic_model
        return env

    def idle_policy(self) -> dict:
        # Exact shape run-microvm --idle-policy expects.
        return {
            "maxIdleDurationSeconds": self.max_idle_seconds,
            "suspendedDurationSeconds": self.suspended_duration_seconds,
            "autoResumeEnabled": self.auto_resume,
        }

@dataclass
class VMHandle:
    vm_id: str
    base_url: str
    status: VMStatus

class MicroVMController(ABC):
    """Control-plane for the Lambda MicroVM lifecycle. The concrete AWS binding
    is LambdaMicroVMController (microvm_control_aws.py); tests use
    FakeMicroVMController. MicroVMSandbox depends only on this ABC."""

    @abstractmethod
    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle: ...
    @abstractmethod
    async def resume(self, handle: VMHandle) -> VMHandle: ...
    @abstractmethod
    async def suspend(self, handle: VMHandle) -> None: ...
    @abstractmethod
    async def stop(self, handle: VMHandle) -> None: ...
    @abstractmethod
    async def status(self, handle: VMHandle) -> VMStatus: ...

@dataclass
class FakeMicroVMController(MicroVMController):
    """In-memory controller for unit tests. Points every VM at `base_url`
    (a fake harness). Records call counts and tracks status."""
    base_url: str
    boot_calls: int = 0
    resume_calls: int = 0
    suspend_calls: int = 0
    stop_calls: int = 0
    _status: dict[str, VMStatus] = field(default_factory=dict)

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        self.boot_calls += 1
        vm_id = f"fake-{project_id}-{self.boot_calls}"
        self._status[vm_id] = "ready"
        return VMHandle(vm_id=vm_id, base_url=self.base_url, status="ready")

    async def resume(self, handle: VMHandle) -> VMHandle:
        self.resume_calls += 1
        self._status[handle.vm_id] = "ready"
        return VMHandle(vm_id=handle.vm_id, base_url=handle.base_url, status="ready")

    async def suspend(self, handle: VMHandle) -> None:
        self.suspend_calls += 1
        self._status[handle.vm_id] = "suspended"

    async def stop(self, handle: VMHandle) -> None:
        self.stop_calls += 1
        self._status[handle.vm_id] = "stopped"

    async def status(self, handle: VMHandle) -> VMStatus:
        return self._status.get(handle.vm_id, "stopped")

    def simulate_auto_suspend(self, handle: VMHandle) -> None:
        """Emulate AWS auto-suspend after max_idle_seconds: the control plane
        now reports 'suspended' while the caller's cached VMHandle still says
        'ready'. This is the exact stale-handle condition Finding A targets."""
        self._status[handle.vm_id] = "suspended"

    def simulate_expiry(self, handle: VMHandle) -> None:
        """Emulate MicroVM expiry (max 8h) / crash: control plane reports
        'expired'; the VM and its filesystem are gone."""
        self._status[handle.vm_id] = "expired"
