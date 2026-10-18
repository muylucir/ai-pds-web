# backend/aipds/auth/verifier.py
#
# Cognito access token verification. Signature verification is left to PyJWT (we do not write crypto code ourselves).
#
# Two things confirmed in the docs determine the shape of this file:
#
#   1) Access tokens identify the app client with the `client_id` claim, not `aud`.
#      Passing audience= to PyJWT raises MissingRequiredClaimError because there is no aud —
#      we turn off verify_aud and compare client_id directly.
#      https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html
#   2) Access tokens contain no email. That is why Principal does not carry email.
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

# If an unauthenticated caller keeps sending JWTs carrying different fake kids, then since
# the kid is read straight from the header before signature verification, every request
# could turn into an outbound HTTPS call to Cognito (serialized behind a single lock). Two
# defenses are in place: a minimum interval (cooldown) after a "still not found after
# refetch", and a negative cache of kids that already failed. The cooldown is based on the
# last "failed lookup", not the last fetch itself — so that normal key rotation (the first unknown kid) is not hindered.
_REFETCH_COOLDOWN_SECONDS = 30.0
_MAX_NEGATIVE_CACHE = 256

# The case where the fetch itself fails (network disconnect, Cognito outage, response parse
# failure) is separate from the cooldown above — _fetch() raises and never reaches the
# "succeeded but no kid" branch above, so a separate timer is needed. This window is kept much
# shorter: outages are usually transient, and once Cognito recovers, legitimate users must be
# able to retry right away. Even so, under a sustained attack it cuts per-request retries by
# orders of magnitude.
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
    """Cache the user pool's JWKS as kid→key.

    Lookups retry only on a kid miss (to handle key rotation). Refetching on every request
    hammers Cognito and adds latency, while a permanent cache rejects every token after rotation.

    There are three more defenses (all of which keep unauthenticated callers from triggering
    refetches with arbitrary kids): a cooldown after a kid miss actually ends in "not found", a
    negative cache of kids already confirmed absent, and a separate (shorter) cooldown for when
    the fetch attempt itself fails.
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
        # Last time (monotonic) a kid was still not found after a refetch. None means that has
        # not happened yet — monotonic is used instead of the wall clock: rolling back the system
        # time must not be able to defeat the cooldown.
        self._last_negative_fetch_at: float | None = None
        # Kids already not found even after a refetch — we do not hammer Cognito with repeated
        # lookups for them. To keep it from growing without bound, it is cleared entirely when it
        # hits the cap (so that it does not itself become an unbounded-growth attack surface).
        self._known_bad_kids: set[str] = set()
        # Last time the fetch attempt itself failed (an exception, or no keys in the parsed result).
        # Separate from _last_negative_fetch_at above — in this state the kid could not even be
        # determined, so nothing is put in the negative cache.
        self._last_fetch_failure_at: float | None = None

    def clear(self) -> None:
        self._keys = {}
        self._last_negative_fetch_at = None
        self._known_bad_kids.clear()
        self._last_fetch_failure_at = None

    async def _fetch(self) -> None:
        # Push the synchronous http_get onto a thread so it does not block the event loop.
        try:
            payload = await asyncio.to_thread(self._http_get, self._url)
        except Exception as exc:  # network, HTTP, or JSON, whatever it is
            self._last_fetch_failure_at = self._now()
            raise TokenError(f"jwks fetch failed: {exc}") from exc
        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            self._last_fetch_failure_at = self._now()
            raise TokenError("jwks response contained no usable keys")
        self._keys = keys
        # It succeeded — even if there was an earlier outage, recovery is immediate.
        self._last_fetch_failure_at = None

    async def key_for(self, kid: str) -> dict:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # double-check: while waiting for the lock, another request may already have filled it.
            if kid in self._keys:
                return self._keys[kid]

            if kid in self._known_bad_kids:
                # A kid already confirmed absent by a refetch — do not ask again.
                # Log only the kid at debug (never log the token body).
                _log.debug("rejecting known-bad kid without refetch: %r", kid)
                raise TokenError(f"unknown signing key: {kid}")

            if (self._last_negative_fetch_at is not None
                    and self._now() - self._last_negative_fetch_at
                    < _REFETCH_COOLDOWN_SECONDS):
                # Within the cooldown, do not refetch. _last_negative_fetch_at records only
                # the times when "the kid was still not found after a refetch" — normal cache
                # fills and successful rotation lookups do not touch this clock, so the genuinely
                # first unknown kid that comes after them gets a refetch as usual (the key
                # rotation path is not blocked). Even for a real key rotation, the first lookup
                # after the cooldown expires fetches the new key — Cognito publishes a new key
                # to the JWKS before issuing tokens with the rotated key, so this short delay
                # does not affect legitimate users.
                _log.debug(
                    "suppressing jwks refetch within cooldown for kid: %r", kid)
                raise TokenError(
                    f"unknown signing key (refetch suppressed): {kid}")

            if (self._last_fetch_failure_at is not None
                    and self._now() - self._last_fetch_failure_at
                    < _FETCH_FAILURE_COOLDOWN_SECONDS):
                # The previous fetch attempt itself failed (network disconnect, Cognito outage,
                # response parse failure, etc.) — the kid could not be determined, so nothing
                # goes into the negative cache. This window is kept short (5 seconds) so that
                # legitimate users recover right away once the outage clears. Even so, during a
                # sustained flood of unknown kids it cuts per-request retries by orders of magnitude.
                _log.debug(
                    "suppressing jwks refetch after recent fetch failure "
                    "for kid: %r", kid)
                raise TokenError(
                    f"jwks fetch recently failed, retry suppressed: {kid}")

            await self._fetch()

            key = self._keys.get(kid)
            if key is None:
                # Still not found after a refetch — start the cooldown from this moment, and put
                # the kid itself into the negative cache so it can be rejected immediately without a
                # refetch. This bookkeeping happens inside the lock — doing it outside the lock
                # after _fetch() is safe today because there is no await in between, but that
                # relies on luck. If someone later adds an await between releasing the lock and
                # this bookkeeping (an async log sink, a metrics call, etc.), a race condition
                # silently appears between two concurrent unknown-kid lookups —
                # so it is prevented structurally.
                self._last_negative_fetch_at = self._now()
                if len(self._known_bad_kids) >= _MAX_NEGATIVE_CACHE:
                    self._known_bad_kids.clear()
                self._known_bad_kids.add(kid)
                raise TokenError(f"unknown signing key: {kid}")

        return key


def _role_from_groups(groups: object) -> Role:
    """Convert group membership into a role.

    Membership in both groups is interpreted as admin — so that the mistake of adding an
    admin to the pm group does not silently strip privileges. If in neither group, there is no role, so it is rejected.
    """
    if groups is None:
        raise TokenError("token has no cognito:groups claim")
    if not isinstance(groups, list):
        # The claim exists but is not an array — distinguished so that operational log
        # classification does not confuse it with "no claim".
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
    """Verify signature, issuer, expiry, token use, and client, and produce a Principal.

    Every failure is a TokenError — so callers do not branch on failure reasons.
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
            # Access tokens have no aud — client_id is compared directly below.
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

    iat = claims.get("iat")
    return Principal(username=str(username), sub=str(sub),
                     role=_role_from_groups(claims.get(_GROUPS_CLAIM)),
                     issued_at=float(iat) if isinstance(iat, (int, float)) else None)
