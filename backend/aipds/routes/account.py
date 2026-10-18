# backend/aipds/routes/account.py — operations on one's own account (not admin-only).
#
# It is split from admin_users.py because the authorization model differs. That one handles **other people's**
# accounts via the Admin* APIs, so it needs the admin role and the backend's IAM permissions. This one handles
# only **one's own** account via Cognito self-service APIs, and the means of authorization is the access token
# carried in the request itself — so there are no new IAM permissions, and there is structurally no path
# to change someone else's password.
#
# The error translation table differs from admin_users.py for the same reason: the one who chose the value is
# the user, not the server, so a policy violation is not our defect (500) but an input error to return to
# the user (400).
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

# Cognito error code → (HTTP status, error code). Codes not in the list are 502.
#
# `NotAuthorizedException` being absent here is not a mistake — that code means two situations
# and they are distinguished only by message (see _not_authorized).
_ERRORS: dict[str, tuple[int, str]] = {
    # The new password did not meet the pool policy. admin_users.py treats the same code as 500,
    # because there the server generated the value, so failing the policy is our defect.
    "InvalidPasswordException": (400, ec.PASSWORD_POLICY),
    "InvalidParameterException": (400, ec.BAD_REQUEST),
    # An admin reset the password and the account went back to the temporary-password state.
    # It cannot be changed with the current session's token — the user must log in again with the temporary password.
    "PasswordResetRequiredException": (403, ec.REAUTH_REQUIRED),
    "UserNotFoundException": (404, ec.USER_NOT_FOUND),
    # Cognito limits the number of attempts. Flattening it to 502 makes the screen say "server error",
    # while what the user needs to do is wait a moment.
    "LimitExceededException": (429, ec.TOO_MANY_REQUESTS),
    "TooManyRequestsException": (429, ec.TOO_MANY_REQUESTS),
}


def _not_authorized(message: str) -> tuple[int, str]:
    """Distinguishes the two meanings of `NotAuthorizedException`.

    Cognito uses **the same code** for "the current password is wrong" and "the token lacks the
    self-service scope". The distinction is needed because what the user has to do is the exact
    opposite. Showing "the current password is wrong" to a user holding a token without the scope
    produces a screen that fails no matter what is entered — what they actually need is to log in again.
    Such tokens are issued when the app client's allowed scopes drift (HostingStack's
    `UpdateUserPoolClient` resend has PUT semantics and overwrites the whole list —
    infra/test/hosting-stack.assert.ts guards against that drift).

    Depending on the message string is not robust. That is why the default is "the password is
    wrong": if Cognito changes the wording, a missing scope shows up as a password error, and that
    misdiagnosis only loses the re-login hint. The other way around, a common typo would become
    "please log in again" every time and send the user into a logout loop.
    """
    if "scope" in message.lower():
        return 403, ec.REAUTH_REQUIRED
    return 400, ec.WRONG_PASSWORD


def _http_error(exc: CognitoError) -> HTTPException:
    if exc.code == "NotAuthorizedException":
        status, detail = _not_authorized(exc.message)
    else:
        status, detail = _ERRORS.get(exc.code, (502, ec.PASSWORD_CHANGE_FAILED))
    # The raw code is internal information — it goes only to the log (same discipline as admin_users.py).
    _log.warning("change_password rejected (%s) -> %d", exc.code, status)
    return HTTPException(status_code=status, detail=detail)


class PasswordBody(BaseModel):
    # Only a minimum length is enforced. Policy checks are done by Cognito; making a second copy here
    # is bound to drift, and the drifted side lies to the user. `min_length=1` is not
    # policy but the discipline of "do not send empty requests upstream".
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


@router.post("/password", status_code=204)
async def change_password(body: PasswordBody,
                          me: Principal = Depends(require_user),
                          token: str = Depends(access_token)) -> None:
    """Changes one's own password.

    `token` is passed straight to Cognito — the point is that this route does not judge identity
    on its own. `me` is used only for logging.
    """
    import aipds.app as app_module

    if app_module.cognito_config() is None:
        # Auth unset (local development). require_user lets it through as a virtual admin, so
        # without blocking here it would do nothing and return 204, giving the user a false
        # confirmation that "the password was changed".
        raise HTTPException(status_code=503, detail=ec.AUTH_NOT_CONFIGURED)

    try:
        change_own_password(
            app_module.cognito_idp_client(), token,
            body.current_password, body.new_password)
    except CognitoError as exc:
        raise _http_error(exc) from exc
    # The token is not invalidated: `ChangePassword` does not end the session, so the user
    # stays logged in. Adding a global sign-out (GlobalSignOut) would bounce the screen to login right after
    # changing the password, and the user would have no way to know why.
    _log.info("password changed for %s", me.username)
