# backend/tests/test_auth_cognito.py
#
# Tests the wrapper without real Cognito, using botocore Stubber. Stubber returns a
# response only when the parameters match the expectation exactly, so "what is called
# and how" is asserted directly.
from __future__ import annotations

import re

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from aipds.auth.cognito import (CognitoAdmin, CognitoError, ManagedUser,
                                     change_own_password,
                                     generate_temp_password)

POOL = "ap-northeast-2_TEST123"

# The `nosec` markers below are all the same fact: botocore insists on
# credentials to sign a request that Stubber intercepts before it reaches the
# wire, and the password-shaped strings in the stubbed parameter dicts are the
# values under test. Nothing here is a credential.


@pytest.fixture()
def admin():
    client = boto3.client("cognito-idp", region_name="ap-northeast-2",
                          aws_access_key_id="x",
                          aws_secret_access_key="y")  # nosec B106
    stub = Stubber(client)
    stub.activate()
    yield CognitoAdmin(client, POOL), stub
    # A response left in the queue means a call the test expected never actually
    # happened — Stubber verifies only calls whose parameters matched and does not check
    # for calls that never occurred, so without this check even a method that has
    # degraded into a no-op would get a green test.
    stub.assert_no_pending_responses()
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


# ---- password generation ----

def test_generated_password_satisfies_the_pool_policy():
    # The policy is 8+ characters with upper/lower/digit/symbol. If the generator misses
    # the policy, InvalidPasswordException fires after the user has already been
    # created, so it must always comply.
    for _ in range(50):
        pw = generate_temp_password()
        assert len(pw) == 16
        assert re.search(r"[a-z]", pw), pw
        assert re.search(r"[A-Z]", pw), pw
        assert re.search(r"[0-9]", pw), pw
        assert re.search(r"[!@#$%^&*_\-+=?]", pw), pw


def test_generated_passwords_are_not_repeated():
    assert len({generate_temp_password() for _ in range(50)}) == 50


# ---- listing ----

def test_list_users_maps_attributes_and_groups(admin):
    a, stub = admin
    stub.add_response(
        "list_users",
        {"Users": [_user("admin@aipds.local", "admin@aipds.local"),
                   _user("pm@aipds.local", "pm@aipds.local")]},
        {"UserPoolId": POOL, "Limit": 60},
    )
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "admin@aipds.local"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "pm@aipds.local"})
    users = a.list_users()
    assert [u.email for u in users] == ["admin@aipds.local", "pm@aipds.local"]
    assert [u.role for u in users] == ["admin", "pm"]
    assert users[0].status == "CONFIRMED" and users[0].enabled is True
    assert users[0].created_at.startswith("2026-07-25")


def test_list_users_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users",
                      {"Users": [_user("a@example.com", "a@example.com")], "PaginationToken": "t1"},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "a@example.com"})
    stub.add_response("list_users", {"Users": [_user("b@example.com", "b@example.com")]},
                      {"UserPoolId": POOL, "Limit": 60, "PaginationToken": "t1"})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "b@example.com"})
    assert [u.email for u in a.list_users()] == ["a@example.com", "b@example.com"]


def test_user_with_no_group_has_role_none(admin):
    # A half-created account that failed before group assignment must be recognizable on screen.
    a, stub = admin
    stub.add_response("list_users", {"Users": [_user("x@example.com", "x@example.com")]},
                      {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": []},
                      {"UserPoolId": POOL, "Username": "x@example.com"})
    assert a.list_users()[0].role is None


def test_missing_email_attribute_falls_back_to_username(admin):
    a, stub = admin
    raw = _user("legacy-user", "unused")
    raw["Attributes"] = []
    stub.add_response("list_users", {"Users": [raw]}, {"UserPoolId": POOL, "Limit": 60})
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "legacy-user"})
    assert a.list_users()[0].email == "legacy-user"


def test_list_users_degrades_a_row_when_its_group_lookup_fails(admin):
    # The whole list must not collapse because of one user who was deleted after the
    # snapshot or got a 429 — downgrade only that row to role=None and keep showing the rest.
    a, stub = admin
    stub.add_response(
        "list_users",
        {"Users": [_user("a@example.com", "a@example.com"), _user("b@example.com", "b@example.com")]},
        {"UserPoolId": POOL, "Limit": 60},
    )
    stub.add_client_error(
        "admin_list_groups_for_user", service_error_code="UserNotFoundException",
        expected_params={"UserPoolId": POOL, "Username": "a@example.com"},
    )
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "b@example.com"})
    users = a.list_users()
    assert [u.email for u in users] == ["a@example.com", "b@example.com"]
    assert users[0].role is None
    assert users[1].role == "pm"


# ---- creation ----

def test_create_user_suppresses_email_and_marks_it_verified(admin):
    a, stub = admin
    # email_verified=true is not optional — it is a requirement for alias (email) sign-in.
    # Username is the local part, not the email: this pool has AliasAttributes=[email],
    # and in that case Cognito rejects an email-format Username ("Username cannot be of
    # email format, since user pool is configured for email alias").
    stub.add_response(
        "admin_create_user",
        {"User": _user("new", "new@example.com", status="FORCE_CHANGE_PASSWORD")},
        {"UserPoolId": POOL, "Username": "new", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "new@example.com"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("new@example.com") == "new"


def test_create_user_never_sends_an_email_shaped_username(admin):
    """Regression guard for an observed deployment failure. The only condition Cognito
    rejects is 'a Username containing @', so assert that invariant directly rather than
    the local-part rule."""
    a, stub = admin
    stub.add_response(
        "admin_create_user",
        {"User": _user("kim.lee", "kim.lee@corp.example.com")},
        {"UserPoolId": POOL, "Username": "kim.lee", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "kim.lee@corp.example.com"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    username = a.create_user("kim.lee@corp.example.com")
    assert "@" not in username


def test_create_user_lowercases_and_trims_the_local_part(admin):
    # The pool has signInCaseSensitive=false, but the Username string itself is stored as-is.
    # Mixed case makes the /admin/users list and self-comparisons messy.
    a, stub = admin
    stub.add_response(
        "admin_create_user",
        {"User": _user("mixed", "Mixed@EXAMPLE.COM")},
        # The email attribute is trimmed too — if whitespace remains, alias sign-in
        # requires that whitespace as well and the user cannot log in.
        {"UserPoolId": POOL, "Username": "mixed", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "Mixed@EXAMPLE.COM"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("  Mixed@EXAMPLE.COM  ") == "mixed"


def test_create_user_strips_characters_cognito_rejects(admin):
    # Cognito Username accepts only a restricted character set. If the local part has other
    # characters (e.g., a tagged address "a+tag@example.com"), passing it as-is raises InvalidParameterException.
    a, stub = admin
    stub.add_response(
        "admin_create_user",
        {"User": _user("a-tag", "a+tag@example.com")},
        {"UserPoolId": POOL, "Username": "a-tag", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "a+tag@example.com"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("a+tag@example.com") == "a-tag"


def test_create_user_returns_the_username_cognito_reports(admin):
    # If the Username in the response differs from what we sent (pool setting change, etc.),
    # trust the response — subsequent set_temp_password/set_group must be called with that value.
    a, stub = admin
    stub.add_response(
        "admin_create_user",
        {"User": _user("server-side-id", "x@example.org")},
        {"UserPoolId": POOL, "Username": "x", "MessageAction": "SUPPRESS",
         "UserAttributes": [{"Name": "email", "Value": "x@example.org"},
                            {"Name": "email_verified", "Value": "true"}]},
    )
    assert a.create_user("x@example.org") == "server-side-id"


def test_duplicate_email_raises_cognito_error_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="UsernameExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("dup@example.com")
    assert exc.value.code == "UsernameExistsException"


def test_alias_exists_is_also_surfaced_by_code(admin):
    # The email is already used as another account's alias.
    a, stub = admin
    stub.add_client_error("admin_create_user", service_error_code="AliasExistsException")
    with pytest.raises(CognitoError) as exc:
        a.create_user("alias@example.com")
    assert exc.value.code == "AliasExistsException"


# ---- passwords ----

def test_set_temp_password_is_not_permanent(admin):
    # Permanent=False makes the user change it themselves on first login (invite flow).
    a, stub = admin
    stub.add_response("admin_set_user_password", {},
                      {"UserPoolId": POOL, "Username": "u@example.com",
                       "Password": "Tmp!23456789abcd",  # nosec B105
                       "Permanent": False})
    a.set_temp_password("u@example.com", "Tmp!23456789abcd")


def test_invalid_password_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_set_user_password",
                         service_error_code="InvalidPasswordException")
    with pytest.raises(CognitoError) as exc:
        a.set_temp_password("u@example.com", "weak")
    assert exc.value.code == "InvalidPasswordException"


# ---- groups (roles) ----

def test_set_group_removes_existing_roles_before_adding(admin):
    # Changing a role is a "replace", not an "add". Skipping the removal leaves the user in
    # two groups and nullifies a demotion (because the verifier gives admin precedence).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "admin"}]},
                      {"UserPoolId": POOL, "Username": "u@example.com"})
    stub.add_response("admin_remove_user_from_group", {},
                      {"UserPoolId": POOL, "Username": "u@example.com", "GroupName": "admin"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@example.com", "GroupName": "pm"})
    a.set_group("u@example.com", "pm")


def test_set_group_leaves_unrelated_groups_alone(admin):
    # Groups other than admin/pm are not our concern — leave them untouched.
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "some-other-group"}]},
                      {"UserPoolId": POOL, "Username": "u@example.com"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@example.com", "GroupName": "admin"})
    a.set_group("u@example.com", "admin")


def test_set_group_is_a_noop_add_when_already_correct(admin):
    # If already in the correct group, do not remove; only add (idempotent).
    a, stub = admin
    stub.add_response("admin_list_groups_for_user", {"Groups": [{"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@example.com"})
    stub.add_response("admin_add_user_to_group", {},
                      {"UserPoolId": POOL, "Username": "u@example.com", "GroupName": "pm"})
    a.set_group("u@example.com", "pm")


# ---- enable/disable/delete ----

def test_disable_and_enable(admin):
    a, stub = admin
    stub.add_response("admin_disable_user", {}, {"UserPoolId": POOL, "Username": "u@example.com"})
    a.set_enabled("u@example.com", False)
    stub.add_response("admin_enable_user", {}, {"UserPoolId": POOL, "Username": "u@example.com"})
    a.set_enabled("u@example.com", True)


def test_delete_user(admin):
    a, stub = admin
    stub.add_response("admin_delete_user", {}, {"UserPoolId": POOL, "Username": "u@example.com"})
    a.delete_user("u@example.com")


def test_unknown_user_raises_with_code(admin):
    a, stub = admin
    stub.add_client_error("admin_delete_user", service_error_code="UserNotFoundException")
    with pytest.raises(CognitoError) as exc:
        a.delete_user("ghost@example.com")
    assert exc.value.code == "UserNotFoundException"


# ---- admin count (input to last-admin protection) ----

def test_admin_count_reads_the_admin_group(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@example.com", "a@example.com"), _user("b@example.com", "b@example.com")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    assert a.admin_count() == 2


def test_admin_count_follows_pagination(admin):
    a, stub = admin
    stub.add_response("list_users_in_group",
                      {"Users": [_user("a@example.com", "a@example.com")], "NextToken": "n1"},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60})
    stub.add_response("list_users_in_group", {"Users": [_user("b@example.com", "b@example.com")]},
                      {"UserPoolId": POOL, "GroupName": "admin", "Limit": 60,
                       "NextToken": "n1"})
    assert a.admin_count() == 2


def test_groups_of_returns_names(admin):
    a, stub = admin
    stub.add_response("admin_list_groups_for_user",
                      {"Groups": [{"GroupName": "admin"}, {"GroupName": "pm"}]},
                      {"UserPoolId": POOL, "Username": "u@example.com"})
    assert a.groups_of("u@example.com") == ["admin", "pm"]


# ---- transport-layer failures (the request never even arrived, rather than Cognito rejecting it) ----

def test_transport_failure_is_wrapped_as_cognito_error(admin):
    # BotoCoreError such as EndpointConnectionError is not a ClientError, so it used to pass
    # straight through _call's except clause — leaking out as an exception type the route
    # has never seen, it becomes an unhandled 500. Stubber can only produce ClientError,
    # so the client method is swapped out directly.
    a, stub = admin

    def _boom(**kwargs):
        raise EndpointConnectionError(endpoint_url="https://example.com")

    a._c.admin_delete_user = _boom
    with pytest.raises(CognitoError) as exc:
        a.delete_user("u@example.com")
    assert exc.value.code == "EndpointConnectionError"


# ---- self-service: changing one's own password ----
#
# The routes/account.py tests use a fake client, so they only check "what kwargs we
# pass". Only Stubber can verify that those kwargs match the real Cognito API shape —
# get one parameter name wrong and the fake passes while the real deployment dies.

@pytest.fixture()
def idp():
    """A raw client not bound to a pool. Self-service calls do not use UserPoolId."""
    client = boto3.client("cognito-idp", region_name="ap-northeast-2",
                          aws_access_key_id="x",
                          aws_secret_access_key="y")  # nosec B106
    stub = Stubber(client)
    stub.activate()
    yield client, stub
    stub.assert_no_pending_responses()
    stub.deactivate()


def test_change_own_password_sends_the_token_and_both_passwords(idp):
    client, stub = idp
    # Stubber returns a response only on an exact parameter match — this dict is the
    # assertion that it is "everything we send to Cognito". If UserPoolId or Username
    # slips in, it gets caught here (that API does not accept them).
    stub.add_response("change_password", {}, {
        "AccessToken": "the.access.token",
        "PreviousPassword": "OldPass1!",
        "ProposedPassword": "NewPass2@",
    })
    assert change_own_password(
        client, "the.access.token", "OldPass1!", "NewPass2@") is None


def test_a_wrong_current_password_keeps_cognitos_code_and_message(idp):
    # The route picks the status from code and tells two meanings apart by message
    # (_not_authorized in routes/account.py) — both must survive on the way up.
    client, stub = idp
    stub.add_client_error(
        "change_password", service_error_code="NotAuthorizedException",
        service_message="Incorrect username or password.")
    with pytest.raises(CognitoError) as exc:
        change_own_password(client, "t", "wrong", "NewPass2@")
    assert exc.value.code == "NotAuthorizedException"
    assert "Incorrect" in exc.value.message


def test_a_missing_scope_is_distinguishable_by_message_alone(idp):
    # A missing scope arrives with the same code. What this test protects is "the message
    # is preserved"; without it, the route cannot show its re-login guidance.
    client, stub = idp
    stub.add_client_error(
        "change_password", service_error_code="NotAuthorizedException",
        service_message="Access Token does not have required scopes")
    with pytest.raises(CognitoError) as exc:
        change_own_password(client, "t", "OldPass1!", "NewPass2@")
    assert exc.value.code == "NotAuthorizedException"
    assert "scope" in exc.value.message.lower()


def test_self_service_transport_failure_is_also_wrapped(idp):
    # Must use the same translation as _call — if only one side misses BotoCoreError, the
    # route gets a 500 with an exception type it has never seen.
    client, _ = idp

    def _boom(**kwargs):
        raise EndpointConnectionError(endpoint_url="https://example.com")

    client.change_password = _boom
    with pytest.raises(CognitoError) as exc:
        change_own_password(client, "t", "OldPass1!", "NewPass2@")
    assert exc.value.code == "EndpointConnectionError"


# ---- enabled status (used by ownership assignment) ----

def test_is_enabled_reads_the_account_through_list_users(admin):
    a, stub = admin
    stub.add_response("list_users", {"Users": [_user("pm", "pm@aipds.local", enabled=False)]},
                      {"UserPoolId": POOL, "Filter": 'username = "pm"', "Limit": 1})
    assert a.is_enabled("pm") is False


def test_is_enabled_reports_a_missing_account_as_user_not_found(admin):
    a, stub = admin
    stub.add_response("list_users", {"Users": []},
                      {"UserPoolId": POOL, "Filter": 'username = "ghost"', "Limit": 1})
    with pytest.raises(CognitoError) as e:
        a.is_enabled("ghost")
    assert e.value.code == "UserNotFoundException"


def test_is_enabled_escapes_the_filter_value(admin):
    a, stub = admin
    stub.add_response("list_users", {"Users": []},
                      {"UserPoolId": POOL, "Filter": 'username = "a\\"b"', "Limit": 1})
    with pytest.raises(CognitoError):
        a.is_enabled('a"b')


def test_every_cognito_call_is_allowed_by_the_instance_role():
    # The instance role allows Cognito APIs one by one (not cognito-idp:*). If an API called
    # here is not on that list, just that feature gets AccessDenied → 502, and the cause is
    # not visible on screen (observed: ownership assignment returned 502 via AdminGetUser).
    # Extending the list requires redeploying HostingStack, and that deployment replaces the
    # instance, so solving it with an already-allowed API comes first.
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    src = (root / "backend/aipds/auth/cognito.py").read_text(encoding="utf-8")
    called = set(re.findall(r'_call\(\s*"(\w+)"', src))
    called |= set(re.findall(r'\bclient\.(\w+)\(', src)) & {"change_password"}
    stack = (root / "infra/lib/aipds-hosting-stack.ts").read_text(encoding="utf-8")
    allowed = set(re.findall(r"'cognito-idp:(\w+)'", stack))

    def pascal(name: str) -> str:
        return "".join(part.capitalize() for part in name.split("_"))

    # ChangePassword is a self-service API signed with the user's own access token, so it needs no role.
    missing = sorted(pascal(c) for c in called if c != "change_password"
                     and pascal(c) not in allowed)
    assert called, "cognito.py에서 호출을 하나도 찾지 못했다 — 정규식을 확인할 것"
    assert missing == [], f"인스턴스 롤에 없는 Cognito API: {missing}"
