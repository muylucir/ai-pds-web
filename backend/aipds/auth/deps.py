# backend/aipds/auth/deps.py
#
# Two FastAPI dependencies. They are attached at router include time so route bodies stay untouched.
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from aipds.auth.models import Principal
from aipds.auth.verifier import TokenError, verify_access_token

_log = logging.getLogger(__name__)

# The virtual requester when auth is not configured (local/test). Why admin: locally, even
# the admin pages must open as is so the development flow is not interrupted.
LOCAL_PRINCIPAL = Principal(username="local-dev", sub="local-dev", role="admin")

_UNAUTHENTICATED = HTTPException(
    status_code=401, detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"})


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _UNAUTHENTICATED
    return token.strip()


async def require_user(request: Request) -> Principal:
    """Both admin and pm pass. If auth is not configured, everyone passes."""
    # Import app lazily: app.py includes the routers and the routers import this module,
    # so a top-level module import would be circular.
    import aipds.app as app_module

    cfg = app_module.cognito_config()
    if cfg is None:
        request.state.principal = LOCAL_PRINCIPAL
        return LOCAL_PRINCIPAL

    token = _bearer_token(request)
    try:
        principal = await verify_access_token(
            token, region=cfg["region"], user_pool_id=cfg["user_pool_id"],
            client_id=cfg["client_id"], jwks=app_module.jwks_cache())
    except TokenError as exc:
        # The reason goes only to the log — the client is not told which check failed.
        _log.info("token rejected: %s", exc)
        raise _UNAUTHENTICATED from exc
    # A token issued before an admin cut off this user (auth/revocation.py). It is the same
    # 401 — the proxy attempts a refresh; a demoted user moves to the new group, and a
    # disabled or deleted user goes to the login screen.
    if app_module.revocations().is_revoked(principal.username, principal.issued_at):
        _log.info("token rejected: issued before %s was revoked", principal.username)
        raise _UNAUTHENTICATED
    # Record the requester in the access log (aipds/access_log.py).
    request.state.principal = principal
    return principal


async def access_token(request: Request) -> str:
    """The request's raw access token.

    `require_user` verifies the token, keeps only the `Principal`, and discards the token
    itself — because routes only need the identity. There is one exception: the Cognito
    self-service API (`ChangePassword`) uses **the token itself as the authorization
    means**, so it needs the raw token (routes/account.py).

    It is a separate dependency to make that exception visible — the presence of
    `access_token` in a route signature signals "this route passes the user's token
    upstream as is". If there is no token, `_bearer_token` returns 401 (the same response
    as `require_user`).
    """
    return _bearer_token(request)


async def require_admin(
        principal: Principal = Depends(require_user)) -> Principal:
    """Only admin passes. pm gets 403 — authenticated but not authorized (not 401)."""
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
