# backend/aipds/auth/verifier.py
#
# Cognito access token verification. Signature verification is left to PyJWT (we do not write
# cryptographic code ourselves).
#
# Two things confirmed from the documentation determine this file's shape:
#
#   1) An access token identifies the app client through the `client_id` claim, not `aud`.
#      Passing audience= to PyJWT raises MissingRequiredClaimError because there is no aud --
#      so verify_aud is turned off and client_id is compared directly.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html
#   2) An access token carries no email. That is why Principal does not hold one.
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

# If an unauthenticated caller keeps sending JWTs carrying different bogus kids, the kid is
# read straight from the header before signature verification, so every request can turn into
# an outbound HTTPS call to Cognito (serialised behind a single lock at that). Two defences:
# a minimum interval (a cooldown) after a "refetched and still not found", and a negative
# cache of kids that have already failed. The cooldown is measured from the last *failed*
# lookup rather than the last fetch as such -- that is what keeps a legitimate key rotation
# (the first unknown kid) from being obstructed.
_REFETCH_COOLDOWN_SECONDS = 30.0
_MAX_NEGATIVE_CACHE = 256

# The case where the fetch itself fails (a dropped network, a Cognito outage, a response that
# will not parse) is separate from the cooldown above -- _fetch() raises and so never reaches
# the "succeeded but the kid is absent" branch, which is why it needs its own timer. This
# window is set much shorter: an outage is usually transient, and a legitimate user has to be
# able to retry as soon as Cognito recovers. It still cuts per-request retries by an order of
# magnitude under a sustained attack.
_FETCH_FAILURE_COOLDOWN_SECONDS = 5.0


class TokenError(Exception):
    """The token cannot be trusted. The route layer translates this into a 401."""


def _default_http_get(url: str) -> dict:
    # httpx is already a dependency (the backend uses it for the prototype proxy).
    import httpx
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


class JwksCache:
    """Cache the user pool's JWKS as kid -> key.

    A lookup is retried only on a kid miss (to handle key rotation). Refetching on every
    request hammers Cognito and adds latency, while a permanent cache would reject every token
    after a rotation.

    There are three further defences, all against an unauthenticated caller triggering
    refetches with arbitrary kids: a cooldown after a kid miss that actually ended in "not
    found", a negative cache of kids already confirmed absent, and a separate (shorter)
    cooldown for when the fetch attempt itself failed.
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
        # The last time a refetch still did not find the kid (monotonic). None means it has
        # not happened yet -- monotonic rather than the wall clock: turning the system time
        # back must not defeat the cooldown.
        self._last_negative_fetch_at: float | None = None
        # The kids a refetch already failed to find -- so a repeat lookup does not hit
        # Cognito every time. It is cleared wholesale on reaching the cap so it cannot grow
        # without bound (which would itself be an unbounded-growth attack surface).
        self._known_bad_kids: set[str] = set()
        # The last time the fetch attempt itself failed (an exception, or a parse that
        # yielded no keys). Separate from _last_negative_fetch_at above -- in this state the
        # kid was never even adjudicated, so nothing goes into the negative cache.
        self._last_fetch_failure_at: float | None = None

    def clear(self) -> None:
        self._keys = {}
        self._last_negative_fetch_at = None
        self._known_bad_kids.clear()
        self._last_fetch_failure_at = None

    async def _fetch(self) -> None:
        # The synchronous http_get is pushed to a thread so it does not block the event
        # loop.
        try:
            payload = await asyncio.to_thread(self._http_get, self._url)
        except Exception as exc:  # network, HTTP, JSON -- whatever it is
            self._last_fetch_failure_at = self._now()
            raise TokenError(f"jwks fetch failed: {exc}") from exc
        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            self._last_fetch_failure_at = self._now()
            raise TokenError("jwks response contained no usable keys")
        self._keys = keys
        # It succeeded -- any previous outage recovers immediately.
        self._last_fetch_failure_at = None

    async def key_for(self, kid: str) -> dict:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # A double-check: another request may have filled it while we waited for the
            # lock.
            if kid in self._keys:
                return self._keys[kid]

            if kid in self._known_bad_kids:
                # A kid a refetch has already confirmed absent -- do not ask again. Only the
                # kid is logged, at debug (the token body is never logged).
                _log.debug("rejecting known-bad kid without refetch: %r", kid)
                raise TokenError(f"unknown signing key: {kid}")

            if (self._last_negative_fetch_at is not None
                    and self._now() - self._last_negative_fetch_at
                    < _REFETCH_COOLDOWN_SECONDS):
                # No refetch inside the cooldown. _last_negative_fetch_at records only the
                # time of a "refetched and still did not find the kid" -- a normal cache fill
                # or a successful rotation lookup does not touch this clock, so the genuine
                # first unknown kid that follows gets its refetch as usual (the key rotation
                # path is not blocked). Even for a real key rotation, the first lookup after
                # the cooldown expires fetches the new key -- Cognito publishes a new key to
                # the JWKS before it issues tokens signed with it, so this short delay has no
                # effect on legitimate users.
                _log.debug(
                    "suppressing jwks refetch within cooldown for kid: %r", kid)
                raise TokenError(
                    f"unknown signing key (refetch suppressed): {kid}")

            if (self._last_fetch_failure_at is not None
                    and self._now() - self._last_fetch_failure_at
                    < _FETCH_FAILURE_COOLDOWN_SECONDS):
                # The previous fetch attempt itself failed (a dropped network, a Cognito
                # outage, a response that would not parse) -- the kid was never adjudicated,
                # so nothing goes into the negative cache. This window is kept short (5
                # seconds) so a legitimate user recovers as soon as the outage clears. It
                # still cuts per-request retries by an order of magnitude under a sustained
                # flood of unknown kids.
                _log.debug(
                    "suppressing jwks refetch after recent fetch failure "
                    "for kid: %r", kid)
                raise TokenError(
                    f"jwks fetch recently failed, retry suppressed: {kid}")

            await self._fetch()

            key = self._keys.get(kid)
            if key is None:
                # The refetch still did not find it -- start the cooldown from this moment
                # and put the kid itself into the negative cache so it can be rejected
                # immediately without a refetch. This is recorded inside the lock -- doing it
                # outside, after _fetch(), happens to be safe today because there is no await
                # in between, but that rests on a coincidence. If someone later adds one await
                # between releasing the lock and this record (an async log sink, a metrics
                # call), a race appears quietly between two concurrent unknown-kid lookups --
                # so it is prevented structurally.
                self._last_negative_fetch_at = self._now()
                if len(self._known_bad_kids) >= _MAX_NEGATIVE_CACHE:
                    self._known_bad_kids.clear()
                self._known_bad_kids.add(kid)
                raise TokenError(f"unknown signing key: {kid}")

        return key


def _role_from_groups(groups: object) -> Role:
    """Turn group membership into a role.

    Membership in both groups is read as admin -- so the mistake of adding an administrator to
    the pm group does not quietly cut their privileges. Membership in neither means no role, so
    it is refused.
    """
    if groups is None:
        raise TokenError("token has no cognito:groups claim")
    if not isinstance(groups, list):
        # The case where the claim exists but is not an array -- distinguished so that
        # operational log triage does not confuse it with "the claim is absent".
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
    """Verify the signature, issuer, expiry, use and client, and produce a Principal.

    Every failure is a TokenError -- so callers do not branch on the reason.
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
            # An access token has no aud -- client_id is compared directly below.
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
