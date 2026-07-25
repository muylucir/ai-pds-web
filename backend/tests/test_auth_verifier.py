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

from pathfinder.auth.verifier import JwksCache, TokenError, verify_access_token

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
        "username": "admin@pathfinder.local",
    }
    claims.update(overrides)
    return jwt.encode(claims, _private_key, algorithm="RS256",
                      headers={"kid": KID})


def _cache(jwks: dict | None = None, calls: list | None = None) -> JwksCache:
    payload = jwks if jwks is not None else _jwks()

    def http_get(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        return payload

    return JwksCache(region=REGION, user_pool_id=POOL, http_get=http_get)


async def _verify(token: str, *, cache: JwksCache | None = None,
                  client_id: str = CLIENT_ID):
    return await verify_access_token(
        token, region=REGION, user_pool_id=POOL, client_id=client_id,
        jwks=cache or _cache())


async def test_valid_token_yields_principal_with_role_from_groups():
    principal = await _verify(_token())
    assert principal.username == "admin@pathfinder.local"
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
    # 여기서만 직접 서명한다.
    now = int(time.time())
    token = jwt.encode(
        {"sub": "s-1", "iss": ISS, "client_id": CLIENT_ID, "token_use": "access",
         "iat": now, "exp": now + 3600, "username": "u@x.io"},
        _private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(TokenError):
        await _verify(token)


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
