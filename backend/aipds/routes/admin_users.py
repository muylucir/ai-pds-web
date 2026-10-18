# backend/aipds/routes/admin_users.py — user management (admin only).
#
# New sign-ups are possible only by invitation (the pool is AllowAdminCreateUserOnly). This
# router is that invitation window. The Cognito calls themselves are handled by
# auth/cognito.py; this file deals only with policy:
#
#   1) Last-admin protection — blocks demoting, deactivating, or deleting yourself or the only
#      admin. Without it, an admin could lock themselves out, leaving the AWS console as the
#      only recovery path.
#   2) Rollback of partial invitation failures — leaves no half-created account (no group = no
#      role). Same discipline as the manifest failure rollback in projects.py.
#   3) Translating Cognito error codes → HTTP status codes. The original code is kept only in
#      the logs.
#   4) Project attribution — when a user is deleted, their projects go to the seed admin. So
#      the seed admin cannot be deleted or demoted. Admins give project ownership to users.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pydantic.networks import validate_email
from pydantic_core import PydanticCustomError
from starlette.responses import Response

from aipds import error_codes as ec
from aipds import project_access
from aipds.auth.cognito import CognitoError, generate_temp_password
from aipds.auth.deps import require_admin
from aipds.auth.models import Principal, Role

_log = logging.getLogger(__name__)

# The whole router is admin only — removes any chance of forgetting to attach it per route.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# Cognito error code → HTTP status. Codes not in the list are treated as 502 (upstream failure).
# InvalidPasswordException is the exception: it is 500, not 502 (upstream failure) —
# it means the temporary password we generated on the server did not satisfy the pool policy,
# so the cause lies on our side (generate_temp_password), not in Cognito.
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

# status → stable error code. The frontend owns the wording (see the error_codes.py header).
_ERROR_DETAIL = {
    409: ec.EMAIL_EXISTS,
    404: ec.USER_NOT_FOUND,
    400: ec.BAD_REQUEST,
    403: ec.FORBIDDEN,
    429: ec.TOO_MANY_REQUESTS,
    500: ec.USER_ADMIN_FAILED,
}


def _http_error(exc: CognitoError) -> HTTPException:
    """Turn a Cognito error into a form that can be shown to the user.

    The original code is internal information, so it is kept only in the logs.
    """
    status = _ERROR_STATUS.get(exc.code, 502)
    _log.warning("cognito call failed (%s) -> %d", exc.code, status)
    return HTTPException(status_code=status,
                         detail=_ERROR_DETAIL.get(status, ec.USER_ADMIN_FAILED))


class InviteBody(BaseModel):
    #: Format checking is done by the route (_checked_email). With EmailStr as the type, a bad
    #: email becomes FastAPI's default 422, whose detail is an array of error objects rather
    #: than a string, so the screen cannot find the code.
    email: str
    role: Role


class RoleBody(BaseModel):
    role: Role


class OwnerBody(BaseModel):
    #: Email or username.
    owner: str


def _end_sessions(username: str) -> None:
    """Reject the tokens this user has already received (auth/revocation.py).

    Called **after** the Cognito call succeeds — a failed change does not kick the user out. A
    recording failure does not roll back the change itself (it is already applied in Cognito).
    What remains is that the user's existing tokens pass until they expire, which is the state
    before this record existed — so it is logged.
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
    """Block deleting or demoting the seed admin. It is where deleted users' projects are
    attributed, so if it disappeared there would be nowhere to attribute them, and if it became
    a pm, one pm would own everyone else's projects. Deactivation is not blocked — admins see
    every project anyway, so nothing is lost."""
    if project_access.same_user_key(username) == project_access.SEED_ADMIN:
        raise HTTPException(status_code=400, detail=ec.SEED_ADMIN_PROTECTED)


def _guard_privilege_removal(cognito, username: str, me: Principal) -> None:
    """Block operations that would lose an admin.

    It blocks two things:
      - Yourself — demoting, deactivating, or deleting would all lock yourself out.
      - The only admin — if that account disappears, nobody can manage users.

    It does not apply to activation (the direction that widens permissions).

    The self comparison uses casefold() — Cognito interprets Username case-insensitively
    (this pool uses email as the Username), so variants that differ only in case can point to
    the same account. The reason to use casefold() rather than lower() is that emails can
    contain arbitrary Unicode, and casefold() is the correct choice for case-insensitive
    comparison. This normalization is used only for comparison — the username passed to
    Cognito is exactly what the caller gave (Cognito interprets it on its own, and the stored
    attribute keeps the original case).
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


def _checked_email(value: str) -> str:
    """Check with the same rules as EmailStr (pydantic.networks.validate_email), and on failure
    return a 400 with a stable code. Why not write separate rules: the accepted addresses must not
    change."""
    try:
        _, email = validate_email(value.strip())
    except PydanticCustomError:
        raise HTTPException(status_code=400, detail=ec.EMAIL_INVALID)
    return email


@router.post("/users", status_code=201)
async def invite_user(body: InviteBody, me: Principal = Depends(require_admin)):
    """Invitation: create → temporary password → group.

    The temporary password is included in the response exactly once and stored nowhere. The
    admin passes it on via the internal messenger, and on first login the Hosted UI requires
    the user to change it.
    """
    email = _checked_email(body.email)
    cognito = _admin()
    try:
        username = cognito.create_user(email)
    except CognitoError as exc:
        raise _http_error(exc) from exc

    password = generate_temp_password()
    # From here on the user already exists — on failure, roll back what was just created.
    try:
        cognito.set_temp_password(username, password)
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        _log.exception("invite failed after user creation; rolling back %s", username)
        try:
            cognito.delete_user(username)
        except CognitoError:
            # If even the rollback fails, an account without a role remains. It shows as role=null
            # in the list, so an admin can spot it.
            _log.exception("rollback failed; %s may be left without a role", username)
        raise HTTPException(
            status_code=500,
            detail=ec.USER_CREATE_FAILED) from exc

    return {"username": username, "email": email, "role": body.role,
            "temp_password": password}


@router.post("/users/{username}/reset-password")
async def reset_password(username: str, me: Principal = Depends(require_admin)):
    """Plant a new temporary password and return it once.

    This app sends no email, so there is no self-service reset path — resets are done by an
    admin (the pool's accountRecovery is admin_only).
    """
    cognito = _admin()
    password = generate_temp_password()
    try:
        cognito.set_temp_password(username, password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    # A reset is what you do when you suspect account takeover — it is meaningless if existing
    # sessions remain.
    _end_sessions(username)
    return {"username": username, "temp_password": password}


@router.put("/users/{username}/role")
async def change_role(username: str, body: RoleBody,
                      me: Principal = Depends(require_admin)):
    cognito = _admin()
    # Promoting to admin increases the number of admins, so it is always safe.
    if body.role != "admin":
        _guard_privilege_removal(cognito, username, me)
        _guard_seed_admin(username)
    try:
        cognito.set_group(username, body.role)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    if body.role != "admin":
        # Demotion only. Existing tokens carry the old group (admin) — a promotion is not cut
        # off, since the existing tokens merely have fewer permissions (they get the new group
        # on the next refresh).
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
    # Activation widens permissions — it has nothing to do with last-admin protection.
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
    # Transfer the projects **first**. In the reverse order, a failed transfer would leave
    # projects owned by a nonexistent account. If it fails in this order, the account stays as
    # is, and the same request can just be sent again (the transfer is idempotent per
    # project).
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
    """Give project ownership to a user. The previous owner stays as a read-only member —
    safer than suddenly losing access, and an admin can remove them from the members if
    needed. The seed admin is not kept (admins see everything anyway).

    The recipient must be an account that can actually log in. Why the account is checked here,
    unlike member addition (routes/projects): this route is admin only, so leaking an account's
    existence does not matter, and giving ownership to a nonexistent account would leave the
    project in a state nobody can use."""
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
