# backend/aipds/auth/verifier.py
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
import time
from typing import Callable

import jwt
from jwt.algorithms import RSAAlgorithm

from aipds.auth.models import ROLE_ADMIN, ROLE_PM, Principal, Role

_log = logging.getLogger(__name__)

_GROUPS_CLAIM = "cognito:groups"

# 인증되지 않은 호출자가 서로 다른 가짜 kid를 실어 나르는 JWT를 계속 보내면,
# kid는 서명 검증 전에 헤더에서 그대로 읽히므로 매 요청이 Cognito로의 외부
# HTTPS 호출(그리고 단일 락 뒤에서의 직렬화)로 번질 수 있다. 두 방어를 둔다:
# "재조회해도 못 찾음"이 있은 뒤 최소 간격(쿨다운)과, 이미 실패한 kid의 네거티브
# 캐시. 쿨다운은 마지막 fetch 자체가 아니라 마지막 "실패한 조회"를 기준으로
# 삼는다 — 그래야 정상적인 키 로테이션(첫 미확인 kid)은 방해받지 않는다.
_REFETCH_COOLDOWN_SECONDS = 30.0
_MAX_NEGATIVE_CACHE = 256

# fetch 자체가 실패하는 경우(네트워크 단절, Cognito 장애, 응답 파싱 실패)는
# 위 쿨다운과 별개다 — _fetch()가 예외를 던져 위의 "성공했지만 kid가 없음"
# 분기에 도달하지 못하므로 별도 타이머가 필요하다. 이 창은 훨씬 짧게 잡는다:
# 장애는 보통 일시적이고, Cognito가 회복되면 정상 사용자가 곧바로 다시 시도할
# 수 있어야 한다. 그래도 지속 공격 상황에서 요청당 재시도를 자릿수 단위로
# 줄여준다.
_FETCH_FAILURE_COOLDOWN_SECONDS = 5.0


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

    세 가지 방어가 더 있다(모두 미인증 호출자가 임의의 kid로 재조회를 유발하는
    것을 막는다): kid 미스가 실제로 "못 찾음"으로 끝난 뒤의 쿨다운, 이미 없다고
    확인된 kid의 네거티브 캐시, 그리고 fetch 시도 자체가 실패했을 때의 별도
    (더 짧은) 쿨다운.
    """

    def __init__(self, region: str, user_pool_id: str,
                 http_get: Callable[[str], dict] | None = None,
                 now: Callable[[], float] | None = None) -> None:
        self._url = (f"https://cognito-idp.{region}.amazonaws.com/"
                     f"{user_pool_id}/.well-known/jwks.json")
        self._http_get = http_get or _default_http_get
        self._now = now or time.monotonic
        self._keys: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        # 재조회했는데도 kid를 못 찾은 마지막 시각(monotonic). None이면 아직
        # 그런 일이 없었다는 뜻 — 벽시계가 아니라 monotonic을 쓴다: 시스템
        # 시각을 되돌려도 쿨다운을 무력화할 수 없어야 한다.
        self._last_negative_fetch_at: float | None = None
        # 이미 재조회해도 못 찾은 kid들 — 반복 조회로 매번 Cognito를 때리지
        # 않는다. 무한히 자라지 않도록 cap에 닿으면 통째로 비운다(그 자체가
        # 무한 성장 공격 표면이 되지 않도록).
        self._known_bad_kids: set[str] = set()
        # fetch 시도 자체가 실패한(예외 또는 파싱 결과 키 없음) 마지막 시각.
        # 위 _last_negative_fetch_at과 별개다 — 이건 kid를 판별하지도 못한
        # 상태이므로 네거티브 캐시에는 아무것도 넣지 않는다.
        self._last_fetch_failure_at: float | None = None

    def clear(self) -> None:
        self._keys = {}
        self._last_negative_fetch_at = None
        self._known_bad_kids.clear()
        self._last_fetch_failure_at = None

    async def _fetch(self) -> None:
        # 동기 http_get을 스레드로 밀어 이벤트 루프를 막지 않는다.
        try:
            payload = await asyncio.to_thread(self._http_get, self._url)
        except Exception as exc:  # 네트워크·HTTP·JSON 무엇이든
            self._last_fetch_failure_at = self._now()
            raise TokenError(f"jwks fetch failed: {exc}") from exc
        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            self._last_fetch_failure_at = self._now()
            raise TokenError("jwks response contained no usable keys")
        self._keys = keys
        # 성공했다 — 이전 장애가 있었더라도 즉시 회복된다.
        self._last_fetch_failure_at = None

    async def key_for(self, kid: str) -> dict:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # double-check: 락을 기다리는 동안 다른 요청이 이미 채웠을 수 있다.
            if kid in self._keys:
                return self._keys[kid]

            if kid in self._known_bad_kids:
                # 이미 재조회해서 없다고 확인된 kid — 다시 물어보지 않는다.
                # kid만 debug로 남긴다(토큰 본문은 절대 로그에 남기지 않는다).
                _log.debug("rejecting known-bad kid without refetch: %r", kid)
                raise TokenError(f"unknown signing key: {kid}")

            if (self._last_negative_fetch_at is not None
                    and self._now() - self._last_negative_fetch_at
                    < _REFETCH_COOLDOWN_SECONDS):
                # 쿨다운 안에서는 재조회하지 않는다. _last_negative_fetch_at은
                # "재조회했는데도 kid를 못 찾은" 시각만 기록한다 — 정상적인
                # 캐시 채움이나 성공적인 로테이션 조회는 이 시계를 건드리지
                # 않으므로, 그 다음에 오는 진짜 첫 미확인 kid는 정상적으로
                # 재조회를 받는다(키 로테이션 경로가 막히지 않는다). 진짜 키
                # 로테이션이라도 쿨다운 만료 후 첫 조회가 새 키를 받아온다 —
                # Cognito는 로테이션된 키로 토큰을 발급하기 전에 JWKS에 새
                # 키를 먼저 게시하므로, 이 짧은 지연은 정상 사용자에게 영향이
                # 없다.
                _log.debug(
                    "suppressing jwks refetch within cooldown for kid: %r", kid)
                raise TokenError(
                    f"unknown signing key (refetch suppressed): {kid}")

            if (self._last_fetch_failure_at is not None
                    and self._now() - self._last_fetch_failure_at
                    < _FETCH_FAILURE_COOLDOWN_SECONDS):
                # 직전 fetch 시도 자체가 실패했다(네트워크 단절, Cognito 장애,
                # 응답 파싱 실패 등) — kid를 판별하지 못했으므로 네거티브
                # 캐시에는 아무것도 넣지 않는다. 이 창은 짧게(5초) 두어, 장애가
                # 걷히면 정상 사용자가 곧바로 회복되게 한다. 그래도 지속되는
                # 미확인-kid 폭주에서는 요청당 재시도를 자릿수 단위로 줄인다.
                _log.debug(
                    "suppressing jwks refetch after recent fetch failure "
                    "for kid: %r", kid)
                raise TokenError(
                    f"jwks fetch recently failed, retry suppressed: {kid}")

            await self._fetch()

            key = self._keys.get(kid)
            if key is None:
                # 재조회해도 못 찾았다 — 이 시각부터 쿨다운을 시작하고, kid
                # 자체도 네거티브 캐시에 넣어 재조회 없이 즉시 거부할 수 있게
                # 한다. 이 기록은 락 안에서 한다 — _fetch() 이후 락 밖에서
                # 하면 지금은 그 사이에 await가 없어 안전하지만, 그건 우연에
                # 기댄 것이다. 나중에 누군가 락 해제와 이 기록 사이에 await를
                # 하나 추가하면(비동기 로그 sink, 메트릭 호출 등) 동시에 들어온
                # 두 개의 미확인-kid 조회 사이에 조용히 경쟁 상태가 생긴다 —
                # 구조적으로 막아 둔다.
                self._last_negative_fetch_at = self._now()
                if len(self._known_bad_kids) >= _MAX_NEGATIVE_CACHE:
                    self._known_bad_kids.clear()
                self._known_bad_kids.add(kid)
                raise TokenError(f"unknown signing key: {kid}")

        return key


def _role_from_groups(groups: object) -> Role:
    """그룹 멤버십을 역할로 바꾼다.

    두 그룹에 모두 속하면 admin으로 해석한다 — 관리자를 pm 그룹에 추가하는 실수가
    권한을 조용히 깎지 않게 한다. 어느 그룹에도 없으면 역할이 없으므로 거부한다.
    """
    if groups is None:
        raise TokenError("token has no cognito:groups claim")
    if not isinstance(groups, list):
        # 클레임이 존재하지만 배열이 아닌 경우 — 운영 로그 분류가 "클레임 없음"과
        # 헛갈리지 않도록 구분한다.
        raise TokenError(
            f"cognito:groups claim is not a list: {type(groups).__name__}")
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
