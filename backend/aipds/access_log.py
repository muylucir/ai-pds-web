# backend/aipds/access_log.py — records who did what, one line at a time.
#
# **Why it exists.** Projects are open to every pm without an owner (the manual's "PM: 프로젝트 전체
# 접근" (full project access)). In that design, what prevents abuse is not permissions but records — yet uvicorn's access log
# does not know the requester, and `audit_log.py`'s audit.md is an AI-PLC artifact that carries no identity and the agent
# can edit it. This module records the identity of authenticated requests in the backend log (journald).
#
# **What it records.** Every state-changing request (anything other than GET, HEAD, OPTIONS), plus GETs whose
# response is an attachment (export/download — identified by `Content-Disposition`). Recording list polling and SSE
# too would bury the record in noise. Query strings are not recorded (they may carry tokens). Email is not in
# the access token (auth/models.Principal), so username and sub are recorded instead.
#
# Why it is pure ASGI middleware: BaseHTTPMiddleware wraps the response body, so it interferes with the flow
# of SSE streams. Here we only peek at the response start message and pass it through.
from __future__ import annotations

import json
import logging

_log = logging.getLogger("aipds.access")

#: The name auth/deps.require_user plants in the request state.
PRINCIPAL_STATE = "principal"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AccessLogMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_and_record(message):
            if message["type"] == "http.response.start":
                _record(scope, message)
            await send(message)

        await self.app(scope, receive, send_and_record)


def _record(scope, start_message) -> None:
    principal = (scope.get("state") or {}).get(PRINCIPAL_STATE)
    if principal is None:
        return  # a public route, or a request that ended before authentication (401)
    method = scope.get("method", "")
    attachment = any(name.lower() == b"content-disposition"
                     for name, _ in start_message.get("headers") or [])
    if method in _SAFE_METHODS and not attachment:
        return
    _log.info(json.dumps({
        "user": principal.username,
        "sub": principal.sub,
        "role": principal.role,
        "method": method,
        "path": scope.get("path", ""),
        "status": start_message.get("status"),
    }, ensure_ascii=False))
