# backend/aipds/auth/deps.py
#
# Two FastAPI dependencies. Attached at router include time so no route body is touched.
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from aipds.auth.models import Principal
from aipds.auth.verifier import TokenError, verify_access_token

_log = logging.getLogger(__name__)

# The virtual requester for the unconfigured (local, tests) state. Why admin: the admin pages
# have to be open locally too, or the development flow breaks.
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
    """Both admin and pm pass. With authentication unconfigured, everything passes."""
    # app is imported lazily: app.py includes the routers and the routers import this
    # module, so a module-level import would be a cycle.
    import aipds.app as app_module

    cfg = app_module.cognito_config()
    if cfg is None:
        return LOCAL_PRINCIPAL

    token = _bearer_token(request)
    try:
        return await verify_access_token(
            token, region=cfg["region"], user_pool_id=cfg["user_pool_id"],
            client_id=cfg["client_id"], jwks=app_module.jwks_cache())
    except TokenError as exc:
        # The reason goes only to the log -- the client is not told which check failed.
        _log.info("token rejected: %s", exc)
        raise _UNAUTHENTICATED from exc


async def require_admin(
        principal: Principal = Depends(require_user)) -> Principal:
    """Only admin passes. pm gets a 403 -- authenticated but unauthorised, not a 401."""
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
