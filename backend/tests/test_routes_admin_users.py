# backend/tests/test_routes_admin_users.py
#
# Tests only the route layer's responsibilities: policy (last-admin protection, rollback on
# partial failure) and error code translation. The Cognito calls themselves are verified
# with Stubber in test_auth_cognito.py, so here CognitoAdmin is swapped for a fake.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.auth.cognito import CognitoError, ManagedUser
from aipds.auth.deps import require_admin, require_user
from aipds.auth.models import Principal

ADMIN_EMAIL = "admin@aipds.local"
PM_EMAIL = "pm@aipds.local"


class FakeCognito:
    """In-memory stand-in for CognitoAdmin. Records the call order in calls."""

    def __init__(self) -> None:
        self.users: dict[str, ManagedUser] = {}
        self.calls: list[tuple] = []
        self.fail_on: dict[str, CognitoError] = {}
        self.deleted: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail_on:
            raise self.fail_on[op]

    def add(self, email: str, role: str | None = "pm", enabled: bool = True,
            status: str = "CONFIRMED") -> None:
        self.users[email] = ManagedUser(username=email, email=email, role=role,
                                        status=status, enabled=enabled,
                                        created_at="2026-07-25T00:00:00+00:00")

    def list_users(self):
        self.calls.append(("list_users",))
        self._maybe_fail("list_users")
        return list(self.users.values())

    def admin_count(self) -> int:
        return sum(1 for u in self.users.values() if u.role == "admin")

    def groups_of(self, username: str):
        # The real CognitoAdmin.groups_of raises UserNotFoundException for a
        # nonexistent user; a lenient imitation returning [] here could let a path
        # where the guard should return 404 pass silently (masking it).
        u = self.users.get(username)
        if u is None:
            raise CognitoError("UserNotFoundException", f"{username} not found")
        return [u.role] if u.role else []

    def create_user(self, email: str) -> str:
        self.calls.append(("create_user", email))
        self._maybe_fail("create_user")
        self.add(email, role=None, status="FORCE_CHANGE_PASSWORD")
        return email

    def set_temp_password(self, username: str, password: str) -> None:
        self.calls.append(("set_temp_password", username, password))
        self._maybe_fail("set_temp_password")

    def set_group(self, username: str, role: str) -> None:
        self.calls.append(("set_group", username, role))
        self._maybe_fail("set_group")
        if username in self.users:
            self.users[username].role = role

    def set_enabled(self, username: str, enabled: bool) -> None:
        self.calls.append(("set_enabled", username, enabled))
        self._maybe_fail("set_enabled")
        if username in self.users:
            self.users[username].enabled = enabled

    def delete_user(self, username: str) -> None:
        self.calls.append(("delete_user", username))
        self.deleted.append(username)
        self._maybe_fail("delete_user")
        self.users.pop(username, None)


@pytest.fixture()
def env(monkeypatch):
    """Fake Cognito + a requester who says 'I am admin@aipds.local'."""
    fake = FakeCognito()
    fake.add(ADMIN_EMAIL, role="admin")
    fake.add(PM_EMAIL, role="pm")
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)

    me = Principal(username=ADMIN_EMAIL, sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield fake
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# ---- List ----

def test_list_returns_users_with_role_and_status(env, client):
    body = client.get("/admin/users").json()
    emails = {u["email"] for u in body["users"]}
    assert emails == {ADMIN_EMAIL, PM_EMAIL}
    admin_row = next(u for u in body["users"] if u["email"] == ADMIN_EMAIL)
    assert admin_row["role"] == "admin"
    assert admin_row["enabled"] is True
    assert admin_row["status"] == "CONFIRMED"
    # The screen shows the email and actions send the username; both must be present.
    assert admin_row["username"] == ADMIN_EMAIL


# ---- Invite ----

def test_invite_creates_sets_password_and_assigns_group_in_order(env, client):
    r = client.post("/admin/users", json={"email": "new@example.com", "role": "pm"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "pm"
    # The temporary password appears in the response only once; the server does not store it.
    assert len(body["temp_password"]) == 16
    ops = [c[0] for c in env.calls]
    assert ops == ["create_user", "set_temp_password", "set_group"], ops


def test_invite_rejects_an_unknown_role(env, client):
    r = client.post("/admin/users", json={"email": "new@example.com", "role": "superuser"})
    assert r.status_code == 422
    assert env.calls == [], "잘못된 역할로 사용자를 만들어서는 안 된다"


@pytest.mark.parametrize("email", ["not-an-email", "kim@corp", "new@aipds.local", "kim@@x.com"])
def test_invite_rejects_a_malformed_email_with_a_code(env, client, email):
    # The default 422 detail is an array of error objects, so the screen could not find a
    # code and showed "[object Object]". The browser's type=email lets two of the first three
    # (kim@corp, .local) through.
    r = client.post("/admin/users", json={"email": email, "role": "pm"})
    assert (r.status_code, r.json()["detail"]) == (400, "email_invalid")
    assert env.calls == []


def test_invite_trims_the_email(env, client):
    r = client.post("/admin/users", json={"email": "  new@example.com ", "role": "pm"})
    assert r.status_code == 201
    assert env.calls[0] == ("create_user", "new@example.com")


def test_duplicate_email_is_409(env, client):
    # email-validator (2.x) treats .local as a special-use reserved domain per RFC 6762
    # and rejects it (even with check_deliverability=False); using PM_EMAIL would give a
    # 400 at the email check and never reach create_user. What this test verifies is the
    # "email already exists -> 409" mapping, and FakeCognito.create_user fails whenever
    # fail_on is set regardless of the email value, so switching to an ordinary domain
    # keeps the test's intent intact.
    env.fail_on["create_user"] = CognitoError("UsernameExistsException", "exists")
    r = client.post("/admin/users", json={"email": "dup@example.com", "role": "pm"})
    assert r.status_code == 409


def test_alias_exists_is_also_409(env, client):
    env.fail_on["create_user"] = CognitoError("AliasExistsException", "alias")
    r = client.post("/admin/users", json={"email": "x@example.com", "role": "pm"})
    assert r.status_code == 409


def test_password_failure_rolls_back_the_created_user(env, client):
    # Do not leave a half-created account (no password / no group) behind;
    # same discipline as the manifest-failure rollback in projects.py.
    env.fail_on["set_temp_password"] = CognitoError("InvalidPasswordException", "weak")
    r = client.post("/admin/users", json={"email": "new@example.com", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@example.com"], f"rollback did not delete the user: {env.deleted}"


def test_group_failure_rolls_back_the_created_user(env, client):
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    r = client.post("/admin/users", json={"email": "new@example.com", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@example.com"]


def test_rollback_failure_still_returns_500(env, client):
    # If the rollback also fails, there is nothing we can do: return 500 and log it.
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users", json={"email": "new@example.com", "role": "pm"})
    assert r.status_code == 500


# ---- Password reset ----

def test_reset_password_returns_a_new_temp_password(env, client):
    r = client.post(f"/admin/users/{PM_EMAIL}/reset-password")
    assert r.status_code == 200
    assert len(r.json()["temp_password"]) == 16
    assert [c[0] for c in env.calls] == ["set_temp_password"]


def test_reset_password_on_unknown_user_is_404(env, client):
    env.fail_on["set_temp_password"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users/ghost@example.com/reset-password")
    assert r.status_code == 404


def test_reset_password_rejected_by_pool_policy_is_500_not_502(env, client):
    # If the temporary password we generated on the server fails the pool policy,
    # that is our bug, not Cognito's: it must be 500, not 502 (upstream
    # failure).
    env.fail_on["set_temp_password"] = CognitoError("InvalidPasswordException", "weak")
    r = client.post(f"/admin/users/{PM_EMAIL}/reset-password")
    assert r.status_code == 500
    assert "InvalidPasswordException" not in r.text


# ---- Role change ----

def test_role_change_replaces_the_group(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json() == {"username": PM_EMAIL, "role": "admin"}
    assert ("set_group", PM_EMAIL, "admin") in env.calls


def test_cannot_demote_yourself(env, client):
    # Without this, an admin can lock themselves out, leaving the AWS console as the only recovery path.
    r = client.put(f"/admin/users/{ADMIN_EMAIL}/role", json={"role": "pm"})
    assert r.status_code == 400
    # detail is a stable code; the frontend dictionary looks up the wording by this value.
    # If it regresses to a Korean sentence, Korean shows up in the English UI.
    assert r.json()["detail"] == "self_target"
    assert "set_group" not in [c[0] for c in env.calls]


def test_cannot_demote_yourself_by_a_case_variant_of_your_own_username(env, client):
    # Cognito resolves Username case-insensitively (in this pool the email is the
    # Username), so an uppercased variant must point to the same account, and it must
    # not be possible to demote that account.
    r = client.put(f"/admin/users/{ADMIN_EMAIL.upper()}/role", json={"role": "pm"})
    assert r.status_code == 400
    assert "set_group" not in [c[0] for c in env.calls]


def test_cannot_demote_the_last_admin(env, client):
    # A different account from the requester, but the only admin.
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@example.com", role="admin")
    r = client.put("/admin/users/other-admin@example.com/role", json={"role": "pm"})
    assert r.status_code == 400
    assert r.json()["detail"] == "last_admin"
    assert "set_group" not in [c[0] for c in env.calls]


def test_can_demote_an_admin_when_another_admin_remains(env, client):
    env.add("second-admin@example.com", role="admin")
    r = client.put("/admin/users/second-admin@example.com/role", json={"role": "pm"})
    assert r.status_code == 200


def test_promoting_to_admin_is_always_allowed(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200


# ---- Disable / enable ----

def test_disable_a_pm(env, client):
    assert client.post(f"/admin/users/{PM_EMAIL}/disable").status_code == 204
    assert ("set_enabled", PM_EMAIL, False) in env.calls


def test_cannot_disable_yourself(env, client):
    r = client.post(f"/admin/users/{ADMIN_EMAIL}/disable")
    assert r.status_code == 400
    assert "set_enabled" not in [c[0] for c in env.calls]


def test_cannot_disable_yourself_by_a_case_variant_of_your_own_username(env, client):
    r = client.post(f"/admin/users/{ADMIN_EMAIL.upper()}/disable")
    assert r.status_code == 400
    assert "set_enabled" not in [c[0] for c in env.calls]


def test_cannot_disable_the_last_admin(env, client):
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@example.com", role="admin")
    assert client.post("/admin/users/other-admin@example.com/disable").status_code == 400
    assert "set_enabled" not in [c[0] for c in env.calls]


def test_enable_is_never_blocked(env, client):
    # Enabling widens permissions, so last-admin protection does not apply.
    env.add("disabled@example.com", role="admin", enabled=False)
    assert client.post("/admin/users/disabled@example.com/enable").status_code == 204


def test_enabling_yourself_is_allowed(env, client):
    assert client.post(f"/admin/users/{ADMIN_EMAIL}/enable").status_code == 204


# ---- Delete ----

def test_delete_a_pm(env, client):
    assert client.delete(f"/admin/users/{PM_EMAIL}").status_code == 204
    assert PM_EMAIL not in env.users


def test_cannot_delete_yourself(env, client):
    r = client.delete(f"/admin/users/{ADMIN_EMAIL}")
    assert r.status_code == 400
    assert ADMIN_EMAIL in env.users
    assert "delete_user" not in [c[0] for c in env.calls]


def test_cannot_delete_yourself_by_a_case_variant_of_your_own_username(env, client):
    r = client.delete(f"/admin/users/{ADMIN_EMAIL.upper()}")
    assert r.status_code == 400
    assert ADMIN_EMAIL in env.users
    assert "delete_user" not in [c[0] for c in env.calls]


def test_cannot_delete_the_last_admin(env, client):
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@example.com", role="admin")
    assert client.delete("/admin/users/other-admin@example.com").status_code == 400
    assert "delete_user" not in [c[0] for c in env.calls]


def test_delete_unknown_user_is_404(env, client):
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    assert client.delete("/admin/users/ghost@example.com").status_code == 404


# ---- Error translation ----

def test_unexpected_cognito_error_is_502(env, client):
    env.fail_on["list_users"] = CognitoError("InternalErrorException", "boom")
    r = client.get("/admin/users")
    assert r.status_code == 502
    # Do not expose internal details to the user.
    assert "InternalErrorException" not in r.text


# ---- Cutting off existing sessions (auth/revocation.py) ----

@pytest.mark.parametrize("call", [
    lambda c: c.post(f"/admin/users/{PM_EMAIL}/disable"),
    lambda c: c.delete(f"/admin/users/{PM_EMAIL}"),
    lambda c: c.post(f"/admin/users/{PM_EMAIL}/reset-password"),
    lambda c: c.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "pm"}),
])
def test_cutting_access_ends_the_sessions_already_issued(env, client, call):
    """Even after disable, delete, reset, or demotion, an access token already issued keeps
    passing until it expires, so record that the user's existing tokens are to be rejected."""
    assert call(client).status_code in (200, 204)
    assert app_module.revocations().is_revoked(PM_EMAIL, issued_at=0)


def test_promotion_and_enabling_keep_the_sessions(env, client):
    """Promotion and enabling widen permissions; existing tokens merely carry fewer permissions, so they are not cut off."""
    client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    client.post(f"/admin/users/{PM_EMAIL}/enable")
    assert not app_module.revocations().is_revoked(PM_EMAIL, issued_at=0)


def test_a_failed_change_does_not_end_the_sessions(env, client):
    env.fail_on["set_enabled"] = CognitoError("InternalErrorException", "boom")
    assert client.post(f"/admin/users/{PM_EMAIL}/disable").status_code >= 500
    assert not app_module.revocations().is_revoked(PM_EMAIL, issued_at=0)


# ---- Authorization (require_admin cannot be bypassed) ----

def test_pm_cannot_reach_admin_routes(monkeypatch, client):
    # Go through the real require_admin without dependency_overrides.
    fake = FakeCognito()
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": "ap-northeast-2", "user_pool_id": "p", "client_id": "c"})

    import aipds.auth.deps as deps_module

    async def fake_verify(token, **kwargs):
        return Principal(username=PM_EMAIL, sub="s-pm", role="pm")

    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())
    r = client.get("/admin/users", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert fake.calls == [], "pm의 요청이 Cognito까지 도달해서는 안 된다"
