# backend/aipds/routes/admin_users.py — 사용자 관리 (admin 전용).
#
# 신규 가입은 초대로만 가능하다(풀은 AllowAdminCreateUserOnly). 이 라우터가 그
# 초대 창구다. Cognito 호출 자체는 auth/cognito.py가 담당하고, 여기서는 정책만
# 다룬다:
#
#   1) 마지막 관리자 보호 — 자기 자신 또는 유일한 admin의 강등·비활성·삭제를 막는다.
#      없으면 관리자가 스스로를 잠가내고 복구 경로가 AWS 콘솔밖에 남지 않는다.
#   2) 초대의 부분 실패 롤백 — 반쯤 만들어진 계정(그룹 없음 = 역할 없음)을
#      남기지 않는다. projects.py의 매니페스트 실패 롤백과 같은 규율.
#   3) Cognito 오류 코드 → HTTP 상태코드 번역. 원문 코드는 로그에만 남긴다.
#   4) 프로젝트 귀속 — 사용자를 지우면 그 사람의 프로젝트는 시드 관리자에게 간다. 그래서
#      시드 관리자는 삭제·강등할 수 없다. 관리자는 프로젝트의 소유권을 사용자에게 준다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from starlette.responses import Response

from aipds import error_codes as ec
from aipds import project_access
from aipds.auth.cognito import CognitoError, generate_temp_password
from aipds.auth.deps import require_admin
from aipds.auth.models import Principal, Role

_log = logging.getLogger(__name__)

# 라우터 전체가 admin 전용이다 — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Cognito 오류 코드 → HTTP 상태. 목록에 없는 코드는 502(업스트림 장애)로 본다.
# InvalidPasswordException은 예외다: 502(업스트림 장애)가 아니라 500이다 —
# 우리가 서버에서 생성한 임시 비밀번호가 풀 정책을 만족시키지 못했다는
# 뜻이므로, 원인이 Cognito가 아니라 이쪽(generate_temp_password)에 있다.
_ERROR_STATUS = {
    "UsernameExistsException": 409,
    "AliasExistsException": 409,
    "UserNotFoundException": 404,
    "ResourceNotFoundException": 404,
    "InvalidParameterException": 400,
    "InvalidPasswordException": 500,
    "NotAuthorizedException": 403,
    "TooManyRequestsException": 429,
}

# status → 안정적 에러 코드. 문구는 프론트가 소유한다(error_codes.py 헤더 참조).
_ERROR_DETAIL = {
    409: ec.EMAIL_EXISTS,
    404: ec.USER_NOT_FOUND,
    400: ec.BAD_REQUEST,
    403: ec.FORBIDDEN,
    429: ec.TOO_MANY_REQUESTS,
    500: ec.USER_ADMIN_FAILED,
}


def _http_error(exc: CognitoError) -> HTTPException:
    """Cognito 오류를 사용자에게 보여줄 수 있는 형태로 바꾼다.

    원문 코드는 내부 정보이므로 로그에만 남긴다.
    """
    status = _ERROR_STATUS.get(exc.code, 502)
    _log.warning("cognito call failed (%s) -> %d", exc.code, status)
    return HTTPException(status_code=status,
                         detail=_ERROR_DETAIL.get(status, ec.USER_ADMIN_FAILED))


class InviteBody(BaseModel):
    email: EmailStr
    role: Role


class RoleBody(BaseModel):
    role: Role


class OwnerBody(BaseModel):
    #: 이메일 또는 username.
    owner: str


def _end_sessions(username: str) -> None:
    """이 사용자가 이미 받은 토큰을 거부한다(auth/revocation.py).

    Cognito 호출이 성공한 **뒤에** 부른다 — 실패한 변경으로 사용자를 내쫓지 않는다. 기록
    실패는 변경 자체를 되돌리지 않는다(이미 Cognito에 반영됐다). 남는 것은 그 사용자의 기존
    토큰이 만료까지 통과하는 것이고, 그것이 이 기록이 없던 상태다 — 그래서 로그로 남긴다.
    """
    import aipds.app as app_module
    try:
        app_module.revocations().revoke(username)
    except Exception:
        _log.exception("could not record the session revocation for %s", username)


def _admin():
    import aipds.app as app_module
    return app_module.cognito_admin()


def _guard_seed_admin(username: str) -> None:
    """시드 관리자의 삭제·강등을 막는다. 삭제된 사용자의 프로젝트가 귀속되는 곳이라,
    사라지면 귀속할 곳이 없고 pm이 되면 pm 한 명이 남의 프로젝트를 전부 갖는다.
    비활성화는 막지 않는다 — 관리자는 원래 모든 프로젝트를 보므로 잃는 것이 없다."""
    if project_access.same_user_key(username) == project_access.SEED_ADMIN:
        raise HTTPException(status_code=400, detail=ec.SEED_ADMIN_PROTECTED)


def _guard_privilege_removal(cognito, username: str, me: Principal) -> None:
    """관리자를 잃는 방향의 조작을 막는다.

    두 가지를 막는다:
      - 자기 자신 — 강등·비활성·삭제 어느 쪽이든 스스로를 잠가낸다.
      - 유일한 admin — 그 계정이 사라지면 아무도 사용자 관리를 할 수 없다.

    활성화(권한을 넓히는 방향)에는 적용하지 않는다.

    자기 자신 비교는 casefold()로 한다 — Cognito는 Username을 대소문자
    구분 없이 해석하므로(이 풀은 email을 Username으로 쓴다) 대소문자만 다른
    변형이 같은 계정을 가리킬 수 있다. lower()가 아니라 casefold()를 쓰는
    이유는 이메일이 임의의 유니코드를 포함할 수 있고, casefold()가 대소문자
    구분 없는 비교의 올바른 선택이기 때문이다. 이 정규화는 비교에만 쓴다 —
    Cognito로 넘기는 username은 호출자가 준 그대로다(Cognito가 자체적으로
    해석하고, 저장된 속성은 원래 대소문자를 유지한다).
    """
    if username.casefold() == me.username.casefold():
        raise HTTPException(
            status_code=400,
            detail=ec.SELF_TARGET)
    try:
        groups = cognito.groups_of(username)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if "admin" not in groups:
        return
    try:
        remaining = cognito.admin_count()
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if remaining <= 1:
        raise HTTPException(
            status_code=400,
            detail=ec.LAST_ADMIN)


@router.get("/users")
async def list_users(me: Principal = Depends(require_admin)):
    cognito = _admin()
    try:
        users = cognito.list_users()
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"users": [
        {"username": u.username, "email": u.email, "role": u.role,
         "status": u.status, "enabled": u.enabled, "created_at": u.created_at}
        for u in users]}


@router.post("/users", status_code=201)
async def invite_user(body: InviteBody, me: Principal = Depends(require_admin)):
    """초대: 생성 → 임시 비밀번호 → 그룹.

    임시 비밀번호는 응답에 딱 한 번 실리고 어디에도 저장되지 않는다. 관리자가
    사내 메신저로 전달하고, 사용자는 첫 로그인에서 Hosted UI가 변경을 요구한다.
    """
    cognito = _admin()
    email = str(body.email)
    try:
        username = cognito.create_user(email)
    except CognitoError as exc:
        raise _http_error(exc) from exc

    password = generate_temp_password()
    # 여기부터는 사용자가 이미 존재한다 — 실패하면 방금 만든 것을 되돌린다.
    try:
        cognito.set_temp_password(username, password)
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        _log.exception("invite failed after user creation; rolling back %s", username)
        try:
            cognito.delete_user(username)
        except CognitoError:
            # 롤백까지 실패하면 역할 없는 계정이 남는다. 목록에서 role=null로
            # 보이므로 관리자가 알아볼 수 있다.
            _log.exception("rollback failed; %s may be left without a role", username)
        raise HTTPException(
            status_code=500,
            detail=ec.USER_CREATE_FAILED) from exc

    return {"username": username, "email": email, "role": body.role,
            "temp_password": password}


@router.post("/users/{username}/reset-password")
async def reset_password(username: str, me: Principal = Depends(require_admin)):
    """새 임시 비밀번호를 심고 1회 반환한다.

    이 앱은 메일을 보내지 않으므로 자가 재설정 경로가 없다 — 재설정은 관리자가
    한다(풀의 accountRecovery는 admin_only).
    """
    cognito = _admin()
    password = generate_temp_password()
    try:
        cognito.set_temp_password(username, password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    # 재설정은 계정 탈취를 의심할 때 하는 일이다 — 기존 세션이 남으면 의미가 없다.
    _end_sessions(username)
    return {"username": username, "temp_password": password}


@router.put("/users/{username}/role")
async def change_role(username: str, body: RoleBody,
                      me: Principal = Depends(require_admin)):
    cognito = _admin()
    # admin으로 올리는 것은 관리자를 늘리는 방향이라 언제나 안전하다.
    if body.role != "admin":
        _guard_privilege_removal(cognito, username, me)
        _guard_seed_admin(username)
    try:
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if body.role != "admin":
        # 강등만. 기존 토큰은 옛 그룹(admin)을 담고 있다 — 승격은 기존 토큰이 권한이 더
        # 적을 뿐이라 끊지 않는다(다음 refresh에서 새 그룹을 받는다).
        _end_sessions(username)
    return {"username": username, "role": body.role}


@router.post("/users/{username}/disable", status_code=204)
async def disable_user(username: str, me: Principal = Depends(require_admin)):
    cognito = _admin()
    _guard_privilege_removal(cognito, username, me)
    try:
        cognito.set_enabled(username, False)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    _end_sessions(username)
    return Response(status_code=204)


@router.post("/users/{username}/enable", status_code=204)
async def enable_user(username: str, me: Principal = Depends(require_admin)):
    # 활성화는 권한을 넓히는 방향 — 마지막 관리자 보호와 무관하다.
    cognito = _admin()
    try:
        cognito.set_enabled(username, True)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.delete("/users/{username}", status_code=204)
async def delete_user(username: str, me: Principal = Depends(require_admin)):
    cognito = _admin()
    _guard_privilege_removal(cognito, username, me)
    _guard_seed_admin(username)
    # 프로젝트를 **먼저** 넘긴다. 순서를 거꾸로 하면 넘기기가 실패했을 때 없는 계정이
    # 소유한 프로젝트가 남는다. 이 순서에서 실패하면 계정은 그대로이고, 같은 요청을 다시
    # 보내면 된다(넘기기는 프로젝트마다 멱등이다).
    try:
        await project_access.release_user(username)
    except Exception:
        _log.exception("could not hand over the projects of %s", username)
        raise HTTPException(status_code=500, detail=ec.USER_ADMIN_FAILED)
    try:
        cognito.delete_user(username)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    _end_sessions(username)
    return Response(status_code=204)


@router.put("/projects/{pid}/owner")
async def assign_owner(pid: str, body: OwnerBody, me: Principal = Depends(require_admin)):
    """프로젝트의 소유권을 사용자에게 준다. 이전 소유자는 열람 멤버로 남는다 — 갑자기
    접근을 잃는 것보다 안전하고, 필요하면 관리자가 멤버에서 뺀다. 시드 관리자는 남기지
    않는다(관리자는 원래 전부 본다).

    받을 사람은 실제로 로그인할 수 있는 계정이어야 한다. 멤버 추가(routes/projects)와 달리
    여기서 계정을 확인하는 이유: 이 라우트는 관리자 전용이라 계정 존재가 새어도 상관없고,
    없는 계정에 소유권을 주면 프로젝트가 아무도 못 쓰는 상태가 된다."""
    import aipds.app as app_module
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    try:
        owner = project_access.username_of(body.owner)
    except ValueError:
        raise HTTPException(status_code=400, detail=ec.MEMBER_INVALID)
    if app_module.cognito_config() is not None:
        cognito = _admin()
        try:
            assignable = cognito.is_enabled(owner) and bool(
                {"admin", "pm"} & set(cognito.groups_of(owner)))
        except CognitoError as exc:
            raise _http_error(exc) from exc
        if not assignable:
            raise HTTPException(status_code=400, detail=ec.OWNER_NOT_ASSIGNABLE)
    async with project_access.access_lock(pid):
        if not app_module.registry.is_registered(pid):
            raise HTTPException(status_code=404, detail="unknown project")
        previous = app_module.registry.get_owner(pid) or project_access.SEED_ADMIN
        members = app_module.registry.get_members(pid)
        if previous not in (owner, project_access.SEED_ADMIN):
            members = (*members, previous)
        try:
            await project_access.set_access(pid, owner, members)
        except Exception:
            _log.exception("owner update failed for %s", pid)
            raise HTTPException(status_code=500, detail="project persistence failed")
    return {"project_id": pid, "owner": app_module.registry.get_owner(pid),
            "members": list(app_module.registry.get_members(pid))}
