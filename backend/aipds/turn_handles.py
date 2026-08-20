# backend/aipds/turn_handles.py — 긴 턴 입력을 URL에서 빼기 위한 핸들.
#
# **왜 있는가.** 채팅 텍스트가 SSE URL의 쿼리스트링으로 가고 있었다
# (`GET /events?text=...`). EventSource는 GET만 지원해 요청 본문을 실을 수
# 없기 때문이다. 그런데 한글은 encodeURIComponent로 한 글자가 9바이트가 된다
# (`%EC%97%AC` × 3). 실측: 2,164자 입력 → 14,376바이트 요청 라인. 여기에 인증
# 쿠키(Cognito JWT 3개, 약 3.7KB)가 더해져 Node.js의 maxHeaderSize 기본값
# 16,384바이트를 넘고, Next.js 프록시가 **HTTP 431**로 거절했다.
#
# 그 실패가 화면에서 "연결이 끊어졌습니다"로 보였던 이유: EventSource는 HTTP
# 상태 코드를 노출하지 않는다. 431이든 500이든 네트워크 단절이든 똑같이
# onerror만 발화한다. 그래서 원인이 화면에서 완전히 숨었다.
#
# **해결 모양.** 텍스트를 POST 본문으로 받아 짧은 핸들로 바꾸고, SSE URL에는
# 그 핸들만 싣는다. 이 리포에 같은 모양의 선례가 있다 — 프로토타입 첫 턴은
# `?text=__first__` 센티널만 보내고 실제 프롬프트는 서버가 채운다
# (routes/prototypes.py의 _FIRST_TURN_SENTINEL).
#
# EventSource를 fetch+ReadableStream으로 교체하지 않은 이유: 이 프록시 계층은
# HTTP/2에서 SSE가 깨지는 문제를 이미 겪고 해결한 곳이다(app/api/[...path]/
# route.ts 헤더의 ERR_HTTP2_PROTOCOL_ERROR 기록). 재연결과 쿠키 인증이
# 브라우저에 내장된 EventSource를 유지하고, 문제의 원인인 URL 길이만 없앤다.
#
# **인메모리인 이유.** 이 값은 POST 직후 수초 안에 GET으로 소비된다. S3에 두면
# 턴 개시마다 왕복이 붙어 첫 응답이 늦어지고, 수초 사는 값에 영구 저장소를
# 쓰는 셈이다. 백엔드가 재시작되면 유실되지만 그때는 그 턴만 실패하고 사용자가
# 다시 보내면 된다 — app.proto_sessions도 같은 성질의 인메모리다.
from __future__ import annotations

import logging
import secrets
import time
from typing import Callable

_log = logging.getLogger(__name__)

#: 핸들 수명. POST → GET 사이의 브라우저 왕복 한 번을 덮으면 충분하고, 길게
#: 두면 URL에 남은 핸들이 오래 살아 있는 창이 늘어난다. 60초는 느린 회선에서도
#: 넉넉하면서 사람이 URL을 복사해 재사용하기에는 짧다.
HANDLE_TTL_SECONDS = 60


class TurnHandleStore:
    """턴 입력(payload) → 짧은 핸들. 1회용이고 만료된다.

    핸들은 **인증이 아니다.** 라우트가 이미 프로젝트 접근을 검증한 뒤에만
    핸들을 만들고, 소비할 때도 같은 프로젝트인지 확인한다. 그래도 추측 불가한
    값을 쓰는 이유는 URL이 브라우저 히스토리·리퍼러·프록시 로그에 남기
    때문이다 — 순번이면 남의 턴 텍스트를 읽는 경로가 된다.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        # (project_id, payload, created_at)를 핸들로 색인한다.
        self._items: dict[str, tuple[str, dict, float]] = {}
        # 테스트가 만료를 결정적으로 시험할 수 있게 시계를 주입받는다.
        self._clock = clock or time.monotonic

    def create(self, project_id: str, payload: dict) -> str:
        """핸들을 만든다. 만료된 것들을 함께 정리한다.

        별도 타이머를 두지 않는 이유: 이 저장소는 프로세스 수명 동안만 살고,
        턴 개시마다 반드시 이 메서드가 불린다 — 정리 시점이 이미 있다.
        """
        self._purge()
        handle = secrets.token_hex(16)
        self._items[handle] = (project_id, payload, self._clock())
        return handle

    def consume(self, project_id: str, handle: str) -> dict | None:
        """핸들을 소비하고 payload를 돌려준다. 없거나 만료됐거나 프로젝트가
        다르면 None.

        1회용인 이유는 위 docstring과 같다 — URL에 남은 핸들로 같은 턴을 다시
        돌릴 수 있으면 안 된다. 프로젝트가 다른 경우는 소비하지 않는다:
        엉뚱한 조회가 정상 소유자의 핸들을 태워 버리면, 공격이 아니라 버그일
        때 원인 추적이 어려워진다.
        """
        item = self._items.get(handle)
        if item is None:
            return None
        owner, payload, created = item
        if owner != project_id:
            _log.warning("turn handle used with the wrong project: %s", project_id)
            return None
        if self._clock() - created > HANDLE_TTL_SECONDS:
            self._items.pop(handle, None)
            return None
        self._items.pop(handle, None)
        return payload

    def size(self) -> int:
        """살아 있는 핸들 수. 테스트가 정리를 확인하는 데 쓴다."""
        return len(self._items)

    def _purge(self) -> None:
        cutoff = self._clock() - HANDLE_TTL_SECONDS
        stale = [h for h, (_, _, created) in self._items.items() if created < cutoff]
        for h in stale:
            self._items.pop(h, None)
