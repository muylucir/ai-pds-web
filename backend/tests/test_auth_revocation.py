# backend/tests/test_auth_revocation.py — rejects, before expiry, the tokens of a user an admin has cut off
# (aipds/auth/revocation.py). When the admin routes cut users off is covered in test_routes_admin_users.py.
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
    assert not store.is_revoked("pm", NOW + 5, now=NOW + 10)   # a token from logging in again
    assert not store.is_revoked("someone-else", NOW - 60, now=NOW + 1)


def test_a_token_from_the_same_second_passes(tmp_path):
    """`iat` is in seconds — a token from logging in again right after a reset is not rejected."""
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
    """An update restarts the backend — if it were only in memory, cut-off sessions would come back."""
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
    """Even after disable, delete, demote, or reset, an already issued access token passes signature and expiry checks
    — local verification does not ask Cognito. The record is what turns it into a 401."""
    now = time.time()
    with_auth["old"] = Principal(username="pm", sub="s", role="pm", issued_at=now - 600)
    with_auth["new"] = Principal(username="pm", sub="s", role="pm", issued_at=now + 5)
    client = _probe()
    assert client.get("/any", headers={"Authorization": "Bearer old"}).status_code == 200

    app_module.revocations().revoke("pm", now=now)

    assert client.get("/any", headers={"Authorization": "Bearer old"}).status_code == 401
    assert client.get("/any", headers={"Authorization": "Bearer new"}).status_code == 200
