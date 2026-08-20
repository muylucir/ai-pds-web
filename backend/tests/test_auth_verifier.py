# backend/tests/test_auth_verifier.py
#
# 실 Cognito 없이 검증기를 시험한다: 테스트용 RSA 키로 토큰을 서명하고, 그 키의
# 공개 부분을 JWKS 형태로 주입한다. 문서 확인 사항 두 개가 이 테스트의 핵심이다 —
# access 토큰은 client_id로(aud 아님) 클라이언트를 식별하고, email 클레임이 없다.
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

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict:
    """테스트 키의 공개 부분을 Cognito JWKS 형태로 내놓는다."""
    from jwt.algorithms import RSAAlgorithm
    jwk = RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _token(**overrides) -> str:
    """Cognito access 토큰의 기본 payload 형태(문서 확인).

    email이 없는 것이 의도다 — access 토큰에는 email 클레임이 존재하지 않는다.
    """
    now = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "cognito:groups": ["admin"],
        "iss": ISS,
        "client_id": CLIENT_ID,
        "token_use": "access",
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
    """서명은 우리 테스트 키로 유효하지만, 헤더의 kid가 JWKS에 없는 토큰.

    kid는 서명 검증 전에 헤더에서 읽히므로, payload 내용은 이 테스트들과
    무관하다 — 매번 kid 미스로 거부되는 경로만 시험한다.
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


async def test_pm_group_yields_pm_role():
    principal = await _verify(_token(**{"cognito:groups": ["pm"]}))
    assert principal.role == "pm"


async def test_admin_wins_when_user_is_in_both_groups():
    # 두 그룹에 모두 속하면 더 넓은 권한(admin)으로 해석한다 — 그래야 관리자를
    # pm 그룹에 추가하는 실수가 권한을 조용히 깎지 않는다.
    principal = await _verify(_token(**{"cognito:groups": ["pm", "admin"]}))
    assert principal.role == "admin"


async def test_expired_token_is_rejected():
    now = int(time.time())
    with pytest.raises(TokenError):
        await _verify(_token(exp=now - 10, iat=now - 3600))


async def test_wrong_client_id_is_rejected():
    # access 토큰은 aud가 아니라 client_id로 앱 클라이언트를 식별한다.
    with pytest.raises(TokenError):
        await _verify(_token(client_id="someone-elses-client"))


async def test_wrong_issuer_is_rejected():
    with pytest.raises(TokenError):
        await _verify(_token(iss="https://cognito-idp.us-east-1.amazonaws.com/other"))


async def test_id_token_is_rejected():
    # id 토큰을 access 토큰 자리에 넣는 혼동을 막는다.
    with pytest.raises(TokenError):
        await _verify(_token(token_use="id"))


async def test_token_without_known_group_is_rejected():
    # 그룹이 역할의 유일한 출처다. 어느 그룹에도 없으면 역할이 없으므로 거부한다.
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": []}))
    with pytest.raises(TokenError):
        await _verify(_token(**{"cognito:groups": ["some-other-group"]}))


async def test_missing_groups_claim_is_rejected():
    # 클레임 자체가 없는 경우(빈 배열과 구분). _token()은 항상 groups를 넣으므로
    # 여기서만 직접 서명한다. 메시지까지 확인한다 — "클레임이 없음"과 "클레임이
    # 있지만 리스트가 아님"을 나중에 다시 합쳐도 이 테스트가 알아채도록.
    now = int(time.time())
    token = jwt.encode(
        {"sub": "s-1", "iss": ISS, "client_id": CLIENT_ID, "token_use": "access",
         "iat": now, "exp": now + 3600, "username": "u@x.io"},
        _private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(TokenError, match="no cognito:groups"):
        await _verify(token)


async def test_non_list_groups_claim_is_rejected_with_distinct_message():
    # 클레임이 존재하지만 리스트가 아닌 경우(위의 "클레임 자체가 없음"과는 다른
    # 경로). 운영 로그 분류가 두 경우를 구분할 수 있으려면 메시지도 달라야
    # 한다 — 그래서 "없음"이 아니라 "리스트가 아님"이라는 문구를 확인한다.
    with pytest.raises(TokenError, match="not a list") as exc_info:
        await _verify(_token(**{"cognito:groups": "admin"}))  # 문자열, 리스트 아님
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
    # 키 로테이션: 캐시에 없는 kid를 보면 한 번 재조회한다. 재조회해도 없으면
    # 실패하지만, 매 요청 재조회로 번지지는 않아야 한다.
    calls: list[str] = []
    cache = _cache(calls=calls)
    await _verify(_token(), cache=cache)          # 캐시 채움 (fetch 1)
    other = jwt.encode({"sub": "x"}, _private_key, algorithm="RS256",
                       headers={"kid": "rotated-key"})
    with pytest.raises(TokenError):
        await _verify(other, cache=cache)          # kid 미스 → fetch 2
    assert len(calls) == 2, f"unknown kid must trigger exactly one refetch, got {calls}"


async def test_jwks_fetch_failure_is_a_token_error():
    # fail-closed: JWKS를 못 받으면 통과시키지 않는다.
    def boom(url: str) -> dict:
        raise RuntimeError("network down")

    cache = JwksCache(region=REGION, user_pool_id=POOL, http_get=boom)
    with pytest.raises(TokenError):
        await _verify(_token(), cache=cache)


class _FakeClock:
    """수동으로만 흐르는 monotonic 시계. sleep 없이 쿨다운을 시험한다."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_five_distinct_bogus_kids_within_cooldown_cause_one_fetch():
    # 공격 시나리오: 인증되지 않은 호출자가 서로 다른 가짜 kid를 실어 나르는
    # JWT 5개를 연달아 보낸다. 방어 전이라면 매번 재조회했을 것 — 쿨다운이
    # 걸리면 그중 첫 번째만 재조회하고(그래서 쿨다운이 시작되고) 나머지 4개는
    # 그 자리에서 거부돼야 한다. 즉 이 배치 전체로 추가 fetch는 정확히 1번.
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # 정상 kid로 캐시를 채운다 (fetch 1)
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
    # 네거티브 캐시는 쿨다운과 독립적이다: 쿨다운이 지나도 이미 실패로 확인된
    # kid 자체는 재조회 없이 계속 거부돼야 한다(재조회는 "새로운" 미확인 kid의
    # 몫이다).
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("stale-bad-kid"), cache=cache)  # fetch 2
    assert len(calls) == 2

    clock.advance(60.0)  # 쿨다운을 넉넉히 넘긴다
    with pytest.raises(TokenError):
        await _verify(_bogus_token("stale-bad-kid"), cache=cache)  # 같은 kid
    assert len(calls) == 2, (
        f"a kid already known-bad must not refetch even past cooldown, got {calls}")


async def test_unknown_kid_after_cooldown_expires_triggers_one_more_fetch():
    # 로테이션 지원은 살아있어야 한다: 쿨다운이 지나면 새로운 미확인 kid가
    # 다시 한번 재조회를 받는다.
    calls: list[str] = []
    clock = _FakeClock()
    cache = _cache(calls=calls, now=clock)
    await _verify(_token(), cache=cache)  # fetch 1

    with pytest.raises(TokenError):
        await _verify(_bogus_token("first-bad-kid"), cache=cache)  # fetch 2
    assert len(calls) == 2

    clock.advance(30.0)  # 쿨다운(30s) 만료
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

    principal = await _verify(_token(), cache=cache)  # 기존 정상 kid
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
    # clear() 이후에는 같은 kid라도 네거티브 캐시/쿨다운 없이 다시 재조회한다.
    with pytest.raises(TokenError):
        await _verify(_bogus_token("retry-me"), cache=cache)  # fetch 3
    assert len(calls) == 3, f"clear() must allow a fresh refetch, got {calls}"


# --- fetch 시도 자체의 실패(네트워크 단절, Cognito 장애, 파싱 실패)에 대한
# 쿨다운. 위의 "재조회했지만 kid가 없음" 쿨다운(30s)과는 별개다 — _fetch()가
# 예외를 던지면 그 분기에 도달하지 못하므로, 그 경로가 별도로 폭주하지 않도록
# 짧은(5s) 쿨다운을 둔다.

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

    clock.advance(5.0)  # 실패 쿨다운(5s) 만료
    healthy["value"] = True
    principal = await _verify(_token(), cache=cache)  # fetch 2: 복구, 정상 kid
    assert principal.role == "admin"
    assert len(calls) == 2

    # 장애 중에 시도됐던 kid는 실제로 JWKS 응답 안에서 찾아본 적이 없다 —
    # 그러니 네거티브 캐시에 들어가 있으면 안 된다. 재시도하면 (이미 실패로
    # 확정된 kid가 아니므로) 그 나름의 재조회를 다시 받아야 한다.
    with pytest.raises(TokenError):
        await _verify(_bogus_token("during-outage"), cache=cache)  # fetch 3
    assert len(calls) == 3, (
        f"kid attempted during the outage must not be negative-cached, got {calls}")


async def test_successful_fetch_clears_failure_state_for_next_unknown_kid():
    # 성공한 fetch는 실패 쿨다운 타임스탬프를 리셋해야 한다. 이 속성은 흐른
    # 시간만으로는(블랙박스로는) 근본적으로 증명할 수 없다는 점에 주의한다 —
    # 복구가 공개 API(key_for)를 통해 성공하려면 이미 "원래 실패 시각으로부터
    # 5초 이상 경과"했어야 하고, 그 뒤 어떤 시점에 그 "원래 실패 시각"을
    # 기준으로 다시 확인해도 단조 시계이므로 역시 5초를 넘겨 있을 수밖에
    # 없다 — 리셋 여부와 무관하게 억제되지 않는다(수학적으로 구분 불가능:
    # now() - t0 >= 5.0 이었다면 이후의 now'() - t0 도 항상 >= 5.0). 그래서
    # 리셋이 실제로 일어났는지는 내부 상태를 직접 확인해서 증명한다.
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

    clock.advance(5.0)  # 실패 쿨다운(5s) 만료 — 복구 fetch 자체가 억제되지 않게
    healthy["value"] = True
    await _verify(_token(), cache=cache)  # fetch 2: 복구
    assert len(calls) == 2

    assert cache._last_fetch_failure_at is None, (
        "a successful fetch must reset the failure-cooldown timestamp, "
        f"got {cache._last_fetch_failure_at!r}")

    # 리셋됐다면 새 미확인 kid는 시간을 더 흘리지 않고도 자기 몫의 재조회를
    # 받는다(30s 쿨다운은 아직 시작되지 않았으므로).
    with pytest.raises(TokenError):
        await _verify(_bogus_token("brand-new-kid"), cache=cache)  # fetch 3
    assert len(calls) == 3, (
        f"a fresh unknown kid must get its own fetch right after recovery, "
        f"got {calls}")
