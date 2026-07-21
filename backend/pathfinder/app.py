# backend/pathfinder/app.py
from __future__ import annotations
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import boto3
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
    default = str(Path(__file__).resolve().parent.parent.parent / "files" / "aiplc-rules")
    return os.environ.get("PATHFINDER_RULES_DIR", default)


def _workspaces_dir() -> Path:
    root = os.environ.get("PATHFINDER_WORKSPACES_DIR")
    return Path(root) if root else Path(tempfile.gettempdir()) / "pathfinder-workspaces"


# Monkeypatchable in tests: StrandsDriver를 fake agent_factory로 갈아끼운다.
def driver_factory(project_id: str, local_root: Path) -> StrandsDriver:
    return StrandsDriver(workspace=str(local_root), rules_dir=_rules_dir())


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
            for pid, name in await restore_projects(projects_root_s3_factory()):
                registry.register(pid, name)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
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
