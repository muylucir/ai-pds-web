# backend/pathfinder/auth/deps.py
#
# FastAPI 의존성 두 개. 라우터 include 시점에 붙여 라우트 본문을 건드리지 않는다.
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from aipds.auth.models import Principal
from aipds.auth.verifier import TokenError, verify_access_token

_log = logging.getLogger(__name__)

# 인증 미설정(로컬/테스트) 상태의 가상 요청자. admin인 이유: 로컬에서 관리
# 페이지까지 그대로 열려야 개발 흐름이 끊기지 않는다.
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
    """admin·pm 모두 통과. 인증이 설정되지 않았으면 전부 통과."""
    # app을 지연 import한다: app.py가 라우터를 include하고 라우터가 이 모듈을
    # import하므로, 모듈 최상단 import는 순환이 된다.
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
        # 사유는 로그에만 — 클라이언트에게 어떤 검증이 실패했는지 알려주지 않는다.
        _log.info("token rejected: %s", exc)
        raise _UNAUTHENTICATED from exc


async def require_admin(
        principal: Principal = Depends(require_user)) -> Principal:
    """admin만 통과. pm은 403 — 인증은 됐고 권한이 없는 상태다(401 아님)."""
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
