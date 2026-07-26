# backend/tests/test_auth_cognito.py
#
# botocore Stubber로 실 Cognito 없이 래퍼를 시험한다. Stubber는 파라미터가
# 예상과 정확히 일치할 때만 응답을 내놓으므로, "무엇을 어떻게 호출하는가"가
# 그대로 단정된다.
from __future__ import annotations

import re

import boto3
import pytest
from botocore.stub import ANY, Stubber

from pathfinder.auth.cognito import (CognitoAdmin, CognitoError, ManagedUser,
                                     generate_temp_password)

POOL = "ap-northeast-2_TEST123"


@pytest.fixture()
def admin():
    client = boto3.client("cognito-idp", region_name="ap-northeast-2",
                          aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.activate()
    yield CognitoAdmin(client, POOL), stub
    stub.deactivate()


def _user(username: str, email: str, status="CONFIRMED", enabled=True):
    from datetime import datetime, timezone
    return {
        "Username": username,
        "Attributes": [{"Name": "email", "Value": email}],
        "UserStatus": status,
        "Enabled": enabled,
        "UserCreateDate": datetime(2026, 7, 25, tzinfo=timezone.utc),
    }


# ---- 비밀번호 생성 ----

def test_generated_password_satisfies_the_pool_policy():
    # 정책은 8자+ 대/소/숫자/기호. 생성기가 정책을 못 맞추면 사용자는 이미
    # 만들어진 뒤 InvalidPasswordException이 나므로 반드시 만족해야 한다.
    for _ in range(50):
        pw = generate_temp_password()
        assert len(pw) == 16
        assert re.search(r"[a-z]", pw), pw
        assert re.search(r"[A-Z]", pw), pw
        assert re.search(r"[0-9]", pw), pw
        assert re.search(r"[!@#$%^&*_\-+=?]", pw), pw


def test_generated_passwords_are_not_repeated():
    assert len({generate_temp_password() for _ in range(50)}) == 50


# ---- 목록 ----

def test_list_users_maps_attributes_and_groups(admin):
    a, stub = admin
    stub.add_response(
        "list_users",
        {"Users": [_user("admin@pathfinder.local", "admin@pathfinder.local"),
                   _user("pm@pathfinder.local", "pm@pathfinder.local")]},
        {"UserPoolId": POOL, "Limit": 60},
    )
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "admin@pathfinder.local"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "pm@pathfinder.local"})
    users = a.list_users()
    assert [u.email for u in users] == ["admin@pathfinder.local", "pm@pathfinder.local"]
    assert [u.role for u in users] == ["admin", "pm"]
    assert users[0].status == "CONFIRMED" and users[0].enabled is True
    assert users[0].created_at.startswith("2026-07-25")


def test_list_users_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users",
                      {"Users": [_user("a@x.io", "a@x.io")], "PaginationToken": "t1"},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "a@x.io"})
    stub.add_response("list_users", {"Users": [_user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "Limit": 60, "PaginationToken": "t1"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "b@x.io"})
    assert [u.email for u in a.list_users()] == ["a@x.io", "b@x.io"]


def test_user_with_no_group_has_role_none(admin):
    # 그룹 배정 전에 실패한 반쯤 만들어진 계정을 화면에서 알아볼 수 있어야 한다.
    a, stub = admin
    stub.add_response("list_users", {"Users": [_user("x@x.io", "x@x.io")]},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": []},
                      {"UserPoolId": POOL, "Username": "x@x.io"})
    assert a.list_users()[0].role is None


def test_missing_email_attribute_falls_back_to_username(admin):
    a, stub = admin
    raw = _user("legacy-user", "unused")
    raw["Attributes"] = []
    stub.add_response("list_users", {"Users": [raw]}, {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "legacy-user"})
    assert a.list_users()[0].email == "legacy-user"


# ---- 생성 ----

def test_create_user_suppresses_email_and_marks_it_verified(admin):
    a, stub = admin
    # email_verified=true는 선택이 아니다 — alias(email) 사인인의 조건이다.
    stub.add_response(
        "admin_create_user",
        {"User": _user("new@x.io", "new@x.io", status="FORCE_CHANGE_PASSWORD")},
        {"UserPoolId": POOL, "Username": "new@x.io", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "new@x.io"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("new@x.io") == "new@x.io"


def test_duplicate_email_raises_cognito_error_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="UsernameExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("dup@x.io")
    assert exc.value.code == "UsernameExistsException"


def test_alias_exists_is_also_surfaced_by_code(admin):
    # 이메일이 다른 계정의 alias로 이미 쓰이는 경우.
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="AliasExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("alias@x.io")
    assert exc.value.code == "AliasExistsException"


# ---- 비밀번호 ----

def test_set_temp_password_is_not_permanent(admin):
    # Permanent=False여야 첫 로그인에서 사용자가 직접 바꾼다(초대 흐름).
    a, stub = admin
    stub.add_response("admin_set_user_password", {},
                      {"UserPoolId": POOL, "Username": "u@x.io",
                       "Password": "Tmp!23456789abcd", "Permanent": False})
    a.set_temp_password("u@x.io", "Tmp!23456789abcd")


def test_invalid_password_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_set_user_password",
                         service_error_code="InvalidPasswordException")
    with pytest.raises(CognitoError) as exc:
        a.set_temp_password("u@x.io", "weak")
    assert exc.value.code == "InvalidPasswordException"


# ---- 그룹(역할) ----

def test_set_group_removes_existing_roles_before_adding(admin):
    # 역할 교체는 "추가"가 아니라 "교체"다. 제거를 빠뜨리면 사용자가 두 그룹에
    # 속해 강등이 무효가 된다(verifier가 admin을 우선하므로).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_remove_user_from_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "admin"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "pm"})
    a.set_group("u@x.io", "pm")


def test_set_group_leaves_unrelated_groups_alone(admin):
    # admin/pm이 아닌 그룹은 우리 관심사가 아니다 — 건드리지 않는다.
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "some-other-group"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "admin"})
    a.set_group("u@x.io", "admin")


def test_set_group_is_a_noop_add_when_already_correct(admin):
    # 이미 맞는 그룹이면 제거하지 않고 추가만 한다(멱등).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@x.io", "GroupName": "pm"})
    a.set_group("u@x.io", "pm")


# ---- 활성/비활성/삭제 ----

def test_disable_and_enable(admin):
    a, stub = admin
    stub.add_response("admin_disable_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.set_enabled("u@x.io", False)
    stub.add_response("admin_enable_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.set_enabled("u@x.io", True)


def test_delete_user(admin):
    a, stub = admin
    stub.add_response("admin_delete_user", {}, {"UserPoolId": POOL, "Username": "u@x.io"})
    a.delete_user("u@x.io")


def test_unknown_user_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_delete_user", service_error_code="UserNotFoundException")
    with pytest.raises(CognitoError) as exc:
        a.delete_user("ghost@x.io")
    assert exc.value.code == "UserNotFoundException"


# ---- 관리자 수 (마지막 관리자 보호의 입력) ----

def test_admin_count_reads_the_admin_group(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@x.io", "a@x.io"), _user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    assert a.admin_count() == 2


def test_admin_count_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@x.io", "a@x.io")], "NextToken": "n1"},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    stub.add_response("list_users_in_group", {"Users": [_user("b@x.io", "b@x.io")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60,
                       "NextToken": "n1"})
    assert a.admin_count() == 2


def test_groups_of_returns_names(admin):
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "admin"}, {"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@x.io"})
    assert a.groups_of("u@x.io") == ["admin", "pm"]
