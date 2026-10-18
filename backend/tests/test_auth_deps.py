# backend/tests/test_auth_deps.py
#
# Contract of the two dependencies: if auth is not configured (local/test), let everything
# through; if configured, verify the Bearer token and block pm with 403 on admin-only spots.
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
    """Minimal app exposing only the dependencies, so it stays decoupled from the real routes."""
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
    """Auth not configured = local mode."""
    monkeypatch.setattr(app_module, "cognito_config", lambda: None)


@pytest.fixture()
def with_auth(monkeypatch):
    """Configure auth + swap the verifier for a fake.

    Put a token -> Principal mapping into 'principals' of the returned dict and it passes
    as is; a token not in it becomes a TokenError.
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
    # With auth not configured, requests pass even with no header at all; this is what
    # lets the existing 53 pytest files and the local run procedure stay unmodified.
    body = client.get("/any").json()
    assert body == {"username": "local-dev", "role": "admin"}
    # The bypass principal is admin, so admin routes open too.
    assert client.get("/admin-only").status_code == 200


def test_missing_authorization_header_is_401(with_auth):
    client = TestClient(_probe_app())
    r = client.get("/any")
    assert r.status_code == 401
    # WWW-Authenticate is the standard header accompanying a 401.
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
    # Authenticated but not authorized: 403 is correct (401 means "log in again").
    assert r.status_code == 403


def test_unauthenticated_request_to_admin_route_is_401_not_403(with_auth):
    # require_admin first requires authentication via require_user, so a request with no
    # header must be unauthenticated (401), not insufficient permission (403).
    # Task 7 relies on this order across all 34 routes.
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
    # Keep a deploy script that exports env vars as empty strings from silently turning auth on.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


def test_config_requires_both_pool_and_client(monkeypatch):
    # A half-configured state with a pool but no client is not "unconfigured" but a
    # deployment accident; treating it as None (bypass) would let every request silently
    # pass as a virtual admin. So it blows up immediately with an exception (fail-closed):
    # even if that means a 500, a visible failure beats a silent privilege leak.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "")
    with pytest.raises(RuntimeError, match="AIPDS_COGNITO_USER_POOL_ID"
                       ".*AIPDS_COGNITO_CLIENT_ID"):
        app_module.cognito_config()


def test_config_requires_both_client_and_pool(monkeypatch):
    # Symmetric case: a client with no pool is the same deployment accident, so it blows up too.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", CLIENT_ID)
    with pytest.raises(RuntimeError, match="AIPDS_COGNITO_USER_POOL_ID"
                       ".*AIPDS_COGNITO_CLIENT_ID"):
        app_module.cognito_config()


def test_both_unset_still_bypasses(monkeypatch):
    # Neither being set at all (the default state for local development, with the switch on; see conftest)
    # is still a bypass; it must not be confused with the half-configured state.
    monkeypatch.delenv("AIPDS_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("AIPDS_COGNITO_CLIENT_ID", raising=False)
    assert app_module.cognito_config() is None


def test_both_empty_strings_still_bypasses(monkeypatch):
    # Both being empty strings is also equivalent to "unconfigured" (both absent vs both
    # present; a normal bypass path, distinct from only one being present).
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "")
    assert app_module.cognito_config() is None


@pytest.mark.parametrize("flag", [None, "", "0", "false"])
def test_both_unset_without_the_switch_refuses_to_run(monkeypatch, flag):
    """A deployment missing both lines is not "unconfigured" but an accident: reading it as a
    bypass would open it to the internet with auth off. The bypass is enabled only by AIPDS_AUTH_DISABLED."""
    monkeypatch.delenv("AIPDS_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("AIPDS_COGNITO_CLIENT_ID", raising=False)
    if flag is None:
        monkeypatch.delenv("AIPDS_AUTH_DISABLED", raising=False)
    else:
        monkeypatch.setenv("AIPDS_AUTH_DISABLED", flag)
    with pytest.raises(RuntimeError, match="AIPDS_AUTH_DISABLED"):
        app_module.cognito_config()


def test_the_switch_does_not_override_a_configured_pool(monkeypatch):
    """Even if the switch is left on, auth is on when the values are present; it is not read only in the off direction."""
    monkeypatch.setenv("AIPDS_AUTH_DISABLED", "1")
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", CLIENT_ID)
    assert app_module.cognito_config()["user_pool_id"] == POOL


async def test_require_user_raises_on_half_configured_env(monkeypatch):
    # This test actually pins the security property: in a half-configured environment,
    # require_user must not return a Principal (in particular, it must not return
    # LOCAL_PRINCIPAL); the exception must propagate as is. If cognito_config() were
    # reverted to return None in the half-configured case, this test must fail.
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", "")

    from starlette.requests import Request as StarletteRequest

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/any"}
    request = StarletteRequest(scope)
    with pytest.raises(RuntimeError):
        await require_user(request)


def test_config_is_read_when_both_present(monkeypatch):
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AIPDS_S3_REGION", REGION)
    monkeypatch.delenv("AIPDS_COGNITO_REGION", raising=False)
    cfg = app_module.cognito_config()
    assert cfg == {"region": REGION, "user_pool_id": POOL, "client_id": CLIENT_ID}


def test_cognito_region_env_overrides_s3_region(monkeypatch):
    monkeypatch.setenv("AIPDS_COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("AIPDS_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AIPDS_S3_REGION", "ap-northeast-2")
    monkeypatch.setenv("AIPDS_COGNITO_REGION", "us-east-1")
    assert app_module.cognito_config()["region"] == "us-east-1"
