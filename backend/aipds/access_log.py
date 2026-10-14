# backend/aipds/access_log.py — 누가 무엇을 했는가를 한 줄씩 남긴다.
#
# **왜 있는가.** 프로젝트는 소유자 없이 모든 pm에게 열려 있다(매뉴얼의 "PM: 프로젝트 전체
# 접근"). 그 설계에서 남용을 막는 것은 권한이 아니라 기록이다 — 그런데 uvicorn의 접근 로그는
# 요청자를 모르고, `audit_log.py`의 audit.md는 AI-PLC 산출물이라 신원을 담지 않으며 에이전트가
# 고칠 수 있다. 이 모듈이 인증된 요청의 신원을 백엔드 로그(journald)에 남긴다.
#
# **무엇을 남기는가.** 상태를 바꾸는 요청(GET·HEAD·OPTIONS가 아닌 것) 전부와, 응답이 첨부
# 파일인 GET(내보내기·다운로드 — `Content-Disposition`으로 안다). 목록 폴링과 SSE까지
# 남기면 기록이 잡음에 묻힌다. 쿼리 문자열은 남기지 않는다(토큰이 실릴 수 있다). 이메일은
# access 토큰에 없으므로(auth/models.Principal) username과 sub로 남긴다.
#
# 순수 ASGI 미들웨어인 이유: BaseHTTPMiddleware는 응답 본문을 감싸므로 SSE 스트림의 흐름에
# 끼어든다. 여기서는 응답 시작 메시지만 엿보고 그대로 넘긴다.
from __future__ import annotations

import json
import logging

_log = logging.getLogger("aipds.access")

#: auth/deps.require_user가 요청 상태에 심는 이름.
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
        return  # 공개 라우트, 또는 인증 전에 끝난 요청(401)
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
