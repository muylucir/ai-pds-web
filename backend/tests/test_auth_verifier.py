# backend/tests/test_auth_verifier.py
#
# Tests the verifier without real Cognito: tokens are signed with a test RSA key, and that key's
# public part is injected in JWKS form. Two facts confirmed from the docs are the core of this test —
# an access token identifies the client by client_id (not aud), and it has no email claim.
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aipds.auth.verifier import JwksCache, TokenError, verify_access_token

REGION = "ap-northeast-2"
POOL = "ap-northeast-2_TEST123"
CLIENT_ID = "client-abc"
ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KID = "test-key-1"
#: `token_use` values. Constants rather than literals at each site because
#: bandit reads any string sitting under a `token*` name -- keyword argument or
#: dict key -- as a hardcoded credential (B105/B106).
USE_ACCESS = "access"
USE_ID = "id"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict:
    """Returns the public part of the test key in Cognito JWKS form."""
    from jwt.algorithms import RSAAlgorithm
    jwk = RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _token(**overrides) -> str:
    """The default payload shape of a Cognito access token (confirmed from the docs).

    The absence of email is intentional — access tokens have no email claim.
    """
    now = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "cognito:groups": ["admin"],
        "iss": ISS,
        "client_id": CLIENT_ID,
        "token_use": USE_ACCESS,
        "scope": "openid email profile",
        "auth_time": now,
        "iat": now,
        "exp": now + 3600,
        "username": "admin@aipds.local",
    }
    claims.update(overrides)
    return jwt.encode(claims, _private_key, algorithm="RS256",
                      headers={"kid": KID})


def _cache(jwks: dict | None = None, calls: list | None = None,
           now=None) -> JwksCache:
    payload = jwks if jwks is not None else _jwks()

    def http_get(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        return payload

    return JwksCache(region=REGION, user_pool_id=POOL, http_get=http_get,
                     now=now)


def _bogus_token(kid: str) -> str:
    """A token whose signature is valid with our test key but whose header kid is not in the JWKS.

    The kid is read from the header before signature verification, so the payload contents are
    irrelevant to these tests — they only exercise the path that is rejected on a kid miss every time.
    """
    return jwt.encode({"sub": "x"}, _private_key, algorithm="RS256",
                      headers={"kid": kid})


async def _verify(token: str, *, cache: JwksCache | None = None,
                  client_id: str = CLIENT_ID):
    return await verify_access_token(
        token, region=REGION, user_pool_id=POOL, client_id=client_id,
        jwks=cache or _cache())


async def test_valid_token_yields_principal_with_role_from_groups():
    principal = await _verify(_token())
    assert principal.username == "admin@aipds.local"
    assert principal.sub == "11111111-2222-3333-4444-555555555555"
    assert principal.role == "admin"


async def test_the_principal_carries_the_issue_time():
    """Session revocation (auth/revocation.py) uses `iat` to pick out existing tokens."""
    issued = int(time.time()) - 60
    principal = await _verify(_token(iat=issued))
    assert principal.issued_at == issued


async def test_pm_group_yields_pm_role():
    principal = await _verify(_token(**{"cognito:groups": ["pm"]}))
    assert principal.role == "pm"


async def test_admin_wins_when_user_is_in_both_groups():
    # Membership in both groups resolves to the broader permission (admin) — that way the mistake of
    # adding an admin to the pm group does not silently cut their permissions.
    principal = await _verify(_token(**{"cognito:groups": ["pm", "admin"]}))
    assert principal.role == "admin"


async def test_expired_token_is_rejected():
    now = int(time.time())
    with pytest.raises(TokenError):
        await _verify(_token(exp=now - 10, iat=now - 3600))


async def test_wrong_client_id_is_rejected():
    # An access token identifies the app client by client_id, not aud.
    with pytest.raises(TokenError):
        await _verify(_token(client_id="someone-elses-client"))


async def test_wrong_issuer_is_rejected():
    with pytest.raises(TokenError):
        await _verify(_token(iss="https://cognito-idp.us-east-1.amazonaws.com/other"))


async def test_id_token_is_rejected():
    # Guards against the mix-up of putting an id token where an access token belongs.
    with pytest.raises(TokenError):
        await _verify(_token(token_use=USE_ID))


async def test_token_without_known_group_is_rejected():
    # Groups are the only source of the role. Not in any group means no role, so it is rejected.
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": []}))
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": ["some-other-group"]}))


async def test_missing_groups_claim_is_rejected():
    # The case where the claim itself is missing (distinct from an empty array). _token() always includes groups,
    # so only here is the token signed directly. The message is checked too — so this test notices if
    # "claim missing" and "claim present but not a list" are later merged back together.
    now = int(time.time())
    token = jwt.encode(
        {"sub": "s-1", "iss": ISS, "client_id": CLIENT_ID, "token_use": USE_ACCESS,
         "iat": now, "exp": now + 3600, "username": "u@example.com"},
        _private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(TokenError, match="no cognito:groups"):
        await _verify(token)


async def test_non_list_groups_claim_is_rejected_with_distinct_message():
    # The case where the claim exists but is not a list (a different path from "claim missing"
    # above). For operational log classification to tell the two apart, the messages must differ
    # too — so it checks for the wording "not a list" rather than "missing".
    with pytest.raises(TokenError, match="not a list") as exc_info:
        await _verify(_token(**{"cognito:groups": "admin"}))  # a string, not a list
    assert "no cognito:groups" not in str(exc_info.value)


async def test_garbage_token_is_rejected():
    with pytest.raises(TokenError):
        await _verify("not-a-jwt")


async def test_jwks_is_fetched_once_and_cached():
    calls: list[str] = []
    cache = _cache(calls=calls)
    await _verify(_token(), cache=cache)
    await _verify(_token(), cache=cache)
    assert len(calls) == 1, f"JWKS should be fetched once, got {calls}"
    assert calls[0] == (
        f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}/.well-known/jwks.json")


async def test_unknown_kid_refetches_jwks_then_fails():
    # Key rotation: on seeing a kid not in the cache, refetch once. If still missing after the refetch,
    # it fails, but this must not spread into a refetch on every request.
    calls: list[str] = []
    cache = _cache(calls=calls)
    await _verify(_token(), cache=cache)          # fill the cache (fetch 1)
    other = jwt.encode({"sub": "x"}, _private_key, algorithm="RS256",
                       headers={"kid": "rotated-key"})
    with pytest.raises(TokenError):
        await _verify(other, cache=cache)          # kid miss → fetch 2
    assert len(calls) == 2, f"unknown kid must trigger exactly one refetch, got {calls}"


async def test_jwks_fetch_failure_is_a_token_error():
    # fail-closed: if the JWKS cannot be fetched, nothing passes.
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=boom)
    with pytest.raises(TokenError):
        await _verify(_token(), cache=cache)


class _FakeClock:
    """A monotonic clock that only moves manually. Tests the cooldown without sleeping."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_five_distinct_bogus_kids_within_cooldown_cause_one_fetch():
    # Attack scenario: an unauthenticated caller sends 5 JWTs in a row, each carrying a different
    # fake kid. Without the defense each would have triggered a refetch — with the cooldown
    # in place, only the first refetches (thereby starting the cooldown) and the other 4 must be
    # rejected on the spot. So across this whole batch there is exactly 1 extra fetch.
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fill the cache with a valid kid (fetch 1)
    assert len(calls) == 1

    for i in range(5):
        with pytest.raises(TokenError):
            await _verify(_bogus_token(f"bogus-kid-{i}"), cache=cache)
    assert len(calls) == 2, (
        f"expected exactly one additional fetch for the whole batch, got {calls}")


async def test_repeating_already_failed_kid_causes_no_additional_fetch():
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("repeat-offender"), cache=cache)  # fetch 2
    assert len(calls) == 2

    for _ in range(3):
        with pytest.raises(TokenError):
            await _verify(_bogus_token("repeat-offender"), cache=cache)
    assert len(calls) == 2, f"repeated bad kid must not refetch, got {calls}"


async def test_known_bad_kid_stays_rejected_without_fetch_past_cooldown():
    # The negative cache is independent of the cooldown: even after the cooldown passes, a kid already
    # confirmed as failing must keep being rejected without a refetch (refetching is for "new"
    # unconfirmed kids).
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("stale-bad-kid"), cache=cache)  # fetch 2
    assert len(calls) == 2

    clock.advance(60.0)  # well past the cooldown
    with pytest.raises(TokenError):
        await _verify(_bogus_token("stale-bad-kid"), cache=cache)  # same kid
    assert len(calls) == 2, (
        f"a kid already known-bad must not refetch even past cooldown, got {calls}")


async def test_unknown_kid_after_cooldown_expires_triggers_one_more_fetch():
    # Rotation support must stay alive: once the cooldown passes, a new unconfirmed kid
    # gets one more refetch.
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("first-bad-kid"), cache=cache)  # fetch 2
    assert len(calls) == 2

    clock.advance(30.0)  # cooldown (30s) expires
    with pytest.raises(TokenError):
        await _verify(_bogus_token("second-bad-kid"), cache=cache)  # fetch 3
    assert len(calls) == 3, f"expected exactly one more fetch, got {calls}"


async def test_known_kid_still_resolves_with_no_extra_fetch_during_cooldown():
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("bad-kid"), cache=cache)  # fetch 2 (fails)

    principal = await _verify(_token(), cache=cache)  # existing valid kid
    assert principal.role == "admin"
    assert len(calls) == 2, f"known kid must not trigger a fetch, got {calls}"


async def test_clear_resets_cooldown_and_negative_cache():
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("retry-me"), cache=cache)  # fetch 2 (fails)
    assert len(calls) == 2

    cache.clear()
    # After clear(), even the same kid is refetched again with no negative cache/cooldown.
    with pytest.raises(TokenError):
        await _verify(_bogus_token("retry-me"), cache=cache)  # fetch 3
    assert len(calls) == 3, f"clear() must allow a fresh refetch, got {calls}"


# --- Cooldown for failures of the fetch attempt itself (network outage, Cognito failure, parse failure).
# It is separate from the "refetched but the kid is missing" cooldown (30s) above — when _fetch()
# throws, that branch is never reached, so a short (5s) cooldown keeps that path
# from running away on its own.

async def test_repeated_fetch_exceptions_within_short_window_cause_one_fetch():
    calls: list[str] = []
    clock = _FakeClock()

    def boom(url: str) -> dict:
        calls.append(url)
        raise RuntimeError("network down")

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=boom, now=clock)
    for i in range(5):
        with pytest.raises(TokenError):
            await _verify(_bogus_token(f"kid-{i}"), cache=cache)
    assert len(calls) == 1, (
        f"expected exactly one fetch attempt across the outage, got {calls}")


async def test_repeated_empty_jwks_response_within_short_window_cause_one_fetch():
    calls: list[str] = []
    clock = _FakeClock()

    def empty_response(url: str) -> dict:
        calls.append(url)
        return {"keys": []}

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=empty_response,
                      now=clock)
    for i in range(5):
        with pytest.raises(TokenError):
            await _verify(_bogus_token(f"kid-{i}"), cache=cache)
    assert len(calls) == 1, (
        f"expected exactly one fetch attempt across the outage, got {calls}")


async def test_recovery_after_fetch_failure_does_not_poison_negative_cache():
    calls: list[str] = []
    clock = _FakeClock()
    healthy = {"value": False}

    def flaky(url: str) -> dict:
        calls.append(url)
        if not healthy["value"]:
            raise RuntimeError("network down")
        return _jwks()

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=flaky, now=clock)

    with pytest.raises(TokenError):
        await _verify(_bogus_token("during-outage"), cache=cache)  # fetch 1, fails
    assert len(calls) == 1

    clock.advance(5.0)  # failure cooldown (5s) expires
    healthy["value"] = True
    principal = await _verify(_token(), cache=cache)  # fetch 2: recovered, valid kid
    assert principal.role == "admin"
    assert len(calls) == 2

    # A kid attempted during the outage was never actually looked up in a JWKS response —
    # so it must not be in the negative cache. On retry it must get its own refetch again
    # (since it is not a kid already confirmed as failing).
    with pytest.raises(TokenError):
        await _verify(_bogus_token("during-outage"), cache=cache)  # fetch 3
    assert len(calls) == 3, (
        f"kid attempted during the outage must not be negative-cached, got {calls}")


async def test_successful_fetch_clears_failure_state_for_next_unknown_kid():
    # A successful fetch must reset the failure-cooldown timestamp. Note that this property fundamentally
    # cannot be proven from elapsed time alone (as a black box) —
    # for recovery to succeed through the public API (key_for), "5 or more seconds since the original
    # failure time" must already have passed, and checking again at any later point against that
    # "original failure time", with a monotonic clock, it is necessarily still past 5 seconds
    # — so it is not suppressed whether or not the reset happened (mathematically indistinguishable:
    # if now() - t0 >= 5.0, then any later now'() - t0 is always >= 5.0 too). So
    # whether the reset actually happened is proven by checking the internal state directly.
    calls: list[str] = []
    clock = _FakeClock()
    healthy = {"value": False}

    def flaky(url: str) -> dict:
        calls.append(url)
        if not healthy["value"]:
            raise RuntimeError("network down")
        return _jwks()

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=flaky, now=clock)

    with pytest.raises(TokenError):
        await _verify(_bogus_token("outage-kid"), cache=cache)  # fetch 1, fails
    assert len(calls) == 1
    assert cache._last_fetch_failure_at == 0.0

    clock.advance(5.0)  # failure cooldown (5s) expires — so the recovery fetch itself is not suppressed
    healthy["value"] = True
    await _verify(_token(), cache=cache)  # fetch 2: recovered
    assert len(calls) == 2

    assert cache._last_fetch_failure_at is None, (
        "a successful fetch must reset the failure-cooldown timestamp, "
        f"got {cache._last_fetch_failure_at!r}")

    # If it was reset, a new unconfirmed kid gets its own refetch without any more time
    # passing (since the 30s cooldown has not started yet).
    with pytest.raises(TokenError):
        await _verify(_bogus_token("brand-new-kid"), cache=cache)  # fetch 3
    assert len(calls) == 3, (
        f"a fresh unknown kid must get its own fetch right after recovery, "
        f"got {calls}")
