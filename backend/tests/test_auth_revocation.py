# backend/tests/test_auth_revocation.py — 관리자가 끊은 사용자의 토큰을 만료 전에 거부한다
# (aipds/auth/revocation.py). 관리 라우트가 언제 끊는지는 test_routes_admin_users.py.
from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.auth.deps import require_user
from aipds.auth.models import Principal
from aipds.auth.revocation import KEEP_SECONDS, Revocations

NOW = 1_800_000_000.0


def test_tokens_issued_before_the_cutoff_are_revoked(tmp_path):
    store = Revocations(tmp_path / "r.json")
    store.revoke("pm", now=NOW)
    assert store.is_revoked("pm", NOW - 60, now=NOW + 1)
    assert not store.is_revoked("pm", NOW + 5, now=NOW + 10)   # 다시 로그인한 토큰
    assert not store.is_revoked("someone-else", NOW - 60, now=NOW + 1)


def test_a_token_from_the_same_second_passes(tmp_path):
    """`iat`는 초 단위다 — 재설정 직후 다시 로그인한 토큰을 거부하지 않는다."""
    store = Revocations(tmp_path / "r.json")
    store.revoke("pm", now=NOW + 0.7)
    assert not store.is_revoked("pm", NOW, now=NOW + 1)


def test_a_token_without_iat_is_revoked_while_the_record_lives(tmp_path):
    store = Revocations(tmp_path / "r.json")
    store.revoke("pm", now=NOW)
    assert store.is_revoked("pm", None, now=NOW + 1)


def test_records_expire_after_the_token_lifetime(tmp_path):
    store = Revocations(tmp_path / "r.json")
    store.revoke("pm", now=NOW)
    assert not store.is_revoked("pm", NOW - 60, now=NOW + KEEP_SECONDS)
    store.revoke("other", now=NOW + KEEP_SECONDS)
    assert "pm" not in store._cutoffs


def test_records_survive_a_restart(tmp_path):
    """갱신이 백엔드를 재시작한다 — 메모리에만 있으면 끊은 세션이 되살아난다."""
    path = tmp_path / "r.json"
    Revocations(path).revoke("pm", now=NOW)
    assert Revocations(path).is_revoked("pm", NOW - 60, now=NOW + 1)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_an_unreadable_file_starts_empty(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("not json", encoding="utf-8")
    assert not Revocations(path).is_revoked("pm", NOW, now=NOW)


@pytest.fixture()
def with_auth(monkeypatch):
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": "ap-northeast-2", "user_pool_id": "pool", "client_id": "client"})
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())
    principals: dict[str, Principal] = {}

    async def fake_verify(token, **kwargs):
        return principals[token]

    import aipds.auth.deps as deps_module
    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    return principals


def _probe() -> TestClient:
    app = FastAPI()

    @app.get("/any")
    async def any_role(p: Principal = Depends(require_user)):
        return {"username": p.username}

    return TestClient(app)


def test_require_user_rejects_a_token_issued_before_revocation(with_auth):
    """비활성화·삭제·강등·재설정 뒤에도 이미 받은 access 토큰은 서명·만료 검증을 통과한다
    — 로컬 검증은 Cognito에 묻지 않는다. 기록이 그것을 401로 만든다."""
    now = time.time()
    with_auth["old"] = Principal(username="pm", sub="s", role="pm", issued_at=now - 600)
    with_auth["new"] = Principal(username="pm", sub="s", role="pm", issued_at=now + 5)
    client = _probe()
    assert client.get("/any", headers={"Authorization": "Bearer old"}).status_code == 200

    app_module.revocations().revoke("pm", now=now)

    assert client.get("/any", headers={"Authorization": "Bearer old"}).status_code == 401
    assert client.get("/any", headers={"Authorization": "Bearer new"}).status_code == 200
