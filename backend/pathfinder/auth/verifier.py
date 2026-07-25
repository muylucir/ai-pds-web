# backend/pathfinder/auth/verifier.py
#
# Cognito access 토큰 검증. 서명 검증은 PyJWT에 맡긴다(암호 코드를 직접 쓰지 않는다).
#
# 문서로 확인한 두 가지가 이 파일의 형태를 결정한다:
#
#   1) access 토큰은 `aud`가 아니라 `client_id` 클레임으로 앱 클라이언트를 식별한다.
#      PyJWT에 audience=를 넘기면 aud가 없어 MissingRequiredClaimError가 난다 —
#      verify_aud를 끄고 client_id를 직접 비교한다.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html
#   2) access 토큰에는 email이 없다. Principal이 email을 담지 않는 이유다.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html
from __future__ import annotations

import asyncio
import logging
from typing import Callable

import jwt
from jwt.algorithms import RSAAlgorithm

from pathfinder.auth.models import ROLE_ADMIN, ROLE_PM, Principal, Role

_log = logging.getLogger(__name__)

_GROUPS_CLAIM = "cognito:groups"


class TokenError(Exception):
    """토큰이 신뢰할 수 없다. 라우트 계층이 401로 번역한다."""


def _default_http_get(url: str) -> dict:
    # httpx는 이미 의존성이다(백엔드가 프로토타입 프록시에 쓴다).
    import httpx
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


class JwksCache:
    """user pool의 JWKS를 kid→키로 캐시한다.

    조회는 kid 미스에서만 재시도한다(키 로테이션 대응). 매 요청 재조회는 Cognito를
    때리고 지연을 만들며, 반대로 영구 캐시는 로테이션 후 모든 토큰을 거부한다.
    """

    def __init__(self, region: str, user_pool_id: str,
                 http_get: Callable[[str], dict] | None = None) -> None:
        self._url = (f"https://cognito-idp.{region}.amazonaws.com/"
                     f"{user_pool_id}/.well-known/jwks.json")
        self._http_get = http_get or _default_http_get
        self._keys: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._keys = {}

    async def _fetch(self) -> None:
        # 동기 http_get을 스레드로 밀어 이벤트 루프를 막지 않는다.
        try:
            payload = await asyncio.to_thread(self._http_get, self._url)
        except Exception as exc:  # 네트워크·HTTP·JSON 무엇이든
            raise TokenError(f"jwks fetch failed: {exc}") from exc
        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            raise TokenError("jwks response contained no usable keys")
        self._keys = keys

    async def key_for(self, kid: str) -> dict:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # double-check: 락을 기다리는 동안 다른 요청이 이미 채웠을 수 있다.
            if kid in self._keys:
                return self._keys[kid]
            await self._fetch()
        key = self._keys.get(kid)
        if key is None:
            raise TokenError(f"unknown signing key: {kid}")
        return key


def _role_from_groups(groups: object) -> Role:
    """그룹 멤버십을 역할로 바꾼다.

    두 그룹에 모두 속하면 admin으로 해석한다 — 관리자를 pm 그룹에 추가하는 실수가
    권한을 조용히 깎지 않게 한다. 어느 그룹에도 없으면 역할이 없으므로 거부한다.
    """
    if not isinstance(groups, list):
        raise TokenError("token has no cognito:groups claim")
    names = {str(g) for g in groups}
    if ROLE_ADMIN in names:
        return ROLE_ADMIN
    if ROLE_PM in names:
        return ROLE_PM
    raise TokenError(f"user belongs to no known role group: {sorted(names)}")


async def verify_access_token(token: str, *, region: str, user_pool_id: str,
                              client_id: str, jwks: JwksCache) -> Principal:
    """서명·발급자·만료·용도·클라이언트를 검증하고 Principal을 낸다.

    어떤 실패도 TokenError다 — 호출자가 실패 사유별로 분기하지 않도록.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError(f"malformed token header: {exc}") from exc
    kid = header.get("kid")
    if not kid:
        raise TokenError("token header has no kid")

    jwk = await jwks.key_for(kid)
    try:
        public_key = RSAAlgorithm.from_jwk(jwk)
    except Exception as exc:
        raise TokenError(f"unusable signing key: {exc}") from exc

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            # access 토큰에는 aud가 없다 — client_id를 아래에서 직접 비교한다.
            options={"verify_aud": False, "require": ["exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"token rejected: {exc}") from exc

    if claims.get("token_use") != "access":
        raise TokenError(f"expected an access token, got {claims.get('token_use')!r}")
    if claims.get("client_id") != client_id:
        raise TokenError("token was issued to a different app client")

    username = claims.get("username")
    sub = claims.get("sub")
    if not username or not sub:
        raise TokenError("token is missing username/sub")

    return Principal(username=str(username), sub=str(sub),
                     role=_role_from_groups(claims.get(_GROUPS_CLAIM)))
