# backend/pathfinder/proto/vm.py
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

import boto3

VMStatus = Literal["booting", "ready", "suspended", "stopped", "expired"]


@dataclass
class BootSpec:
    """Everything needed to boot a Claude Code MicroVM.

    Backed by the AWS Lambda MicroVMs API: `image_id` -> --image-identifier,
    `exec_role_arn` -> --execution-role-arn, idle_policy() -> --idle-policy.
    Auth is via the MicroVM IAM execution role (CLAUDE_CODE_USE_BEDROCK); there
    are NO long-lived keys. `anthropic_model` pins Claude Code to a Bedrock
    inference-profile id; injected from env, never hardcoded here.

    suspend/resume are NOT supported by this controller (boot/stop/status
    only), so the historical `suspended_duration_seconds`/`auto_resume`
    fields are dropped here as caller-configurable knobs. However, the real
    lambda-microvms RunMicrovm service model (confirmed via botocore's
    ParamValidator, not a guess) requires `suspendedDurationSeconds` and
    `autoResumeEnabled` as part of `idlePolicy` regardless -- AWS applies
    idle-suspend/auto-resume at the platform level whether or not THIS
    controller ever calls suspend_microvm/resume_microvm itself. So
    idle_policy() still sends both, pinned to fixed constants rather than
    exposed as BootSpec fields: autoResumeEnabled=True means a suspended VM
    is transparently woken by AWS on the next request to its endpoint (no
    resume_microvm call needed from us), which is what makes it safe to drop
    the resume() method entirely.
    """
    region: str = "ap-northeast-1"
    image_id: str | None = None
    exec_role_arn: str | None = None
    anthropic_model: str | None = None
    max_idle_seconds: int = 300

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": self.region,
        }
        if self.anthropic_model:
            env["ANTHROPIC_MODEL"] = self.anthropic_model
        return env

    def idle_policy(self) -> dict:
        # Exact shape run-microvm --idle-policy expects. suspendedDurationSeconds
        # and autoResumeEnabled are required by the service model even though
        # this controller has no suspend()/resume() methods -- see docstring.
        return {
            "maxIdleDurationSeconds": self.max_idle_seconds,
            "suspendedDurationSeconds": 1800,
            "autoResumeEnabled": True,
        }


@dataclass
class VMHandle:
    vm_id: str
    base_url: str
    status: VMStatus


_STATUS_MAP: dict[str, VMStatus] = {
    "PENDING": "booting",
    "STARTING": "booting",
    "RUNNING": "ready",
    "SUSPENDED": "suspended",
    "TERMINATED": "expired",
    "EXPIRED": "expired",
}


def _map_status(raw: str) -> VMStatus:
    """get-microvm status string -> our VMStatus. Unknown -> 'stopped' (the
    conservative reboot-worthy state).

    The real lambda-microvms (2025-09-09) service model's MicrovmState enum
    is PENDING/RUNNING/SUSPENDING/SUSPENDED/TERMINATING/TERMINATED -- it has
    neither STARTING nor EXPIRED. Both are kept in this table anyway as
    harmless forward-compat entries (mapping to "booting" and "expired"
    respectively): _map_status is a pure string->string function with no
    service-model binding, so an entry AWS never actually emits costs nothing
    and only helps if a future API revision introduces it.

    SUSPENDING and TERMINATING -- states the real enum DOES emit -- are not
    yet in this table and so fall through to the "stopped" default. This
    controller never calls suspend/resume itself, but AWS's idle policy can
    still auto-suspend a VM out of band, so SUSPENDED is kept in the map even
    though there is no resume() here to act on it -- status() should still
    report the true state rather than mask it as "stopped".
    """
    return _STATUS_MAP.get(raw, "stopped")


class LambdaMicroVMController:
    """AWS Lambda MicroVMs control-plane binding (ap-northeast-1).

    boot() polls get-microvm until RUNNING before returning, so a cached
    VMHandle is only ever created in the 'ready' state. boto3 is sync, so
    each call is wrapped in asyncio.to_thread (same pattern as S3Store).

    Response-shape note: the real service model returns a flat
    GetMicrovmResponse/RunMicrovmResponse (microvmId, state, endpoint, ...),
    not a nested {"microvm": {"status": ...}} shape.
    """

    def __init__(self, region: str = "ap-northeast-1", client=None,
                 boot_timeout_seconds: float = 120.0, poll_interval_seconds: float = 3.0):
        self.region = region
        self._client = client
        self._boot_timeout = boot_timeout_seconds
        self._poll = poll_interval_seconds

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("lambda-microvms", region_name=self.region)
        return self._client

    @staticmethod
    def _endpoint_url(endpoint: str) -> str:
        # run/get-microvm return a bare host (e.g. "<id>.lambda-microvm.<region>.on.aws")
        # with no scheme; HarnessClient builds "{base_url}/message" and httpx
        # requires a scheme. The proxy is TLS-terminated, so default to https.
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"https://{endpoint}"

    async def _get_microvm(self, vm_id: str) -> dict:
        return await asyncio.to_thread(self.client.get_microvm, microvmIdentifier=vm_id)

    async def _poll_until_running(self, vm_id: str) -> dict:
        deadline = time.monotonic() + self._boot_timeout
        while True:
            resp = await self._get_microvm(vm_id)
            raw = resp["state"]
            if _map_status(raw) == "ready":
                return resp
            if time.monotonic() >= deadline:
                raise RuntimeError(f"microvm {vm_id} did not reach RUNNING (last status {raw})")
            await asyncio.sleep(self._poll)

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        resp = await asyncio.to_thread(
            self.client.run_microvm,
            imageIdentifier=spec.image_id,
            executionRoleArn=spec.exec_role_arn,
            idlePolicy=spec.idle_policy(),
        )
        vm_id = resp["microvmId"]
        base_url = self._endpoint_url(resp["endpoint"])
        await self._poll_until_running(vm_id)
        return VMHandle(vm_id=vm_id, base_url=base_url, status="ready")

    async def stop(self, handle: VMHandle) -> None:
        await asyncio.to_thread(self.client.terminate_microvm, microvmIdentifier=handle.vm_id)

    async def status(self, handle: VMHandle) -> VMStatus:
        resp = await self._get_microvm(handle.vm_id)
        return _map_status(resp["state"])


@dataclass
class FakeMicroVMController:
    """In-memory controller for unit tests. Points every VM at `base_url`
    (a fake harness). Records call counts and tracks status."""
    base_url: str
    boot_calls: int = 0
    stop_calls: int = 0
    _status: dict[str, VMStatus] = field(default_factory=dict)

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        self.boot_calls += 1
        vm_id = f"fake-{project_id}-{self.boot_calls}"
        self._status[vm_id] = "ready"
        return VMHandle(vm_id=vm_id, base_url=self.base_url, status="ready")

    async def stop(self, handle: VMHandle) -> None:
        self.stop_calls += 1
        self._status[handle.vm_id] = "stopped"

    async def status(self, handle: VMHandle) -> VMStatus:
        return self._status.get(handle.vm_id, "stopped")


def mint_harness_token(vm_id: str, region: str = "ap-northeast-1", client=None,
                       port: int = 8080, minutes: int = 30) -> dict[str, str]:
    """Mint a short-lived JWE via CreateMicrovmAuthToken and return it as the
    harness auth header. Max TTL is 60 min; we use 30.

    CreateMicrovmAuthTokenResponse.authToken is a map (TokenParts), not a bare
    string -- per the service docs: "Use the value at key 'X-aws-proxy-auth'
    as the header value when connecting to the MicroVM endpoint."""
    # This is a sync, blocking boto3 call (this function is itself sync, not
    # async, unlike LambdaMicroVMController's methods) -- callers must wrap
    # this call in asyncio.to_thread, the same pattern used throughout this
    # module.
    c = client if client is not None else boto3.client("lambda-microvms", region_name=region)
    resp = c.create_microvm_auth_token(
        microvmIdentifier=vm_id,
        expirationInMinutes=minutes,
        allowedPorts=[{"port": port}],
    )
    return {"X-aws-proxy-auth": resp["authToken"]["X-aws-proxy-auth"]}
