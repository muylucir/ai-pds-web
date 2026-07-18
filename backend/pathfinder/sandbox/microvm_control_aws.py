# backend/pathfinder/sandbox/microvm_control_aws.py
from __future__ import annotations
import asyncio
import time
import boto3
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle, VMStatus

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
    (confirmed straight from the service-2.json shape, not a drill) is
    PENDING/RUNNING/SUSPENDING/SUSPENDED/TERMINATING/TERMINATED -- it has
    neither STARTING nor EXPIRED. Both are kept in this table anyway as
    harmless forward-compat entries (mapping to "booting" and "expired"
    respectively): _map_status is a pure string->string function with no
    service-model binding, so an entry AWS never actually emits costs nothing
    and only helps if a future API revision introduces it.

    SUSPENDING and TERMINATING -- states the real enum DOES emit -- are not
    yet in this table and so fall through to the "stopped" default. This is
    the conservative reboot path and is data-safe (workspace state persists
    in S3 via S3Store, restored on next boot), but it does forfeit the
    resume-without-reboot fast path for a MicroVM that is merely mid-suspend.
    Whether SUSPENDING should instead map to "booting" (or a new status) is
    an open drill item for Task 6+, not resolved here."""
    return _STATUS_MAP.get(raw, "stopped")


class LambdaMicroVMController(MicroVMController):
    """AWS Lambda MicroVMs control-plane binding (ap-northeast-1, GA 2026-06-22).

    boot()/resume() poll get-microvm until RUNNING before returning, so a
    cached VMHandle is only ever created in the 'ready' state — MicroVMSandbox's
    _ensure_ready therefore never observes a transient 'booting' on a handle it
    just created (Part-2 'booting falls-through' resolution). boto3 is sync, so
    each call is wrapped in asyncio.to_thread (same pattern as S3Store).

    Response-shape note: the real service model returns a flat
    GetMicrovmResponse/RunMicrovmResponse (microvmId, state, endpoint, ...),
    not the plan's originally-guessed nested {"microvm": {"status": ...}}.
    ResumeMicrovmResponse carries no members at all (not even "endpoint"), so
    resume() must poll get_microvm to learn the post-resume endpoint.
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
        # (Confirmed against a real boot: GET https://<endpoint>/health -> 200.)
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

    async def resume(self, handle: VMHandle) -> VMHandle:
        await asyncio.to_thread(
            self.client.resume_microvm, microvmIdentifier=handle.vm_id
        )
        # ResumeMicrovmResponse has no members -- the new endpoint is only
        # available from the subsequent get_microvm poll.
        final = await self._poll_until_running(handle.vm_id)
        ep = final.get("endpoint")
        base_url = self._endpoint_url(ep) if ep else handle.base_url
        return VMHandle(vm_id=handle.vm_id, base_url=base_url, status="ready")

    async def suspend(self, handle: VMHandle) -> None:
        await asyncio.to_thread(self.client.suspend_microvm, microvmIdentifier=handle.vm_id)

    async def stop(self, handle: VMHandle) -> None:
        await asyncio.to_thread(self.client.terminate_microvm, microvmIdentifier=handle.vm_id)

    async def status(self, handle: VMHandle) -> VMStatus:
        resp = await self._get_microvm(handle.vm_id)
        return _map_status(resp["state"])


def mint_harness_token(vm_id: str, region: str = "ap-northeast-1", client=None,
                       port: int = 8080, minutes: int = 30) -> dict[str, str]:
    """Mint a short-lived JWE via CreateMicrovmAuthToken and return it as the
    harness auth header. Called per handle transition (mint-on-resume) by
    app.py's harness_factory. Max TTL is 60 min; we use 30.

    CreateMicrovmAuthTokenResponse.authToken is a map (TokenParts), not a bare
    string -- per the service docs: "Use the value at key 'X-aws-proxy-auth'
    as the header value when connecting to the MicroVM endpoint."""
    # This is a sync, blocking boto3 call (this function is itself sync, not
    # async, unlike LambdaMicroVMController's methods) -- Task 5's async
    # caller (app.py's harness_factory) must wrap this call in
    # asyncio.to_thread, the same pattern used throughout this module.
    c = client if client is not None else boto3.client("lambda-microvms", region_name=region)
    resp = c.create_microvm_auth_token(
        microvmIdentifier=vm_id,
        expirationInMinutes=minutes,
        allowedPorts=[{"port": port}],
    )
    return {"X-aws-proxy-auth": resp["authToken"]["X-aws-proxy-auth"]}
