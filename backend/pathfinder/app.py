# backend/pathfinder/app.py
from __future__ import annotations
import os
import tempfile
from pathlib import Path
import httpx
from fastapi import FastAPI
from pathfinder.workspace import ProjectRegistry
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import Sandbox
from pathfinder.sandbox.harness import HarnessClient
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, MicroVMController, VMHandle
from pathfinder.sandbox.microvm_control_aws import LambdaMicroVMController

registry = ProjectRegistry()

# Monkeypatchable in tests to inject a FakeMicroVMController (no AWS).
def microvm_controller_factory(project_id: str) -> MicroVMController:
    return LambdaMicroVMController(region=os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1"))

def _boot_spec() -> BootSpec:
    return BootSpec(
        region=os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1"),
        image_id=os.environ.get("PATHFINDER_VM_IMAGE_ID") or None,     # --image-identifier
        exec_role_arn=os.environ.get("PATHFINDER_VM_ROLE_ARN") or None,  # --execution-role-arn
        # Confirmed "global.anthropic.claude-sonnet-5" (ap-northeast-1); re-verified in Task 7.
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,
    )

async def _make_microvm_sandbox(project_id: str) -> Sandbox:
    controller = microvm_controller_factory(project_id)
    shared_http = httpx.AsyncClient(timeout=None)  # streaming SSE: no read timeout
    def harness_factory(handle: VMHandle) -> HarnessClient:
        return HarnessClient(base_url=handle.base_url, http=shared_http)
    sb = MicroVMSandbox(
        project_id=project_id,
        controller=controller,
        spec=_boot_spec(),
        harness_factory=harness_factory,
    )
    await sb.start()
    return sb

async def _make_local_sandbox(project_id: str) -> Sandbox:
    root = Path(tempfile.mkdtemp(prefix=f"pf-{project_id}-"))
    sb = LocalSandbox(root=root)
    await sb.start()
    return sb

async def make_sandbox(project_id: str) -> Sandbox:
    if os.environ.get("PATHFINDER_SANDBOX") == "microvm":
        return await _make_microvm_sandbox(project_id)
    return await _make_local_sandbox(project_id)

app = FastAPI(title="Pathfinder")

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)

from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router)

from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router)
