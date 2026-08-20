# backend/pathfinder/app.py
from __future__ import annotations
import asyncio
import logging
import os
import shutil
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
from aipds.workspace import ProjectRegistry, Workspace
from aipds.runner import AgentRunner
from aipds.agent.claude_driver import ClaudeDriver
from aipds.cli_settings import cli_model_id
from aipds.s3store import S3Store, S3StoreLike
from aipds.project_store import restore_projects
from aipds.pathsafe import reject_unsafe_segment
from aipds.turn_handles import TurnHandleStore

_log = logging.getLogger(__name__)

#: 애플리케이션 로그 레벨. 기본 INFO — 진단에 필요한 것 대부분이 그 레벨이다.
_LOG_LEVEL_ENV = "PATHFINDER_LOG_LEVEL"

#: configure_logging이 자기가 붙인 핸들러를 알아보기 위한 표식. 재호출 시
#: 핸들러가 쌓여 같은 줄이 여러 번 찍히는 것을 막는다(uvicorn --reload,
#: TestClient가 lifespan을 두 번 도는 경우).
_HANDLER_TAG = "pathfinder"


def configure_logging() -> None:
    """루트 로거에 핸들러를 붙인다.

    없으면 애플리케이션 로그가 사실상 사라진다. uvicorn은 자기 로거만
    설정하고 루트는 건드리지 않으므로, 핸들러 없는 상태에서 INFO는 조용히
    버려지고 WARNING만 Python의 lastResort로 포맷 없이 새어나온다.

    실측: 워크숍 박스 journald에 `pathfinder` 로거의 산출이 2905줄 중 **0건**
    이었다. 그 사이 채팅 내역 복원 버그를 쫓고 있었는데, 원인을 가리키는 로그
    (`_resolve_resume`의 resume 판단, SDK의 "dropping mirror frame" 경고)가
    전부 이 구멍으로 사라져서 프로덕션에서 재현·계측을 반복해야 했다.

    SDK 로거를 함께 여는 이유가 그 경고다: 트랜스크립트 미러링 실패는
    `claude_agent_sdk` 쪽 로거로만 보고되므로, 우리 로거만 열면 "프레임이
    버려졌다"와 "프레임이 오지 않았다"를 여전히 구별할 수 없다.

    uvicorn의 설정을 갈아엎지 않고 루트에만 핸들러를 더한다 — 액세스 로그의
    모양은 그대로 두는 것이 목적이다.
    """
    level = getattr(logging, os.environ.get(_LOG_LEVEL_ENV, "INFO").upper(),
                    logging.INFO)
    root = logging.getLogger()
    if not any(getattr(h, "_pathfinder_tag", None) == _HANDLER_TAG
               for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(levelname)s:    %(name)s: %(message)s"))
        handler._pathfinder_tag = _HANDLER_TAG  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(min(root.level or level, level) if root.level else level)
    # 두 로거를 명시적으로 연다. 루트 레벨만으로는 부족하다 — 서드파티가 자기
    # 로거 레벨을 올려 두면 루트가 열려 있어도 걸러진다.
    for name in ("pathfinder", "claude_agent_sdk"):
        logging.getLogger(name).setLevel(level)


registry = ProjectRegistry()

# 턴 입력 핸들. 긴 채팅 텍스트를 SSE URL에서 빼기 위한 것이다 —
# turn_handles.py 헤더에 실측한 HTTP 431의 원인과 함께 적어 뒀다.
# proto_sessions와 같은 성질의 인메모리다: 수초 사는 값이고, 재시작 시
# 유실되면 그 턴만 실패한다.
turn_handles = TurnHandleStore()


# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store keeps
# the project's aiplc-docs/prototype/uploads subtree (S3 = source of truth); the
# in-process AgentRunner restores it to a local workspace at the start of a turn.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)


# Monkeypatchable in tests. Scoped to the `sessions/` prefix
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


# 모델 카탈로그용 — 버킷 루트 스토어. 카탈로그는 프로젝트보다 먼저 존재해야
# 하므로(프로젝트 생성 화면이 프로젝트 없이 읽는다) projects/ 밖에 있다.
# 테스트에서 monkeypatch.
def models_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


# 브랜드 프로필용 — 버킷 루트 스토어. design/ 아래 profile.json 하나뿐이고
# 모델 카탈로그와 같은 이유로 projects/ 밖에 있다. 테스트에서 monkeypatch.
def design_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def design_profile_store():
    """DesignProfileStore 팩토리 (monkeypatchable in tests).

    버킷이 없으면 읽기 전용(None) 스토어를 준다 -- model_catalog()와 같은
    이유: 버킷 없는 로컬 개발에서도 빌드 세션의 start()가 막히면 안 된다.
    """
    from aipds.design_profile import DesignProfileStore
    if not durable_projects_enabled():
        return DesignProfileStore(None)
    return DesignProfileStore(design_root_s3_factory())


def model_catalog():
    """ModelCatalog 팩토리 (monkeypatchable in tests).

    버킷이 없으면 읽기 전용 카탈로그(시드만)를 준다 — 로컬 개발이 아무 설정
    없이 프로젝트를 만들 수 있어야 하고, 그 화면의 콤보박스도 채워져야 한다.
    """
    from aipds.model_catalog import ModelCatalog
    if not durable_projects_enabled():
        return ModelCatalog(None)
    return ModelCatalog(models_root_s3_factory())


def project_model(project_id: str) -> str | None:
    """이 프로젝트가 도는 Bedrock 모델 id.

    폴백 순서는 프로젝트 → env → None이고 각 칸에 이유가 있다:
      - 프로젝트: 생성 시 고른 값(매니페스트에 복사돼 있다).
      - env(ANTHROPIC_MODEL): 이 기능 이전에 만든 프로젝트가 계속 도는 길.
        배포에서는 backend-permissions.ts의 MODEL이 이 값을 넣는다.
      - None: 로컬 개발에서 env도 없는 경우. 드라이버는 None을 받으면
        ANTHROPIC_MODEL을 넣지 않아 SDK 기본값으로 간다(종전 동작).
    """
    return registry.get_model_id(project_id) or os.environ.get("ANTHROPIC_MODEL")


def project_language(project_id: str) -> str:
    """이 프로젝트의 생성물 언어("ko"|"en"). 항상 값이 있다.

    project_model과 달리 env 폴백이 없다: 언어는 프로세스 전역 기본값을 가질
    이유가 없고(모델은 배포가 정하는 것이 자연스럽지만 언어는 프로젝트의
    성질이다), 레지스트리가 이미 "ko"로 확정한다.

    이 함수를 두는 이유는 호출부(driver_factory, proto_session_factory,
    survey_store_factory)가 registry를 직접 만지지 않게 하는 것이다 —
    project_model과 같은 모양을 유지한다.
    """
    return registry.get_language(project_id)


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
        from aipds.auth.verifier import JwksCache
        cfg = cognito_config() or {}
        _jwks_singleton = JwksCache(region=cfg.get("region", "ap-northeast-2"),
                                    user_pool_id=cfg.get("user_pool_id", ""))
    return _jwks_singleton


def cognito_admin():
    """CognitoAdmin 팩토리 (monkeypatchable in tests).

    싱글턴으로 두지 않는 이유: boto3 클라이언트는 스레드 세이프하지만, 테스트가
    요청마다 가짜로 갈아끼울 수 있어야 하고 생성 비용은 무시할 만하다.
    """
    from aipds.auth.cognito import CognitoAdmin
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


async def purge_local_workspace(project_id: str) -> None:
    """이 프로젝트의 로컬 워크스페이스 디렉터리를 지운다. 멱등.

    **왜 `runner.stop()`으로 부족한가.** 그 안에도 rmtree가 있지만 두 조건에
    걸린다. 첫째, 삭제 라우트가 `registry.has_workspace(pid)`일 때만 stop을
    부르는데 그 플래그는 `attach()`로만 채워지고, 기동 시 복원은
    `register()`만 한다(위 lifespan: "프로젝트 '목록'만 복원") — 즉 **재시작 뒤
    한 번도 열지 않은 프로젝트는 전부 False**이고, 그것이 워크숍마다 재배포가
    있는 이 제품의 흔한 상태다. 둘째, stop의 실패는 의도적으로 삼켜지므로
    드라이버 종료가 실패하면 rmtree까지 함께 건너뛴다.

    실측(2026-08-19, 배포 인스턴스): `/opt/pathfinder/workspaces/`에 S3에 없는
    프로젝트 6개의 디렉터리가 남아 있었다. 사용자에게는 "채팅 기록·문서가 영구
    삭제된다"고 약속한 상태다(`project.deleteConfirmBody`).

    **잔여물은 raise다.** `ignore_errors=True`는 첫 실패에서 멈추지 않고 갈 수
    있는 만큼 가게 하는 용도이므로 성공 신호로 쓸 수 없다 — node_modules 깊은
    곳의 권한 오류가 성공으로 보고되면 문서가 남은 채 "삭제됐다"가 된다.
    `ProtoHost.purge`가 같은 이유로 같은 모양을 갖는다.

    **`reject_unsafe_segment`가 선행한다.** URL 파라미터 하나가 디렉터리 이름이
    되는 자리이고, `pathlib`은 정규화하지 않으므로 `".."`는 정말로 부모다 — 검증
    없이는 한 프로젝트 삭제가 `workspaces/` 전체의 rmtree가 된다. 라우트도 막지만
    (그쪽이 1차 방어) 위험한 원시 연산이 누가 부르든 무기가 되기를 거부한다.
    """
    reject_unsafe_segment(project_id)
    target = _workspaces_dir() / project_id
    if not target.is_dir():
        return
    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    if target.exists():
        raise RuntimeError(f"workspace purge left residue: {target}")


def _discovery_config_dir() -> Path:
    return Path(os.environ.get("PATHFINDER_DISCOVERY_CONFIG_DIR",
                               "~/pathfinder-discovery-config")).expanduser()


# Discovery 드라이버. Claude Agent SDK 한 벌뿐이다 — AI-PLC 룰이 전제한 실행
# 환경이고, 여기 있던 `strands` 폴백은 삭제했다.
#
# **왜 폴백을 없앴는가.** 워크숍 중 env 하나로 되돌리는 탈출로로 뒀는데, 실제로는
# 당길 수 없는 상태로 썩어 있었다: StrandsDriver는 `language`를 받지 않아 영어
# 프로젝트가 한국어로 돌고(7f33652가 고친 그 결함), session_store·pending_store·
# answer_store가 없어 트랜스크립트 미러링과 질문·답변 복원이 전부 빠진다. 그
# 사실은 실제로 당기는 순간 — 즉 워크숍 중 사고가 났을 때 — 처음 드러난다.
# 작동하는 롤백은 git revert + `pathfinder-update`다(배포가 브랜치를 가리키므로
# 인스턴스 교체 없이 되돌아간다).
#
# Monkeypatchable in tests: 이 함수 자체를 fake agent_factory로 갈아끼운다.
def driver_factory(project_id: str, local_root: Path):
    return ClaudeDriver(
        workspace=str(local_root),
        rules_dir=_rules_dir(),
        config_dir=str(_discovery_config_dir()),
        s3=s3_store_factory(project_id),
        # cli_model_id를 여기서 씌운다(project_model 안이 아니다) — `[1m]`은
        # CLI 별칭이고 Bedrock 모델 id가 아니라서, project_model을 그대로 쓰는
        # 설문 생성 경로(BedrockModel)에 흘러가면 ValidationException이 된다.
        anthropic_model=cli_model_id(project_model(project_id)),
        language=project_language(project_id),
    )


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


def _proto_permission_mode() -> str:
    """빌드는 무인으로 돌아간다 — 승인해 줄 사람이 없으므로 bypassPermissions가
    기본값이다. 더 조이려면 환경변수로 덮어쓴다(잘못된 값은 즉시 ValueError)."""
    from aipds.proto.builder import DEFAULT_PERMISSION_MODE
    return os.environ.get("PATHFINDER_PROTO_PERMISSION_MODE",
                          DEFAULT_PERMISSION_MODE)


# 전역 동시 빌드 상한 (monkeypatchable in tests).
from aipds.proto.limits import BuildSemaphore  # noqa: E402

build_semaphore = BuildSemaphore(
    max_concurrent=int(os.environ.get("PATHFINDER_PROTO_MAX_CONCURRENT", "10")))


def proto_host():
    """ProtoHost 싱글턴 (monkeypatchable in tests)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from aipds.proto.host import ProtoHost
        _proto_host_singleton = ProtoHost(root=_proto_root())
    return _proto_host_singleton


def proto_session_factory(project_id: str, slug: str):
    """PrototypeSession 조립 (monkeypatchable in tests). VM은 없다 — 빌더가
    백엔드 프로세스 안에서 claude 서브프로세스를 띄운다."""
    from aipds.proto.builder import PrototypeBuilder
    from aipds.proto.session import PrototypeSession
    from aipds.proto.session_store import S3SessionStore

    s3 = s3_store_factory(project_id)
    build_root = _proto_root()
    config_dir = _proto_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    store = S3SessionStore(s3, slug=slug) if os.environ.get("PATHFINDER_S3_BUCKET") else None
    # 한 번 읽어 빌더와 세션에 같은 값을 준다 — 둘이 어긋나면 프롬프트와 도구
    # 설명의 언어가 갈린다.
    language = project_language(project_id)

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            config_dir=str(config_dir),
            session_id=session_id,
            resume=resume,
            session_store=store,
            # driver_factory와 같은 이유로 CLI용 조립을 여기서 한다.
            anthropic_model=cli_model_id(project_model(project_id)),
            language=language,
            permission_mode=_proto_permission_mode(),
        )

    return PrototypeSession(
        project_id=project_id, slug=slug, s3=s3,
        build_root=build_root,
        builder_factory=builder_factory,
        semaphore=build_semaphore,
        language=language,
        design_profiles=design_profile_store(),
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
    from aipds.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id,
                       language=project_language(project_id))


def questionnaire_agent_factory(project_id: str):
    """A one-shot `async (prompt) -> str` callable. Deliberately NOT
    Discovery 드라이버: 그쪽은 AI-PLC 룰 프롬프트·워크스페이스 도구·세션
    관리를 함께 싣는데, 무상태 생성 호출에는 그중 아무것도 필요 없다.

    project_id를 받는 이유: 문항 생성도 그 프로젝트의 모델로 돌아야 한다.
    종전에는 os.environ["ANTHROPIC_MODEL"]을 직접 읽어, 프로젝트별 모델을
    골라도 이 경로만 전역 env를 썼다.
    """
    model_id = project_model(project_id)

    async def call(prompt: str) -> str:
        if not model_id:
            # 여기가 유일하게 모델을 필수로 요구하는 지점이다(다른 둘은 None을
            # SDK 기본값으로 넘긴다). 라우트가 502로 감싸고 이 문장이 로그에
            # 남아 원인이 프로젝트 설정임을 말해 준다.
            raise RuntimeError(
                f"no model for project {project_id!r}: neither the project's "
                "model_id nor ANTHROPIC_MODEL is set")
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=model_id, max_tokens=8000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call


def design_token_extractor():
    """산문뿐인 DESIGN.md에서 토큰을 뽑을 단발 호출자. 모델이 없으면 None.

    `questionnaire_agent_factory`와 같은 모양(Strands + BedrockModel 단발)이지만
    두 곳이 다른 점이 둘 있다.

    1. `project_model()`을 쓸 수 없다 — 브랜드 프로필은 프로젝트 밖의 전역 한
       장이고(design_profile.py), 업로드 시점에는 프로젝트가 없다. 그래서 배포가
       내보내는 기본 모델(`ANTHROPIC_MODEL`, infra/lib/backend-permissions.ts의
       MODEL)을 읽는다.
    2. 모델이 없을 때 RuntimeError를 올리지 않고 None을 돌려준다. 그쪽은 문항
       생성이 곧 그 요청의 목적이라 실패해야 하지만, 여기서 실패하면 추출이
       **업로드 자체를** 막는다 — 토큰 없이 산문만 적용하는 것도 유효한
       상태다(라우트가 경고로 번역한다).

    `temperature`는 넘기지 않는다 — Opus 4.7+·Sonnet 5는 샘플링 파라미터를 400으로
    거부한다. max_tokens가 작은 이유는 출력이 ```tokens 블록 한 개(최대 14줄)라서다.
    """
    model_id = os.environ.get("ANTHROPIC_MODEL")
    if not model_id:
        return None

    async def call(prompt: str) -> str:
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=model_id, max_tokens=2000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        return str(await agent.invoke_async(prompt))
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
    # 가장 먼저. 아래의 모든 것이 실패를 로그로 보고하고, 핸들러가 붙기 전의
    # 로그는 사라진다(configure_logging 참고 — 실제로 그렇게 잃었다).
    configure_logging()
    # 기동 시 S3 매니페스트에서 프로젝트 '목록'만 복원한다. 워크스페이스는 첫
    # 요청에서 lazy 초기화(deps.ensure_workspace) — 기동을 빠르게 유지한다.
    # 복원 실패는 기동을 막지 않는다.
    if durable_projects_enabled():
        try:
            for pid, name, created_at, model_id, language in await restore_projects(
                    projects_root_s3_factory()):
                registry.register(pid, name, created_at=created_at,
                                  model_id=model_id, language=language)
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
    # 프로토타입 접근 토큰을 디스크에서 다시 읽는다. 위 스윕과 짝이지만 방향이
    # 반대다: 스윕은 재시작으로 의미를 잃은 것(고아 프로세스)을 버리고, 이쪽은
    # 재시작을 넘어 살아야 하는 것(이미 배포된 링크)을 되살린다. 이것이 없으면
    # 워크숍 중 백엔드가 재시작될 때 참가자에게 나눠 준 URL이 전부 404가 되고,
    # 다시 호스팅해도 복구되지 않는다 — 그 URL 안의 토큰은 바뀌지 않으므로.
    try:
        loaded = proto_host().load_tokens()
        if loaded:
            _log.info("loaded %d prototype access token(s)", loaded)
    except Exception:
        _log.exception("prototype token load failed; continuing startup")
    yield


def _docs_openapi_url() -> str | None:
    """스키마/문서 UI(/openapi.json, /docs, /redoc)의 활성화 여부.

    이 라우트들은 FastAPI가 자체 등록한다 — app.include_router(...,
    dependencies=_AUTH)를 거치지 않으므로 아래 인증 배선과 무관하게 익명
    200을 반환한다. 인증이 설정된 배포에서는 그게 이 앱의 전체 라우트
    표·파라미터·스키마를 익명 방문자에게 넘기는 것과 같으므로 끈다
    (openapi_url=None이면 /docs·/redoc도 함께 꺼진다 — 둘 다 openapi_url을
    전제로 등록되기 때문). 로컬 개발(인증 미설정)에서는 유용하니 켜 둔다.

    별도 함수로 뽑은 이유: FastAPI(...)의 openapi_url 인자는 임포트 시점에
    딱 한 번 평가되므로, 이 로직 자체를 테스트에서 monkeypatch(cognito_config)
    만으로 검증하려면 app 생성과 분리된 순수 함수여야 한다 — module 전체를
    importlib.reload()하면 registry 등 다른 모듈 전역 싱글턴이 새로 만들어져
    이미 그 객체를 참조 중인 다른 테스트 파일들이 깨진다(실측: 대량 KeyError).
    """
    return None if cognito_config() else "/openapi.json"


# cognito_config()는 매 요청 호출과 같은 순수 env 읽기이므로 임포트 시점
# 호출도 안전하다(반쯤 설정된 상태면 여기서 바로 RuntimeError로 죄는 게
# 오히려 첫 요청까지 기다리는 것보다 낫다).
app = FastAPI(title="Pathfinder", lifespan=_lifespan,
             openapi_url=_docs_openapi_url())

# CORS: the frontend (:3000 in dev, Playwright e2e) calls this API (:8000)
# from a real browser and needs the preflight/simple-request headers.
# allow_credentials=True: frontend/lib/auth.ts's CREDENTIALS constant sends
# `credentials: "include"` on every client call (the same-origin /api proxy
# needs the browser to send its httpOnly session cookie so it can translate
# that into Authorization: Bearer -- see app/api/[...path]/route.ts). Without
# this the browser silently drops every cross-origin response in the
# README's documented default setup (:3000 -> :8000). This is safe only
# because allow_origins below is an explicit allowlist, never "*" --
# Starlette's CORSMiddleware refuses to combine allow_credentials with a
# wildcard origin's shortcut path anyway (it falls back to echoing the
# specific Origin), but keep the allowlist explicit regardless.
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
    allow_credentials=True,
)

# ---- 라우터 등록 ----
#
# 인증은 라우트 본문이 아니라 여기서 붙인다: 라우터 단위 dependencies로 걸면
# 라우트 함수를 하나도 건드리지 않고 전부 보호된다. 인증이 설정되지 않은
# 로컬/테스트에서는 require_user가 전부 통과시킨다(auth/deps.py).
from aipds.auth.deps import require_user  # noqa: E402
from fastapi import Depends  # noqa: E402

_AUTH = [Depends(require_user)]

from aipds.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router, dependencies=_AUTH)
app.include_router(artifacts.router, dependencies=_AUTH)

from aipds.routes import answers  # noqa: E402
app.include_router(answers.router, dependencies=_AUTH)

from aipds.routes import approval  # noqa: E402
app.include_router(approval.router, dependencies=_AUTH)

from aipds.routes import turns  # noqa: E402
app.include_router(turns.router, dependencies=_AUTH)

from aipds.routes import discovery  # noqa: E402
app.include_router(discovery.router, dependencies=_AUTH)

from aipds.routes import history  # noqa: E402
app.include_router(history.router, dependencies=_AUTH)

from aipds.routes import uploads  # noqa: E402
app.include_router(uploads.router, dependencies=_AUTH)

from aipds.routes import prototypes  # noqa: E402
app.include_router(prototypes.router, dependencies=_AUTH)

from aipds.routes import surveys  # noqa: E402
app.include_router(surveys.router, dependencies=_AUTH)

from aipds.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)

from aipds.routes import models as models_routes  # noqa: E402
app.include_router(models_routes.router, dependencies=_AUTH)
app.include_router(models_routes.admin_router, dependencies=_AUTH)

from aipds.routes import design as design_routes  # noqa: E402
app.include_router(design_routes.admin_router, dependencies=_AUTH)

# ---- 공개(무인증) 라우터 — 정확히 둘 (라우터 2개, 경로는 3개 — 아래 참고) ----
#
# 여기에 라우터를 추가하는 것은 인터넷에 공개하는 것과 같다. 두 경로 모두 계정이
# 없는 최종 사용자를 위한 것이다: 설문 링크를 받아 응답하고(surveys_public),
# 평가 대상 프로토타입을 실제로 써본다(proto_public).
# tests/test_auth_route_coverage.py가 이 목록을 강제한다.
from aipds.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)

from aipds.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
