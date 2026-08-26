# backend/tests/test_routes_account.py
#
# 자기 비밀번호 변경(POST /me/password)의 라우트 계층. 검증하는 것은 세 가지다:
#
#   1) 요청의 access 토큰이 그대로 Cognito로 간다 — 이 경로는 Admin* 호출이
#      아니라 **토큰 자체로 인가**되는 셀프서비스 API이므로, 다른 사용자의
#      비밀번호를 바꿀 방법이 구조적으로 없다. 토큰이 신원이다.
#   2) Cognito 오류 코드 → HTTP 상태 + 안정적 에러 코드. 여기의 매핑은
#      admin_users.py와 **다르다**: 값을 고른 주체가 서버가 아니라 사용자다.
#   3) 인증이 미설정인 로컬 개발에서 조용히 통과하지 않는다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds import error_codes as ec
from aipds.auth.cognito import CognitoError
from aipds.auth.deps import require_user
from aipds.auth.models import Principal

ME = Principal(username="pm", sub="s-pm", role="pm")
# The `nosec` markers in this file all say the same thing: these are fixture
# strings for a route that forwards passwords to a stubbed Cognito client. The
# token is three words with dots in it, and the passwords exist to be asserted
# on. None of them opens anything.
TOKEN = "header.payload.signature"  # nosec B105
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"current_password": "OldPass1!", "new_password": "NewPass2@"}  # nosec B105


class FakeIdp:
    """cognito-idp 클라이언트의 대역. change_password 호출만 받는다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: CognitoError | None = None

    def change_password(self, **params):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


@pytest.fixture()
def idp(monkeypatch):
    fake = FakeIdp()
    # 이 라우트는 "Cognito가 설정된 배포"에서만 동작한다 — 미설정이면 503으로
    # 명시 거부한다(맨 아래 테스트). 그래서 설정된 상태를 흉내내야 한다.
    monkeypatch.setattr(
        app_module, "cognito_config",
        lambda: {"region": "ap-northeast-2",
                 "user_pool_id": "ap-northeast-2_TEST123",
                 "client_id": "testclientid"})
    monkeypatch.setattr(app_module, "cognito_idp_client", lambda: fake)
    app_module.app.dependency_overrides[require_user] = lambda: ME
    yield fake
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _fail_with(idp: FakeIdp, code: str) -> None:
    idp.error = CognitoError(code, f"stubbed {code}")


# ---- 성공 경로 ----

def test_the_request_access_token_is_what_authorizes_the_change(idp, client):
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 204, r.text
    assert len(idp.calls) == 1
    call = idp.calls[0]
    # 토큰은 요청에서 온 그대로여야 한다. 여기에 서버가 가진 다른 자격증명이
    # 끼어들면 "자기 것만 바꾼다"는 보장이 사라진다.
    assert call["AccessToken"] == TOKEN
    assert call["PreviousPassword"] == BODY["current_password"]
    assert call["ProposedPassword"] == BODY["new_password"]
    # 사용자 이름/풀 id는 넘기지 않는다 — 이 API는 그것을 받지 않는다.
    assert "Username" not in call
    assert "UserPoolId" not in call


def test_nothing_of_the_password_comes_back_in_the_response(idp, client):
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 204
    assert r.content == b""


# ---- 오류 번역 ----

def test_a_wrong_current_password_is_400_with_its_own_code(idp, client):
    # Cognito는 현재 비밀번호가 틀리면 NotAuthorizedException을 낸다.
    # admin_users.py는 그 코드를 403/forbidden으로 번역하는데, 여기서 그렇게
    # 하면 화면이 "권한이 없습니다"라고 말한다 — 사용자는 자기 계정에 대한
    # 권한을 갖고 있고, 틀린 것은 입력한 값이다.
    _fail_with(idp, "NotAuthorizedException")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"] == ec.WRONG_PASSWORD


def test_a_policy_violating_new_password_is_400_not_500(idp, client):
    # admin_users.py는 InvalidPasswordException을 500으로 본다 — 그쪽 값은
    # 서버가 생성했으므로 정책을 못 맞추면 우리 결함이다. 여기서는 값을
    # 사용자가 골랐으므로 400이고, 화면이 정책을 다시 안내할 수 있어야 한다.
    _fail_with(idp, "InvalidPasswordException")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"] == ec.PASSWORD_POLICY


def test_too_many_attempts_is_429(idp, client):
    # Cognito가 시도 횟수를 제한한다. 502로 뭉개면 화면이 "서버 오류"라고
    # 말하는데, 사용자가 해야 할 일은 잠시 기다리는 것이다.
    _fail_with(idp, "LimitExceededException")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 429
    assert r.json()["detail"] == ec.TOO_MANY_REQUESTS


def test_throttling_is_also_429(idp, client):
    _fail_with(idp, "TooManyRequestsException")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 429


def test_a_token_without_the_self_service_scope_asks_for_reauth(idp, client):
    """스코프 누락은 "현재 비밀번호가 틀렸다"로 보여서는 안 된다.

    Cognito는 두 경우 모두 `NotAuthorizedException`을 낸다 — 코드만 보면
    구분할 수 없고 메시지만 다르다. 구분이 필요한 이유는 사용자가 해야 할 일이
    정반대라는 것이다: 스코프가 없는 토큰을 쥔 사용자는 무엇을 입력하든 "현재
    비밀번호가 틀립니다"를 받게 되고, 실제로 해야 할 일은 다시 로그인하는 것이다.
    """
    idp.error = CognitoError(
        "NotAuthorizedException",
        "Access Token does not have required scopes")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"] == ec.REAUTH_REQUIRED


def test_an_unknown_cognito_code_is_502(idp, client):
    _fail_with(idp, "InternalErrorException")
    r = client.post("/me/password", json=BODY, headers=AUTH)
    assert r.status_code == 502
    assert r.json()["detail"] == ec.PASSWORD_CHANGE_FAILED


# ---- 입력 검증 ----

def test_a_missing_new_password_never_reaches_cognito(idp, client):
    r = client.post("/me/password",
                    json={"current_password": "OldPass1!"},  # nosec B105
                    headers=AUTH)
    assert r.status_code == 422
    assert idp.calls == []


def test_an_empty_new_password_never_reaches_cognito(idp, client):
    r = client.post("/me/password",
                    json={"current_password": "OldPass1!",  # nosec B105
                          "new_password": ""},
                    headers=AUTH)
    assert r.status_code == 422
    assert idp.calls == []


# ---- 토큰이 없는 경우 ----

def test_without_a_bearer_token_the_call_is_401_not_a_crash(idp, client):
    # require_user가 오버라이드되어 통과하더라도, 이 라우트는 **원문 토큰**을
    # 따로 필요로 한다. 그것이 없으면 Cognito에 보낼 것이 없다 — 여기서
    # 500으로 터지면 원인이 "설정 문제"처럼 보인다.
    r = client.post("/me/password", json=BODY)
    assert r.status_code == 401
    assert idp.calls == []


def test_local_dev_without_cognito_refuses_explicitly(monkeypatch, client):
    # 인증 미설정 로컬에서는 require_user가 가상 admin으로 전부 통과시킨다.
    # 그 상태에서 이 라우트가 조용히 200을 내면 "비밀번호를 바꿨다"는 거짓
    # 확인을 사용자에게 준다.
    monkeypatch.setattr(app_module, "cognito_config", lambda: None)
    app_module.app.dependency_overrides[require_user] = lambda: ME
    try:
        r = client.post("/me/password", json=BODY, headers=AUTH)
        assert r.status_code == 503
        assert r.json()["detail"] == ec.AUTH_NOT_CONFIGURED
    finally:
        app_module.app.dependency_overrides.clear()
