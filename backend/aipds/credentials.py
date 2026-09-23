# backend/aipds/credentials.py — 에이전트·프로토타입 프로세스에 Bedrock 전용 단기 자격증명을 준다.
#
# 실행 래퍼(aipds/launcher.py) 아래의 프로세스는 IMDS에 닿지 못한다(unit별 IPAddressDeny).
# 대신 AWS SDK의 컨테이너 자격증명 규약을 쓴다: `AWS_CONTAINER_CREDENTIALS_FULL_URI`(루프백 HTTP)와
# `AWS_CONTAINER_AUTHORIZATION_TOKEN`. SDK는 그 URI를 부르고, 만료되면 다시 부른다.
# 실측(2026-09-23, 번들 CLI 2.1.277): IMDS가 막힌 채로 Bedrock 호출이 됐고, 만료 **뒤** 다음
# 호출에서 재요청했다 — 그래서 알려 주는 만료를 실제보다 앞당긴다(EXPIRY_MARGIN).
#
# 주는 자격증명은 인스턴스 롤이 아니라 **AgentRole**(infra/lib/aipds-agent-creds-stack.ts)의
# 것이다: Bedrock 호출만 된다. S3·Cognito·Secrets Manager는 없다.
#
# **왜 8000이 아닌 별도 포트인가.** nginx도 127.0.0.1에서 8000으로 프록시하므로 앱 포트에 두면
# `client.host`로는 외부 요청과 구분되지 않는다. 이 포트는 nginx가 모르는 포트다.
# 그래도 루프백의 누구나 닿을 수 있으므로 모든 요청은 토큰을 검사한다.
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable

import h11

_log = logging.getLogger("aipds.credentials")

ROLE_ENV = "AIPDS_AGENT_ROLE_ARN"
PORT_ENV = "AIPDS_CREDENTIALS_PORT"
DEFAULT_PORT = 8001
PATH = "/credentials"

#: 호출자에게 알리는 만료를 실제 STS 만료보다 이만큼 앞당긴다. SDK가 만료 뒤에야 다시 부르므로,
#: 긴 스트림 도중 실제 만료를 맞지 않게 한다.
EXPIRY_MARGIN = dt.timedelta(minutes=5)
#: 캐시된 자격증명이 이보다 적게 남으면 새로 받는다. EXPIRY_MARGIN보다 커야 한다 — 알린 만료가
#: 지나 다시 온 요청에 같은(이미 알린 만료가 지난) 값을 주지 않게.
REFRESH_BEFORE = dt.timedelta(minutes=10)
#: 롤 체이닝(인스턴스 롤 → AgentRole)의 상한이 1시간이다.
SESSION_SECONDS = 3600

#: 자격증명을 받는 kind. host-build(npm install/build)는 LLM을 부를 일이 없다.
CREDENTIAL_KINDS = {"discovery", "build", "proto"}

#: STS RoleSessionName이 받는 문자. ASCII로 한정한다 — 파이썬의 `\w`는 한글도 받는데
#: project id는 한글일 수 있고, STS는 그 이름을 ValidationError로 거부한다.
_SESSION_NAME_BAD = re.compile(r"[^\w+=,.@-]", re.ASCII)


@dataclass(frozen=True)
class Grant:
    kind: str
    project_id: str
    slug: str | None


@dataclass
class _Cached:
    body: dict[str, str]
    expires: dt.datetime


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CredentialBroker:
    def __init__(self, role_arn: str, port: int = DEFAULT_PORT,
                 sts_factory: Callable[[], Any] | None = None,
                 now: Callable[[], dt.datetime] = _now) -> None:
        self.role_arn = role_arn
        self.port = port
        self._sts_factory = sts_factory or _default_sts
        self._now = now
        self._tokens: dict[str, Grant] = {}
        self._cache: dict[Grant, _Cached] = {}
        self._locks: dict[Grant, asyncio.Lock] = {}
        self._server: asyncio.AbstractServer | None = None

    # ---- tokens ----

    def env_for(self, kind: str, project_id: str, slug: str | None) -> dict[str, str]:
        """이 프로세스에 줄 env. 자격증명을 받지 않는 kind면 빈 dict."""
        if kind not in CREDENTIAL_KINDS:
            return {}
        return {
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": f"http://127.0.0.1:{self.port}{PATH}",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN": self.issue(kind, project_id, slug),
        }

    def issue(self, kind: str, project_id: str, slug: str | None) -> str:
        """(kind, project, slug)의 토큰. 같은 셋에는 같은 토큰 — 재연결마다 늘지 않게."""
        grant = Grant(kind, project_id, slug)
        for token, known in self._tokens.items():
            if known == grant:
                return token
        token = secrets.token_urlsafe(32)
        self._tokens[token] = grant
        return token

    def revoke_project(self, project_id: str) -> None:
        for token, grant in list(self._tokens.items()):
            if grant.project_id == project_id:
                del self._tokens[token]
                self._cache.pop(grant, None)

    def resolve(self, token: str) -> Grant | None:
        found = None
        for known, grant in self._tokens.items():
            if secrets.compare_digest(known, token):
                found = grant
        return found

    # ---- credentials ----

    async def credentials(self, grant: Grant) -> dict[str, str]:
        lock = self._locks.setdefault(grant, asyncio.Lock())
        async with lock:
            cached = self._cache.get(grant)
            if cached is not None and cached.expires - self._now() > REFRESH_BEFORE:
                return cached.body
            name = _SESSION_NAME_BAD.sub("-", f"aipds-{grant.kind}-{grant.project_id}")[:64]
            resp = await asyncio.to_thread(
                lambda: self._sts_factory().assume_role(
                    RoleArn=self.role_arn, RoleSessionName=name,
                    DurationSeconds=SESSION_SECONDS))
            creds = resp["Credentials"]
            expires = creds["Expiration"]
            if isinstance(expires, str):
                expires = dt.datetime.fromisoformat(expires.replace("Z", "+00:00"))
            body = {
                "AccessKeyId": creds["AccessKeyId"],
                "SecretAccessKey": creds["SecretAccessKey"],
                "Token": creds["SessionToken"],
                "Expiration": _iso(expires - EXPIRY_MARGIN),
            }
            self._cache[grant] = _Cached(body=body, expires=expires)
            return body

    async def respond(self, method: str, target: str,
                      authorization: str | None) -> tuple[int, dict | None]:
        """(상태 코드, JSON 본문). HTTP와 떼어 둔 판정부 — 테스트가 직접 부른다."""
        if method != "GET" or target.split("?", 1)[0] != PATH:
            return 404, None
        grant = self.resolve(authorization or "")
        if grant is None:
            return 403, None
        try:
            return 200, await self.credentials(grant)
        except Exception:
            _log.exception("AssumeRole for %s/%s failed", grant.kind, grant.project_id)
            return 500, None

    # ---- loopback HTTP ----

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        _log.info("credential endpoint on 127.0.0.1:%d (role %s)", self.port, self.role_arn)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        conn = h11.Connection(h11.SERVER, max_incomplete_event_size=16 * 1024)
        try:
            request = None
            while request is None:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    data = await asyncio.wait_for(reader.read(8192), timeout=10)
                    conn.receive_data(data)
                    if not data:
                        return
                elif isinstance(event, h11.Request):
                    request = event
                else:
                    return
            headers = {k.decode().lower(): v.decode("latin-1") for k, v in request.headers}
            status, body = await self.respond(
                request.method.decode(), request.target.decode("latin-1"),
                headers.get("authorization"))
            payload = json.dumps(body).encode() if body is not None else b""
            writer.write(conn.send(h11.Response(status_code=status, headers=[
                ("content-type", "application/json"),
                ("content-length", str(len(payload))),
                ("connection", "close"),
            ])))
            writer.write(conn.send(h11.Data(data=payload)))
            writer.write(conn.send(h11.EndOfMessage()))
            await writer.drain()
        except (h11.RemoteProtocolError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()


def _default_sts():
    import os

    import boto3
    return boto3.client("sts", region_name=os.environ.get("AWS_REGION"))
