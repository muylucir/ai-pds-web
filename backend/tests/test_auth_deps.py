# backend/tests/test_auth_deps.py
#
# 의존성 두 개의 계약: 인증이 설정되지 않았으면(로컬/테스트) 전부 통과시키고,
# 설정됐으면 Bearer 토큰을 검증하며 admin 전용 자리에서 pm을 403으로 막는다.
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.auth.deps import require_admin, require_user
from aipds.auth.models import Principal
from aipds.auth.verifier import TokenError

REGION = "ap-northeast-2"
POOL = "ap-northeast-2_TEST123"
CLIENT_ID = "client-abc"


def _probe_app() -> FastAPI:
    """의존성만 노출하는 최소 앱 — 실 라우트와 얽히지 않게 한다."""
    app = FastAPI()

    @app.get("/any")
    async def any_role(p: Principal = Depends(require_user)):
        return {"username": p.username, "role": p.role}

    @app.get("/admin-only")
    async def admin_only(p: Principal = Depends(require_admin)):
        return {"username": p.username, "role": p.role}

    return app


@pytest.fixture()
def no_auth(monkeypatch):
    """인증 미설정 = 로컬 모드."""
    monkeypatch.setattr(app_module, "cognito_config", lambda: None)


@pytest.fixture()
def with_auth(monkeypatch):
    """인증 설정 + 검증기를 가짜로 갈아끼운다.

    반환된 dict의 'principals'에 토큰→Principal 매핑을 넣으면 그대로 통과하고,
    없는 토큰은 TokenError가 된다.
    """
    state: dict = {"principals": {}}
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": REGION, "user_pool_id": POOL, "client_id": CLIENT_ID})
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())

    async def fake_verify(token, *, region, user_pool_id, client_id, jwks):
        assert region == REGION and user_pool_id == POOL and client_id == CLIENT_ID
        try:
            return state["principals"][token]
        except KeyError:
            raise TokenError("no such token")

    import aipds.auth.deps as deps_module
    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    return state


def test_bypass_lets_every_request_through_as_admin(no_auth):
    client = TestClient(_probe_app())
    # 인증 미설정 상태에서는 헤더가 아예 없어도 통과한다 — 기존 pytest 53개
    # 파일과 로컬 실행 절차가 무수정으로 유지되는 근거가 이것이다.
    body = client.get("/any").json()
    assert body == {"username": "local-dev", "role": "admin"}
    # 바이패스 principal은 admin이므로 관리 라우트도 열린다.
    assert client.get("/admin-only").status_code == 200


def test_missing_authorization_header_is_401(with_auth):
    client = TestClient(_probe_app())
    r = client.get("/any")
    assert r.status_code == 401
    # WWW-Authenticate는 401의 표준 동반 헤더다.
    assert r.headers.get("www-authenticate") == "Bearer"


def test_non_bearer_scheme_is_401(with_auth):
    client = TestClient(_probe_app())
    assert client.get("/any", headers={"Authorization": "Basic abc"}).status_code == 401


def test_invalid_token_is_401(with_auth):
    client = TestClient(_probe_app())
    r = client.get("/any", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_valid_admin_token_passes_both_dependencies(with_auth):
    with_auth["principals"]["tok-admin"] = Principal(
        username="admin@aipds.local", sub="s-1", role="admin")
    client = TestClient(_probe_app())
    headers = {"Authorization": "Bearer tok-admin"}
    assert client.get("/any", headers=headers).json()["role"] == "admin"
    assert client.get("/admin-only", headers=headers).status_code == 200


def test_pm_passes_require_user_but_is_403_on_require_admin(with_auth):
    with_auth["principals"]["tok-pm"] = Principal(
        username="pm@aipds.local", sub="s-2", role="pm")
    client = TestClient(_probe_app())
    headers = {"Authorization": "Bearer tok-pm"}
    assert client.get("/any", headers=headers).json()["role"] == "pm"
    r = client.get("/admin-only", headers=headers)
    # 인증은 됐고 권한이 없는 것 — 403이 맞다(401은 "다시 로그인하라"는 뜻).
    assert r.status_code == 403


def test_unauthenticated_request_to_admin_route_is_401_not_403(with_auth):
    # require_admin은 require_user를 통해 먼저 인증을 요구한다 — 헤더가 없는
    # 요청은 권한 부족(403)이 아니라 인증 자체가 안 된 것(401)이어야 한다.
    # Task 7이 34개 라우트 전체에서 이 순서에 의존한다.
    client = TestClient(_probe_app())
    r = client.get("/admin-only")
    assert r.status_code == 401


def test_bearer_scheme_is_case_insensitive(with_auth):
    with_auth["principals"]["tok-admin"] = Principal(
        username="admin@aipds.local", sub="s-1", role="admin")
    client = TestClient(_probe_app())
    assert client.get("/any", headers={"Authorization": "bearer tok-admin"}
                      ).status_code == 200


def test_empty_user_pool_id_counts_as_unset(monkeypatch):
    # env를 빈 문자열로 내보내는 배포 스크립트가 인증을 조용히 켜지 않게 한다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


def test_config_requires_both_pool_and_client(monkeypatch):
    # 풀만 있고 클라이언트가 없는 반쯤 설정된 상태는 "미설정"이 아니라 배포
    # 사고다 — None(바이패스)으로 취급하면 모든 요청이 조용히 가상 admin으로
    # 통과한다. 그래서 예외로 즉시 터뜨린다(fail-closed): 500이 되더라도
    # 보이는 실패가 조용한 권한 유출보다 낫다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")
    with pytest.raises(RuntimeError, match="PATHFINDER_COGNITO_USER_POOL_ID"
                       ".*PATHFINDER_COGNITO_CLIENT_ID"):
        app_module.cognito_config()


def test_config_requires_both_client_and_pool(monkeypatch):
    # 대칭 케이스: client만 있고 풀이 없어도 같은 배포 사고이므로 같이 터진다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", CLIENT_ID)
    with pytest.raises(RuntimeError, match="PATHFINDER_COGNITO_USER_POOL_ID"
                       ".*PATHFINDER_COGNITO_CLIENT_ID"):
        app_module.cognito_config()


def test_both_unset_still_bypasses(monkeypatch):
    # 둘 다 아예 설정되지 않은 것(로컬 개발의 기본 상태)은 여전히 바이패스다 —
    # 반쯤 설정된 상태와 혼동하면 안 된다.
    monkeypatch.delenv("PATHFINDER_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("PATHFINDER_COGNITO_CLIENT_ID", raising=False)
    assert app_module.cognito_config() is None


def test_both_empty_strings_still_bypasses(monkeypatch):
    # 둘 다 빈 문자열인 것도 "미설정"과 동치다(둘 다 없음 vs 둘 다 있음 —
    # 하나만 있는 경우와 구분되는 정상 바이패스 경로).
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


async def test_require_user_raises_on_half_configured_env(monkeypatch):
    # 이 테스트가 보안 속성을 실제로 고정한다: 반쯤 설정된 환경에서
    # require_user는 Principal을 반환해서는 안 된다(특히 LOCAL_PRINCIPAL을
    # 반환해서는 안 된다) — 예외가 그대로 올라와야 한다. cognito_config()가
    # 반쯤 설정된 경우에 None을 반환하도록 되돌리면 이 테스트가 실패해야 한다.
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", "")

    from starlette.requests import Request as StarletteRequest

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/any"}
    request = StarletteRequest(scope)
    with pytest.raises(RuntimeError):
        await require_user(request)


def test_config_is_read_when_both_present(monkeypatch):
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PATHFINDER_S3_REGION", REGION)
    monkeypatch.delenv("PATHFINDER_COGNITO_REGION", raising=False)
    cfg = app_module.cognito_config()
    assert cfg == {"region": REGION, "user_pool_id": POOL, "client_id": CLIENT_ID}


def test_cognito_region_env_overrides_s3_region(monkeypatch):
    monkeypatch.setenv("PATHFINDER_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("PATHFINDER_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PATHFINDER_S3_REGION", "ap-northeast-2")
    monkeypatch.setenv("PATHFINDER_COGNITO_REGION", "us-east-1")
    assert app_module.cognito_config()["region"] == "us-east-1"
