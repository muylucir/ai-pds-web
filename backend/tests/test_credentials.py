# backend/tests/test_credentials.py — 샌드박스 프로세스용 자격증명 엔드포인트(aipds/credentials.py).
from __future__ import annotations

import datetime as dt
import json
import socket

import httpx
import pytest

from aipds.credentials import (EXPIRY_MARGIN, CredentialBroker, Grant, PATH)

ROLE = "arn:aws:iam::123456789012:role/AgentRole"
T0 = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)


class FakeSts:
    def __init__(self, clock):
        self.calls = []
        self._clock = clock

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        return {"Credentials": {
            "AccessKeyId": f"ASIA{n}", "SecretAccessKey": f"secret{n}",
            "SessionToken": f"session{n}",
            "Expiration": self._clock["now"] + dt.timedelta(hours=1),
        }}


@pytest.fixture
def clock():
    return {"now": T0}


@pytest.fixture
def sts(clock):
    return FakeSts(clock)


@pytest.fixture
def broker(sts, clock):
    return CredentialBroker(ROLE, port=18001, sts_factory=lambda: sts,
                            now=lambda: clock["now"])


def test_only_llm_calling_kinds_get_credentials(broker):
    for kind in ("discovery", "build", "proto"):
        env = broker.env_for(kind, "p1", None if kind == "discovery" else "s")
        assert env["AWS_CONTAINER_CREDENTIALS_FULL_URI"] == f"http://127.0.0.1:18001{PATH}"
        assert env["AWS_CONTAINER_AUTHORIZATION_TOKEN"]
    # npm install/build는 LLM을 부르지 않는다.
    assert broker.env_for("host-build", "p1", "s") == {}


def test_tokens_are_stable_per_grant_and_distinct_across_grants(broker):
    a = broker.issue("discovery", "p1", None)
    assert broker.issue("discovery", "p1", None) == a
    assert broker.issue("discovery", "p2", None) != a
    assert broker.issue("build", "p1", "s") != a
    assert broker.resolve(a) == Grant("discovery", "p1", None)
    assert broker.resolve("nope") is None


def test_revoke_project_drops_every_grant_of_that_project(broker):
    a = broker.issue("discovery", "p1", None)
    b = broker.issue("proto", "p1", "s")
    c = broker.issue("discovery", "p2", None)
    broker.revoke_project("p1")
    assert broker.resolve(a) is None and broker.resolve(b) is None
    assert broker.resolve(c) is not None


async def test_respond_checks_path_method_and_token(broker):
    token = broker.issue("discovery", "p1", None)
    assert (await broker.respond("GET", "/other", token))[0] == 404
    assert (await broker.respond("POST", PATH, token))[0] == 404
    assert (await broker.respond("GET", PATH, None))[0] == 403
    assert (await broker.respond("GET", PATH, "wrong"))[0] == 403
    status, body = await broker.respond("GET", PATH, token)
    assert status == 200
    assert set(body) == {"AccessKeyId", "SecretAccessKey", "Token", "Expiration"}


async def test_expiration_is_reported_early(broker):
    """SDK는 만료 **뒤에야** 다시 부른다(실측). 알린 만료가 실제보다 앞서야 스트림 도중
    실제 만료를 맞지 않는다."""
    token = broker.issue("discovery", "p1", None)
    _, body = await broker.respond("GET", PATH, token)
    reported = dt.datetime.fromisoformat(body["Expiration"].replace("Z", "+00:00"))
    assert reported == T0 + dt.timedelta(hours=1) - EXPIRY_MARGIN


async def test_credentials_are_cached_then_refreshed_before_expiry(broker, sts, clock):
    token = broker.issue("build", "p1", "todo")
    await broker.respond("GET", PATH, token)
    clock["now"] = T0 + dt.timedelta(minutes=30)
    await broker.respond("GET", PATH, token)
    assert len(sts.calls) == 1
    # 알린 만료(55분)가 지나 다시 온 요청에는 새 값을 준다.
    clock["now"] = T0 + dt.timedelta(minutes=56)
    _, body = await broker.respond("GET", PATH, token)
    assert len(sts.calls) == 2 and body["AccessKeyId"] == "ASIA2"
    call = sts.calls[0]
    assert call["RoleArn"] == ROLE and call["DurationSeconds"] == 3600
    assert call["RoleSessionName"] == "aipds-build-p1"


async def test_session_names_are_sanitized(broker, sts):
    token = broker.issue("discovery", "한글 프로젝트/x", None)
    await broker.respond("GET", PATH, token)
    name = sts.calls[0]["RoleSessionName"]
    assert len(name) <= 64
    assert all(c.isascii() and (c.isalnum() or c in "+=,.@-_") for c in name)


async def test_sts_failure_is_a_500_not_a_crash(clock):
    class Boom:
        def assume_role(self, **_):
            raise RuntimeError("AccessDenied")
    b = CredentialBroker(ROLE, sts_factory=lambda: Boom(), now=lambda: clock["now"])
    token = b.issue("discovery", "p1", None)
    assert (await b.respond("GET", PATH, token))[0] == 500


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_loopback_http_round_trip(sts, clock):
    """AWS SDK가 부르는 모양 그대로: GET + Authorization 헤더에 토큰 값."""
    port = _free_port()
    b = CredentialBroker(ROLE, port=port, sts_factory=lambda: sts,
                         now=lambda: clock["now"])
    await b.start()
    try:
        env = b.env_for("proto", "p1", "todo")
        async with httpx.AsyncClient() as client:
            ok = await client.get(env["AWS_CONTAINER_CREDENTIALS_FULL_URI"],
                                  headers={"Authorization":
                                           env["AWS_CONTAINER_AUTHORIZATION_TOKEN"]})
            denied = await client.get(env["AWS_CONTAINER_CREDENTIALS_FULL_URI"])
        assert ok.status_code == 200
        assert json.loads(ok.content)["AccessKeyId"] == "ASIA1"
        assert denied.status_code == 403
    finally:
        await b.close()
