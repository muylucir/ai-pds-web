# backend/tests/test_routes_admin_users.py
#
# 라우트 계층의 책임만 시험한다: 정책(마지막 관리자 보호, 부분 실패 롤백)과
# 오류 코드 번역. Cognito 호출 자체는 test_auth_cognito.py가 Stubber로 검증하므로
# 여기서는 CognitoAdmin을 가짜로 갈아끼운다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.cognito import CognitoError, ManagedUser
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal

ADMIN_EMAIL = "admin@aipds.local"
PM_EMAIL = "pm@aipds.local"


class FakeCognito:
    """CognitoAdmin의 인메모리 대역. 호출 순서를 calls에 기록한다."""

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
        # 실제 CognitoAdmin.groups_of는 존재하지 않는 사용자에 대해
        # UserNotFoundException을 낸다 — 여기서 []를 반환하는 관대한 흉내를
        # 내면 가드가 404를 내야 할 경로가 조용히 통과(마스킹)될 수 있다.
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
    """가짜 Cognito + '나는 admin@aipds.local' 라는 요청자."""
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


# ---- 목록 ----

def test_list_returns_users_with_role_and_status(env, client):
    body = client.get("/admin/users").json()
    emails = {u["email"] for u in body["users"]}
    assert emails == {ADMIN_EMAIL, PM_EMAIL}
    admin_row = next(u for u in body["users"] if u["email"] == ADMIN_EMAIL)
    assert admin_row["role"] == "admin"
    assert admin_row["enabled"] is True
    assert admin_row["status"] == "CONFIRMED"
    # 화면은 email을 보여주고 액션은 username을 보낸다 — 둘 다 나와야 한다.
    assert admin_row["username"] == ADMIN_EMAIL


# ---- 초대 ----

def test_invite_creates_sets_password_and_assigns_group_in_order(env, client):
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@x.io"
    assert body["role"] == "pm"
    # 임시 비밀번호는 응답에 1회만 실린다 — 서버는 저장하지 않는다.
    assert len(body["temp_password"]) == 16
    ops = [c[0] for c in env.calls]
    assert ops == ["create_user", "set_temp_password", "set_group"], ops


def test_invite_rejects_an_unknown_role(env, client):
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "superuser"})
    assert r.status_code == 422
    assert env.calls == [], "잘못된 역할로 사용자를 만들어서는 안 된다"


def test_invite_rejects_a_malformed_email(env, client):
    r = client.post("/admin/users", json={"email": "not-an-email", "role": "pm"})
    assert r.status_code == 422
    assert env.calls == []


def test_duplicate_email_is_409(env, client):
    # email-validator(2.x)는 RFC 6762에 따라 .local을 특수 예약 도메인으로
    # 보고 거부한다(check_deliverability=False라도 마찬가지) — PM_EMAIL을 쓰면
    # EmailStr 단계에서 422가 나 create_user에 도달하지 못한다. 이 테스트가
    # 검증하는 것은 "이미 존재하는 이메일 → 409" 매핑이고 FakeCognito.create_user는
    # fail_on이 설정되면 이메일 값과 무관하게 실패하므로, 일반 도메인으로 바꿔도
    # 테스트 의도는 그대로다.
    env.fail_on["create_user"] = CognitoError("UsernameExistsException", "exists")
    r = client.post("/admin/users", json={"email": "dup@x.io", "role": "pm"})
    assert r.status_code == 409


def test_alias_exists_is_also_409(env, client):
    env.fail_on["create_user"] = CognitoError("AliasExistsException", "alias")
    r = client.post("/admin/users", json={"email": "x@x.io", "role": "pm"})
    assert r.status_code == 409


def test_password_failure_rolls_back_the_created_user(env, client):
    # 반쯤 만들어진 계정(비밀번호 없음 / 그룹 없음)을 남기지 않는다 —
    # projects.py의 매니페스트 실패 롤백과 같은 규율.
    env.fail_on["set_temp_password"] = CognitoError("InvalidPasswordException", "weak")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@x.io"], f"rollback did not delete the user: {env.deleted}"


def test_group_failure_rolls_back_the_created_user(env, client):
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500
    assert env.deleted == ["new@x.io"]


def test_rollback_failure_still_returns_500(env, client):
    # 롤백까지 실패하면 우리가 할 수 있는 것이 없다 — 500을 내고 로그에 남긴다.
    env.fail_on["set_group"] = CognitoError("ResourceNotFoundException", "no group")
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users", json={"email": "new@x.io", "role": "pm"})
    assert r.status_code == 500


# ---- 비밀번호 재설정 ----

def test_reset_password_returns_a_new_temp_password(env, client):
    r = client.post(f"/admin/users/{PM_EMAIL}/reset-password")
    assert r.status_code == 200
    assert len(r.json()["temp_password"]) == 16
    assert [c[0] for c in env.calls] == ["set_temp_password"]


def test_reset_password_on_unknown_user_is_404(env, client):
    env.fail_on["set_temp_password"] = CognitoError("UserNotFoundException", "gone")
    r = client.post("/admin/users/ghost@x.io/reset-password")
    assert r.status_code == 404


def test_reset_password_rejected_by_pool_policy_is_500_not_502(env, client):
    # 우리가 서버에서 생성한 임시 비밀번호가 풀 정책을 만족시키지 못했다면
    # 그건 Cognito가 아니라 우리 쪽 버그다 — 502(업스트림 장애)가 아니라
    # 500이어야 한다.
    env.fail_on["set_temp_password"] = CognitoError("InvalidPasswordException", "weak")
    r = client.post(f"/admin/users/{PM_EMAIL}/reset-password")
    assert r.status_code == 500
    assert "InvalidPasswordException" not in r.text


# ---- 역할 변경 ----

def test_role_change_replaces_the_group(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json() == {"username": PM_EMAIL, "role": "admin"}
    assert ("set_group", PM_EMAIL, "admin") in env.calls


def test_cannot_demote_yourself(env, client):
    # 이게 없으면 관리자가 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 안 남는다.
    r = client.put(f"/admin/users/{ADMIN_EMAIL}/role", json={"role": "pm"})
    assert r.status_code == 400
    # detail은 안정적 코드다 — 프론트 딕셔너리가 이 값으로 문구를 찾는다.
    # 한국어 문장으로 회귀하면 영어 UI에 한국어가 뜬다.
    assert r.json()["detail"] == "self_target"
    assert "set_group" not in [c[0] for c in env.calls]


def test_cannot_demote_yourself_by_a_case_variant_of_your_own_username(env, client):
    # Cognito는 Username을 대소문자 구분 없이 해석한다(이 풀은 email이
    # Username이다) — 대문자로 바꾼 변형도 같은 계정을 가리켜야 하고, 그
    # 계정을 강등할 수 있어서는 안 된다.
    r = client.put(f"/admin/users/{ADMIN_EMAIL.upper()}/role", json={"role": "pm"})
    assert r.status_code == 400
    assert "set_group" not in [c[0] for c in env.calls]


def test_cannot_demote_the_last_admin(env, client):
    # 요청자가 아닌 다른 계정이지만 유일한 admin인 경우.
    env.users.pop(ADMIN_EMAIL)
    env.add("other-admin@x.io", role="admin")
    r = client.put("/admin/users/other-admin@x.io/role", json={"role": "pm"})
    assert r.status_code == 400
    assert r.json()["detail"] == "last_admin"
    assert "set_group" not in [c[0] for c in env.calls]


def test_can_demote_an_admin_when_another_admin_remains(env, client):
    env.add("second-admin@x.io", role="admin")
    r = client.put("/admin/users/second-admin@x.io/role", json={"role": "pm"})
    assert r.status_code == 200


def test_promoting_to_admin_is_always_allowed(env, client):
    r = client.put(f"/admin/users/{PM_EMAIL}/role", json={"role": "admin"})
    assert r.status_code == 200


# ---- 비활성 / 활성 ----

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
    env.add("other-admin@x.io", role="admin")
    assert client.post("/admin/users/other-admin@x.io/disable").status_code == 400
    assert "set_enabled" not in [c[0] for c in env.calls]


def test_enable_is_never_blocked(env, client):
    # 활성화는 권한을 넓히는 방향이므로 마지막 관리자 보호와 무관하다.
    env.add("disabled@x.io", role="admin", enabled=False)
    assert client.post("/admin/users/disabled@x.io/enable").status_code == 204


def test_enabling_yourself_is_allowed(env, client):
    assert client.post(f"/admin/users/{ADMIN_EMAIL}/enable").status_code == 204


# ---- 삭제 ----

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
    env.add("other-admin@x.io", role="admin")
    assert client.delete("/admin/users/other-admin@x.io").status_code == 400
    assert "delete_user" not in [c[0] for c in env.calls]


def test_delete_unknown_user_is_404(env, client):
    env.fail_on["delete_user"] = CognitoError("UserNotFoundException", "gone")
    assert client.delete("/admin/users/ghost@x.io").status_code == 404


# ---- 오류 번역 ----

def test_unexpected_cognito_error_is_502(env, client):
    env.fail_on["list_users"] = CognitoError("InternalErrorException", "boom")
    r = client.get("/admin/users")
    assert r.status_code == 502
    # 내부 세부사항을 사용자에게 노출하지 않는다.
    assert "InternalErrorException" not in r.text


# ---- 권한 (require_admin 우회 불가) ----

def test_pm_cannot_reach_admin_routes(monkeypatch, client):
    # dependency_overrides 없이 실제 require_admin을 통과시켜본다.
    fake = FakeCognito()
    monkeypatch.setattr(app_module, "cognito_admin", lambda: fake)
    monkeypatch.setattr(app_module, "cognito_config", lambda: {
        "region": "ap-northeast-2", "user_pool_id": "p", "client_id": "c"})

    import pathfinder.auth.deps as deps_module

    async def fake_verify(token, **kwargs):
        return Principal(username=PM_EMAIL, sub="s-pm", role="pm")

    monkeypatch.setattr(deps_module, "verify_access_token", fake_verify)
    monkeypatch.setattr(app_module, "jwks_cache", lambda: object())
    r = client.get("/admin/users", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert fake.calls == [], "pm의 요청이 Cognito까지 도달해서는 안 된다"
