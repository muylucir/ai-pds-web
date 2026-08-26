# backend/aipds/routes/account.py — 자기 계정 조작 (admin 전용이 아니다).
#
# admin_users.py와 나누는 이유는 인가 모델이 다르기 때문이다. 그쪽은 Admin* API로
# **남의** 계정을 다루므로 admin 역할과 백엔드의 IAM 권한이 필요하다. 여기는
# Cognito 셀프서비스 API로 **자기** 계정만 다루고, 인가 수단은 요청에 실려 온
# access 토큰 그 자체다 — 그래서 새 IAM 권한이 없고, 남의 비밀번호를 바꿀 경로도
# 구조적으로 없다.
#
# 오류 번역표가 admin_users.py와 다른 것도 그 차이에서 나온다: 값을 고른 주체가
# 서버가 아니라 사용자이므로, 정책 위반은 우리 결함(500)이 아니라 사용자에게
# 돌려줄 입력 오류(400)다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aipds import error_codes as ec
from aipds.auth.cognito import CognitoError, change_own_password
from aipds.auth.deps import access_token, require_user
from aipds.auth.models import Principal

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/me")

# Cognito 오류 코드 → (HTTP 상태, 에러 코드). 목록에 없는 코드는 502다.
#
# `NotAuthorizedException`이 여기 없는 것은 실수가 아니다 — 그 코드는 두 상황을
# 뜻하고 메시지로만 갈린다(_not_authorized 참조).
_ERRORS: dict[str, tuple[int, str]] = {
    # 새 비밀번호가 풀 정책을 못 맞췄다. admin_users.py는 같은 코드를 500으로
    # 보는데, 그쪽 값은 서버가 생성했으므로 정책 미달이면 우리 결함이다.
    "InvalidPasswordException": (400, ec.PASSWORD_POLICY),
    "InvalidParameterException": (400, ec.BAD_REQUEST),
    # 관리자가 비밀번호를 재설정해 계정이 임시 비밀번호 상태로 돌아갔다.
    # 지금 세션의 토큰으로는 바꿀 수 없다 — 임시 비밀번호로 다시 로그인해야 한다.
    "PasswordResetRequiredException": (403, ec.REAUTH_REQUIRED),
    "UserNotFoundException": (404, ec.USER_NOT_FOUND),
    # Cognito가 시도 횟수를 제한한다. 502로 뭉개면 화면이 "서버 오류"라고 말하는데,
    # 사용자가 해야 할 일은 잠시 기다리는 것이다.
    "LimitExceededException": (429, ec.TOO_MANY_REQUESTS),
    "TooManyRequestsException": (429, ec.TOO_MANY_REQUESTS),
}


def _not_authorized(message: str) -> tuple[int, str]:
    """`NotAuthorizedException`의 두 뜻을 가른다.

    Cognito는 "현재 비밀번호가 틀렸다"와 "토큰에 셀프서비스 스코프가 없다"에
    **같은 코드**를 쓴다. 구분이 필요한 이유는 사용자가 해야 할 일이 정반대라는
    것이다. 스코프가 없는 토큰을 쥔 사용자에게 "현재 비밀번호가 틀립니다"를
    보여주면 무엇을 입력해도 실패하는 화면이 된다 — 실제로 필요한 것은 재로그인이다.
    그런 토큰은 앱 클라이언트의 허용 스코프가 흔들리면 발급된다(HostingStack의
    `UpdateUserPoolClient` 재전송이 PUT 시맨틱이라 목록을 통째로 덮어쓴다 —
    infra/test/hosting-stack.assert.ts가 그 드리프트를 막는다).

    메시지 문자열에 의존하는 것은 견고하지 않다. 그래서 기본값을 "비밀번호가
    틀렸다"로 두었다: Cognito가 문구를 바꾸면 스코프 누락이 비밀번호 오류로
    보이는데, 그 오진은 재로그인 안내가 사라지는 것뿐이다. 반대로 두면 흔한
    오타가 매번 "다시 로그인하세요"가 되어 사용자를 로그아웃 루프로 보낸다.
    """
    if "scope" in message.lower():
        return 403, ec.REAUTH_REQUIRED
    return 400, ec.WRONG_PASSWORD


def _http_error(exc: CognitoError) -> HTTPException:
    if exc.code == "NotAuthorizedException":
        status, detail = _not_authorized(exc.message)
    else:
        status, detail = _ERRORS.get(exc.code, (502, ec.PASSWORD_CHANGE_FAILED))
    # 원문 코드는 내부 정보다 — 로그에만 남긴다(admin_users.py와 같은 규율).
    _log.warning("change_password rejected (%s) -> %d", exc.code, status)
    return HTTPException(status_code=status, detail=detail)


class PasswordBody(BaseModel):
    # 길이 하한만 둔다. 정책 검사는 Cognito가 하고, 여기서 두 벌로 만들면
    # 반드시 어긋나며 어긋난 쪽이 사용자에게 거짓말을 한다. `min_length=1`은
    # 정책이 아니라 "빈 요청을 업스트림까지 보내지 않는다"는 규율이다.
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


@router.post("/password", status_code=204)
async def change_password(body: PasswordBody,
                          me: Principal = Depends(require_user),
                          token: str = Depends(access_token)) -> None:
    """자기 비밀번호를 바꾼다.

    `token`을 그대로 Cognito에 넘긴다 — 이 라우트가 신원을 따로 판단하지 않는
    것이 요점이다. `me`는 로그에만 쓴다.
    """
    import aipds.app as app_module

    if app_module.cognito_config() is None:
        # 인증 미설정(로컬 개발). require_user가 가상 admin으로 통과시키므로
        # 여기서 막지 않으면 아무 일도 하지 않고 204를 내어, 사용자에게
        # "비밀번호를 바꿨다"는 거짓 확인을 준다.
        raise HTTPException(status_code=503, detail=ec.AUTH_NOT_CONFIGURED)

    try:
        change_own_password(
            app_module.cognito_idp_client(), token,
            body.current_password, body.new_password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    # 토큰을 무효화하지 않는다: `ChangePassword`는 세션을 끊지 않으므로 사용자는
    # 로그인 상태를 유지한다. 전역 로그아웃(GlobalSignOut)을 붙이면 비밀번호를
    # 바꾼 직후 화면이 로그인으로 튕기는데, 그 이유를 사용자가 알 수 없다.
    _log.info("password changed for %s", me.username)
