# backend/aipds/credentials.py — gives agent and prototype processes short-lived, Bedrock-only credentials.
#
# Processes under the launch wrapper (aipds/launcher.py) cannot reach IMDS (per-unit IPAddressDeny).
# Instead they use the AWS SDK's container credentials convention: `AWS_CONTAINER_CREDENTIALS_FULL_URI`
# (loopback HTTP) and `AWS_CONTAINER_AUTHORIZATION_TOKEN`. The SDK calls that URI, and calls it again on expiry.
# Observed (2026-09-23, bundled CLI 2.1.277): Bedrock calls worked with IMDS blocked, and it re-requested on
# the next call **after** expiry -- so the expiry we report is moved earlier than the real one (EXPIRY_MARGIN).
#
# The credentials handed out belong to **AgentRole** (infra/lib/aipds-agent-creds-stack.ts), not the
# instance role: they can only call Bedrock. No S3, Cognito, or Secrets Manager.
#
# **Why a separate port rather than 8000.** Next's `/api` proxy also sends from 127.0.0.1 to 8000, so on the
# app port `client.host` could not distinguish this from external requests. nginx does not know this port.
# Still, anyone on loopback can reach it, so every request is checked for the token.
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

#: The expiry reported to callers is moved this much earlier than the real STS expiry. The SDK only
#: calls again after expiry, so this keeps a long stream from hitting the real expiry midway.
EXPIRY_MARGIN = dt.timedelta(minutes=5)
#: Fetch new credentials when the cached ones have less than this left. Must be larger than
#: EXPIRY_MARGIN -- so a request that comes back after the reported expiry does not get the same
#: value (whose reported expiry has already passed).
REFRESH_BEFORE = dt.timedelta(minutes=10)
#: Role chaining (instance role → AgentRole) is capped at 1 hour.
SESSION_SECONDS = 3600

#: The kinds that receive credentials. host-build (npm install/build) never calls an LLM.
CREDENTIAL_KINDS = {"discovery", "build", "proto"}

#: Characters STS RoleSessionName accepts. Restricted to ASCII -- Python's `\w` also matches Hangul,
#: a project id can be in Hangul, and STS rejects such a name with a ValidationError.
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
        """The env to give this process. An empty dict for kinds that do not receive credentials."""
        if kind not in CREDENTIAL_KINDS:
            return {}
        return {
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": f"http://127.0.0.1:{self.port}{PATH}",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN": self.issue(kind, project_id, slug),
        }

    def issue(self, kind: str, project_id: str, slug: str | None) -> str:
        """The token for (kind, project, slug). Same triple, same token -- so they do not multiply on every reconnect."""
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
        """(status code, JSON body). The decision logic kept apart from HTTP -- tests call it directly."""
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
