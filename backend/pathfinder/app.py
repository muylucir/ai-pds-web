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


# ---- 인증 (routes/*, auth/deps.py) ----

_jwks_singleton = None


def cognito_config() -> dict | None:
    """Cognito 설정. 둘 다 미설정이면 None = 인증 바이패스.

    durable_projects_enabled()와 같은 규율이다: 필수 env가 전혀 없으면 그 기능
    전체를 생략하고 로컬/테스트가 아무 설정 없이 돌게 한다.

    하지만 풀 id와 client id 중 **하나만** 있는 상태는 "미설정"이 아니라 배포
    사고다. 예전에는 이 경우도 None(바이패스)으로 취급했는데, 그러면 인증이
    꺼진 채 모든 요청이 조용히 가상 admin(LOCAL_PRINCIPAL)으로 통과한다 —
    크래시도, 경고도, 흔적도 없다. 그래서 반쯤 설정된 상태는 예외로 즉시
    터뜨린다(fail-closed): 이 요청들은 500이 되지만, 아무도 모르게 관리자
    권한이 새는 것보다는 눈에 보이는 실패가 낫다. cognito_config()는 매 요청
    호출되므로(require_user), 배포 스크립트가 두 변수 중 하나를 지우는 순간
    재시작 없이도 즉시 이 예외가 뜬다.
    """
    pool = os.environ.get("PATHFINDER_COGNITO_USER_POOL_ID", "").strip()
    client = os.environ.get("PATHFINDER_COGNITO_CLIENT_ID", "").strip()
    if not pool and not client:
        return None
    if not pool or not client:
        raise RuntimeError(
            "PATHFINDER_COGNITO_USER_POOL_ID and PATHFINDER_COGNITO_CLIENT_ID "
            "must both be set or both be unset — exactly one is set, which "
            "would otherwise silently bypass authentication as admin for "
            "every request")
    region = (os.environ.get("PATHFINDER_COGNITO_REGION", "").strip()
              or os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"))
    return {"region": region, "user_pool_id": pool, "client_id": client}


def jwks_cache():
    """JWKS 캐시 싱글턴 (monkeypatchable in tests)."""
    global _jwks_singleton
    if _jwks_singleton is None:
        from pathfinder.auth.verifier import JwksCache
        cfg = cognito_config() or {}
        _jwks_singleton = JwksCache(region=cfg.get("region", "ap-northeast-2"),
                                    user_pool_id=cfg.get("user_pool_id", ""))
    return _jwks_singleton


def cognito_admin():
    """CognitoAdmin 팩토리 (monkeypatchable in tests).

    싱글턴으로 두지 않는 이유: boto3 클라이언트는 스레드 세이프하지만, 테스트가
    요청마다 가짜로 갈아끼울 수 있어야 하고 생성 비용은 무시할 만하다.
    """
    from pathfinder.auth.cognito import CognitoAdmin
    cfg = cognito_config()
    if cfg is None:
        raise RuntimeError(
            "user management requires PATHFINDER_COGNITO_USER_POOL_ID / "
            "PATHFINDER_COGNITO_CLIENT_ID")
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    return CognitoAdmin(client, cfg["user_pool_id"])


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
# 백엔드 재시작 시 소멸(빌드 디렉토리와 transcript는 남아 resume으로 이어진다).
proto_sessions: dict = {}

_proto_host_singleton = None


def _proto_root() -> Path:
    return Path(os.environ.get("PATHFINDER_PROTO_ROOT",
                               "~/pathfinder-protos")).expanduser()


def _proto_config_dir() -> Path:
    """빌드 에이전트 전용 CLAUDE_CONFIG_DIR. 지정하지 않으면 번들 바이너리가
    백엔드 유저의 ~/.claude(개인 skills/agents/CLAUDE.md)를 읽는다."""
    return Path(os.environ.get("PATHFINDER_PROTO_CONFIG_DIR",
                               "~/pathfinder-proto-config")).expanduser()


# 전역 동시 빌드 상한 (monkeypatchable in tests).
from pathfinder.proto.limits import BuildSemaphore  # noqa: E402

build_semaphore = BuildSemaphore(
    max_concurrent=int(os.environ.get("PATHFINDER_PROTO_MAX_CONCURRENT", "2")))


def proto_host():
    """ProtoHost 싱글턴 (monkeypatchable in tests)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from pathfinder.proto.host import ProtoHost
        _proto_host_singleton = ProtoHost(root=_proto_root())
    return _proto_host_singleton


def proto_session_factory(project_id: str, slug: str):
    """PrototypeSession 조립 (monkeypatchable in tests). VM은 없다 — 빌더가
    백엔드 프로세스 안에서 claude 서브프로세스를 띄운다."""
    from pathfinder.proto.builder import PrototypeBuilder
    from pathfinder.proto.session import PrototypeSession
    from pathfinder.proto.session_store import S3SessionStore

    s3 = s3_store_factory(project_id)
    build_root = _proto_root()
    config_dir = _proto_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    store = S3SessionStore(s3, slug=slug) if os.environ.get("PATHFINDER_S3_BUCKET") else None

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            config_dir=str(config_dir),
            session_id=session_id,
            resume=resume,
            session_store=store,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL"),
        )

    return PrototypeSession(
        project_id=project_id, slug=slug, s3=s3,
        build_root=build_root,
        builder_factory=builder_factory,
        semaphore=build_semaphore,
    )


# ---- validation survey wiring (routes/surveys.py) ----


def surveys_root_s3_factory() -> S3StoreLike:
    """Bucket-root store: the token index must be readable before we know
    which project a token belongs to."""
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def survey_store_factory(project_id: str, slug: str):
    from pathfinder.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id)


def questionnaire_agent_factory():
    """A one-shot `async (prompt) -> str` callable. Deliberately NOT
    StrandsDriver: that bakes in the AIPLC rules prompt, workspace tools and a
    session manager, none of which belong in a stateless generation call."""
    async def call(prompt: str) -> str:
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=os.environ["ANTHROPIC_MODEL"],
                             max_tokens=8000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call


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
    # 재시작으로 소멸한 인메모리 세션이 남긴 고아 호스팅 프로세스 정리
    # (구 고아 VM 스윕의 대체물 — 이제 그 자식들은 우리 프로세스의 자식이다).
    try:
        swept = proto_host().sweep_orphans()
        if swept:
            _log.info("swept %d orphan prototype hosting process(es)", swept)
    except Exception:
        _log.exception("orphan hosting sweep failed; continuing startup")
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

# ---- 라우터 등록 ----
#
# 인증은 라우트 본문이 아니라 여기서 붙인다: 라우터 단위 dependencies로 걸면
# 라우트 함수를 하나도 건드리지 않고 전부 보호된다. 인증이 설정되지 않은
# 로컬/테스트에서는 require_user가 전부 통과시킨다(auth/deps.py).
from pathfinder.auth.deps import require_user  # noqa: E402
from fastapi import Depends  # noqa: E402

_AUTH = [Depends(require_user)]

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router, dependencies=_AUTH)
app.include_router(artifacts.router, dependencies=_AUTH)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router, dependencies=_AUTH)

from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router, dependencies=_AUTH)

from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router, dependencies=_AUTH)

from pathfinder.routes import history  # noqa: E402
app.include_router(history.router, dependencies=_AUTH)

from pathfinder.routes import uploads  # noqa: E402
app.include_router(uploads.router, dependencies=_AUTH)

from pathfinder.routes import prototypes  # noqa: E402
app.include_router(prototypes.router, dependencies=_AUTH)

from pathfinder.routes import surveys  # noqa: E402
app.include_router(surveys.router, dependencies=_AUTH)

from pathfinder.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)

# ---- 공개(무인증) 라우터 — 정확히 둘 (라우터 2개, 경로는 3개 — 아래 참고) ----
#
# 여기에 라우터를 추가하는 것은 인터넷에 공개하는 것과 같다. 두 경로 모두 계정이
# 없는 최종 사용자를 위한 것이다: 설문 링크를 받아 응답하고(surveys_public),
# 평가 대상 프로토타입을 실제로 써본다(proto_public).
# tests/test_auth_route_coverage.py가 이 목록을 강제한다.
from pathfinder.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)

from pathfinder.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
