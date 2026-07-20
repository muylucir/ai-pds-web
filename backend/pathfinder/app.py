# backend/pathfinder/app.py
from __future__ import annotations
import os
import tempfile
from pathlib import Path
import boto3
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI

# backend/.env (gitignored, optional) feeds the PATHFINDER_*/ANTHROPIC_MODEL
# settings read via os.environ below. Real environment variables win over the
# file (override=False) so shell exports / container env keep working as
# before, and a missing file is a silent no-op (local mode needs no config).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from pathfinder.workspace import ProjectRegistry
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import Sandbox
from pathfinder.sandbox.harness import HarnessClient
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, MicroVMController, VMHandle
from pathfinder.sandbox.microvm_control_aws import LambdaMicroVMController, mint_harness_token
from pathfinder.sandbox.s3store import S3Store, S3StoreLike

registry = ProjectRegistry()

# Monkeypatchable in tests to inject a FakeMicroVMController (no AWS).
def microvm_controller_factory(project_id: str) -> MicroVMController:
    return LambdaMicroVMController(region=os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1"))

# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store is
# Seoul (ap-northeast-2) while MicroVMs run in Tokyo (ap-northeast-1), because
# Lambda MicroVMs is not available in Seoul. This cross-border processing
# must be disclosed to the customer at workshop start.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)

# Monkeypatchable in tests. Reads the strands session objects (sessions/ prefix)
# that S3SessionManager writes from inside the VM; the backend only READS them.
def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)

# Monkeypatchable in tests so unit tests never call AWS. Returns the auth header
# dict for a HarnessClient, or None to attach no auth (local/fake controllers).
def _harness_token_provider(vm_id: str, region: str) -> dict[str, str] | None:
    if vm_id.startswith("fake-"):   # FakeMicroVMController handles: never mint.
        return None
    # NOTE: mint_harness_token is a sync, blocking boto3 call (its own
    # docstring says so) made directly on the asyncio event loop here --
    # harness_factory (this function's only caller) is sync by design (see
    # microvm.py's HarnessLike-returning Callable[[VMHandle], HarnessLike]),
    # so there is no `await`/asyncio.to_thread wrapping at this call site.
    # Accepted as a documented limitation for now under the workshop's
    # few-concurrent-tenants model (same class of deferral as the
    # SUSPENDING->"stopped" drill item in microvm_control_aws.py's
    # _map_status docstring) -- one blocking CreateMicrovmAuthToken call per
    # handle transition (boot/resume/reboot), not per request, so it is rare
    # and short-lived, but it does stall the event loop for its duration. A
    # real fix needs an async `harness_factory`, which ripples into
    # `_ensure_ready`/`_boot_and_restore` (microvm.py) and every test's inline
    # `harness_factory=lambda handle: harness` -- out of scope here; see the
    # plan doc's Open Questions (Task 5 area) for the resolution path.
    return mint_harness_token(vm_id, region)


def _build_harness_for_test(
    handle: VMHandle, shared_http: httpx.AsyncClient, region: str,
    session: dict | None = None,
) -> HarnessClient:
    """Extracted so the header-minting wiring is unit-testable without booting."""
    return HarnessClient(
        base_url=handle.base_url,
        http=shared_http,
        headers=_harness_token_provider(handle.vm_id, region),
        session=session,
    )


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
    s3 = s3_store_factory(project_id)
    region = os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1")
    # streaming SSE: no read timeout, but CONNECT must still time out -- a
    # dead VM endpoint (expired/terminated, DNS/network gone) must not hang
    # the request forever.
    shared_http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
    # Session descriptor the harness needs to resume conversation state across
    # /message, /answers, and /pending (Task 4's HTTP contract). The durable
    # store's bucket/region (Seoul), not the VM's own region (Tokyo).
    session = {
        "session_id": project_id,
        "bucket": os.environ.get("PATHFINDER_S3_BUCKET", ""),
        "region": os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"),
        "prefix": "sessions",
    }
    def harness_factory(handle: VMHandle) -> HarnessClient:
        # mint-on-resume (Part-2 Task 5): a fresh CreateMicrovmAuthToken JWE is
        # minted on every boot/resume/reboot and attached per HarnessClient.
        # The shared AsyncClient is reused (headers live on the HarnessClient,
        # not the client), so on_stop=shared_http.aclose stays correct.
        return _build_harness_for_test(handle, shared_http, region, session=session)
    sb = MicroVMSandbox(
        project_id=project_id,
        controller=controller,
        spec=_boot_spec(),
        harness_factory=harness_factory,
        s3=s3,
        on_stop=shared_http.aclose,  # close the shared client this closure owns
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

# CORS: the frontend (:3000 in dev, Playwright e2e) calls this API (:8000)
# from a real browser and needs the preflight/simple-request headers.
# allow_credentials is intentionally NOT enabled -- no cookies are used, the
# auth token goes in a header, so we don't need the credentialed-CORS dance.
_cors_origins = [
    o.strip()
    for o in os.environ.get("PATHFINDER_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)

from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router)

from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router)

from pathfinder.routes import history  # noqa: E402
app.include_router(history.router)
