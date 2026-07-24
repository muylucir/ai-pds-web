# backend/pathfinder/app.py
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
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
from pathfinder.workspace import ProjectRegistry, Workspace
from pathfinder.runner import AgentRunner
from pathfinder.agent.driver import StrandsDriver
from pathfinder.s3store import S3Store, S3StoreLike
from pathfinder.project_store import restore_projects

_log = logging.getLogger(__name__)

registry = ProjectRegistry()


# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store keeps
# the project's aiplc-docs/prototype/uploads subtree (S3 = source of truth); the
# in-process AgentRunner restores it to a local workspace at the start of a turn.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)


# Monkeypatchable in tests. Reads the strands session objects (sessions/ prefix)
# that S3SessionManager writes; the backend only READS them for history.
def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)


# 매니페스트/삭제용 — projects/ 전체를 보는 root 스토어. 테스트에서 monkeypatch.
def projects_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="projects/", client=client)


def durable_projects_enabled() -> bool:
    """버킷 미설정(로컬/테스트)이면 목록 영속화 전체를 생략한다."""
    return bool(os.environ.get("PATHFINDER_S3_BUCKET"))


def _rules_dir() -> str:
    default = str(Path(__file__).resolve().parent.parent.parent / "rule" / "aiplc-rules")
    return os.environ.get("PATHFINDER_RULES_DIR", default)


def _workspaces_dir() -> Path:
    root = os.environ.get("PATHFINDER_WORKSPACES_DIR")
    return Path(root) if root else Path(tempfile.gettempdir()) / "pathfinder-workspaces"


# Monkeypatchable in tests: StrandsDriver를 fake agent_factory로 갈아끼운다.
def driver_factory(project_id: str, local_root: Path) -> StrandsDriver:
    return StrandsDriver(workspace=str(local_root), rules_dir=_rules_dir())


# ---- prototype build/hosting wiring (routes/prototypes.py) ----

# 살아있는 빌드 세션 레지스트리 — (pid, slug) → PrototypeSession. 인메모리:
# 백엔드 재시작 시 소멸(스펙 §6 — 기동 시 고아 VM 정리로 뒷정리).
proto_sessions: dict = {}

_proto_host_singleton = None


def proto_host():
    """ProtoHost 싱글턴 (monkeypatchable in tests). 루트는
    PATHFINDER_PROTO_ROOT(기본 ~/pathfinder-protos)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from pathfinder.proto.host import ProtoHost
        root = Path(os.environ.get("PATHFINDER_PROTO_ROOT",
                                   "~/pathfinder-protos")).expanduser()
        _proto_host_singleton = ProtoHost(
            s3=_proto_bundle_s3_factory, root=root)
    return _proto_host_singleton


def _proto_bundle_s3_factory(project_id: str):
    # ProtoHost가 프로젝트별 번들을 읽을 때 쓰는 프로젝트-프리픽스 스토어.
    return s3_store_factory(project_id)


# 공유 httpx 클라이언트 — 하네스 SSE는 read 타임아웃 없음(무기한 스트림),
# connect만 5초 (과거 microvm app.py와 동일한 셰이프).
_proto_http: httpx.AsyncClient | None = None


def _proto_http_client() -> httpx.AsyncClient:
    global _proto_http
    if _proto_http is None:
        _proto_http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
    return _proto_http


def proto_session_factory(project_id: str, slug: str):
    """PrototypeSession 조립 (monkeypatchable in tests). VM은 Tokyo 고정
    기본값; 이미지/롤은 VmStack 배포 산출물을 env로 주입받는다."""
    from pathfinder.proto.harness_client import HarnessClient
    from pathfinder.proto.session import PrototypeSession
    from pathfinder.proto.vm import BootSpec, LambdaMicroVMController, mint_harness_token

    vm_region = os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1")
    image_id = os.environ.get("PATHFINDER_VM_IMAGE_ID")
    role_arn = os.environ.get("PATHFINDER_VM_ROLE_ARN")
    spec = BootSpec(region=vm_region, image_id=image_id, exec_role_arn=role_arn,
                    anthropic_model=os.environ.get("ANTHROPIC_MODEL"))

    # fake-* 이미지(테스트/로컬)는 토큰 민팅 불가/불필요 — 과거
    # _harness_token_provider의 fake 분기와 동일한 규칙을 팩토리에서 결정.
    minter = None
    if image_id and not image_id.startswith("fake-"):
        minter = lambda vm_id: mint_harness_token(vm_id, vm_region)  # noqa: E731

    def harness_factory(base_url: str, headers: dict):
        return HarnessClient(base_url, _proto_http_client(), headers=headers or None)

    return PrototypeSession(
        project_id=project_id, slug=slug,
        s3=s3_store_factory(project_id),
        controller=LambdaMicroVMController(region=vm_region),
        spec=spec, harness_factory=harness_factory,
        rules_dir=Path(_rules_dir()),
        token_minter=minter,
    )


async def _cleanup_orphan_vms() -> None:
    """기동 시 고아 VM 정리 — best effort, 실패해도 기동은 계속(로그만).
    VM 태깅 API가 없으므로 imageArn == PATHFINDER_VM_IMAGE_ID 필터로 우리
    이미지의 RUNNING VM만 terminate한다."""
    image_id = os.environ.get("PATHFINDER_VM_IMAGE_ID")
    if not image_id or image_id.startswith("fake-"):
        return
    region = os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1")
    try:
        def _sweep() -> int:
            client = boto3.client("lambda-microvms", region_name=region)
            stopped = 0
            paginator_resp = client.list_microvms()
            for vm in paginator_resp.get("microvms", []):
                if vm.get("imageArn") == image_id and vm.get("state") == "RUNNING":
                    client.terminate_microvm(microvmIdentifier=vm["microvmId"])
                    stopped += 1
            return stopped
        stopped = await asyncio.to_thread(_sweep)
        if stopped:
            _log.info("terminated %d orphan prototype VM(s)", stopped)
    except Exception:
        _log.exception("orphan VM cleanup failed; continuing startup")


async def make_workspace(project_id: str) -> Workspace:
    s3 = s3_store_factory(project_id)
    local_root = _workspaces_dir() / project_id
    # Session descriptor the in-process driver's S3SessionManager needs to
    # resume conversation state across /message, /answers, and /pending. The
    # durable store's bucket/region, keyed by project_id.
    session = {
        "session_id": project_id,
        "bucket": os.environ.get("PATHFINDER_S3_BUCKET", ""),
        "region": os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"),
        "prefix": "sessions",
    }
    driver = driver_factory(project_id, local_root)
    runner = AgentRunner(project_id=project_id, driver=driver, s3=s3,
                         local_root=local_root, session=session)
    return Workspace(runner)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 기동 시 S3 매니페스트에서 프로젝트 '목록'만 복원한다. 워크스페이스는 첫
    # 요청에서 lazy 초기화(deps.ensure_workspace) — 기동을 빠르게 유지한다.
    # 복원 실패는 기동을 막지 않는다.
    if durable_projects_enabled():
        try:
            for pid, name, created_at in await restore_projects(projects_root_s3_factory()):
                registry.register(pid, name, created_at=created_at)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
    # 재시작으로 소멸한 인메모리 세션이 남긴 고아 VM 정리 (best effort).
    await _cleanup_orphan_vms()
    yield


app = FastAPI(title="Pathfinder", lifespan=_lifespan)

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

from pathfinder.routes import uploads  # noqa: E402
app.include_router(uploads.router)

from pathfinder.routes import prototypes  # noqa: E402
app.include_router(prototypes.router)
