# backend/aipds/routes/admin_users.py -- user management (admin only).
#
# New accounts exist only by invitation (the pool is AllowAdminCreateUserOnly), and
# this router is that invitation desk. The Cognito calls themselves live in
# auth/cognito.py; only policy lives here:
#
#   1) Last-admin protection -- refuse to demote, disable or delete yourself or the
#      only remaining admin. Without it an admin can lock themselves out, leaving the
#      AWS console as the only recovery path.
#   2) Rollback on a partially failed invitation -- never leave a half-built account
#      (no group = no role) behind. The same discipline as projects.py's rollback on
#      a failed manifest write.
#   3) Translate Cognito error codes into HTTP status codes. The original code goes
#      to the log only.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.auth.cognito import CognitoError, generate_temp_password
from aipds.auth.deps import require_admin
from aipds.auth.models import Principal, Role

_log = logging.getLogger(__name__)

# The whole router is admin-only, which removes any chance of forgetting it on an
# individual route.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Cognito error code -> HTTP status. Anything not listed is treated as 502 (an
# upstream fault). InvalidPasswordException is the exception: it is a 500 rather than
# a 502, because it means the temporary password WE generated server-side failed to
# satisfy the pool policy -- so the cause is on this side
# (generate_temp_password), not Cognito's.
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

# status -> a stable error code. The wording is owned by the frontend (see
# error_codes.py's header).
_ERROR_DETAIL = {
    409: ec.EMAIL_EXISTS,
    404: ec.USER_NOT_FOUND,
    400: ec.BAD_REQUEST,
    403: ec.FORBIDDEN,
    429: ec.TOO_MANY_REQUESTS,
    500: ec.USER_ADMIN_FAILED,
}


def _http_error(exc: CognitoError) -> HTTPException:
    """Turn a Cognito error into something showable to the user.

    The original code is internal information and goes to the log only.
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


def _admin():
    import aipds.app as app_module
    return app_module.cognito_admin()


def _guard_privilege_removal(cognito, username: str, me: Principal) -> None:
    """Refuse operations that move in the direction of losing admins.

    Two cases are blocked:
      - Yourself -- demoting, disabling or deleting all lock you out.
      - The only admin -- if that account goes, nobody can manage users.

    It does not apply to enabling (which widens access).

    The self-comparison uses casefold(): Cognito interprets Username
    case-insensitively (this pool uses email as the Username), so a variant differing
    only in case can name the same account. casefold() rather than lower() because an
    email can contain arbitrary Unicode and casefold() is the correct choice for
    case-insensitive comparison. This normalisation is used for the comparison only --
    the username passed to Cognito is exactly what the caller gave (Cognito
    interprets it itself, and the stored attribute keeps its original case).
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
    """Invitation: create -> temporary password -> group.

    The temporary password rides in the response exactly once and is stored nowhere.
    The admin passes it on over an internal messenger, and the Hosted UI requires a
    change at first login.
    """
    cognito = _admin()
    email = str(body.email)
    try:
        username = cognito.create_user(email)
    except CognitoError as exc:
        raise _http_error(exc) from exc

    password = generate_temp_password()
    # From here on the user already exists -- on failure, undo what we just made.
    try:
        cognito.set_temp_password(username, password)
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        _log.exception("invite failed after user creation; rolling back %s", username)
        try:
            cognito.delete_user(username)
        except CognitoError:
            # If the rollback also fails, an account with no role is left behind.
            # It shows as role=null in the list, so an admin can spot it.
            _log.exception("rollback failed; %s may be left without a role", username)
        raise HTTPException(
            status_code=500,
            detail=ec.USER_CREATE_FAILED) from exc

    return {"username": username, "email": email, "role": body.role,
            "temp_password": password}


@router.post("/users/{username}/reset-password")
async def reset_password(username: str, me: Principal = Depends(require_admin)):
    """Plant a new temporary password and return it once.

    This app sends no mail, so there is no self-service reset path -- an admin does
    the reset (the pool's accountRecovery is admin_only).
    """
    cognito = _admin()
    password = generate_temp_password()
    try:
        cognito.set_temp_password(username, password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"username": username, "temp_password": password}


@router.put("/users/{username}/role")
async def change_role(username: str, body: RoleBody,
                      me: Principal = Depends(require_admin)):
    cognito = _admin()
    # Promoting to admin adds an admin, so it is always safe.
    if body.role != "admin":
        _guard_privilege_removal(cognito, username, me)
    try:
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return {"username": username, "role": body.role}


@router.post("/users/{username}/disable", status_code=204)
async def disable_user(username: str, me: Principal = Depends(require_admin)):
    cognito = _admin()
    _guard_privilege_removal(cognito, username, me)
    try:
        cognito.set_enabled(username, False)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.post("/users/{username}/enable", status_code=204)
async def enable_user(username: str, me: Principal = Depends(require_admin)):
    # Enabling widens access, so last-admin protection does not apply.
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
    try:
        cognito.delete_user(username)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)
