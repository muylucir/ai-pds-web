# backend/aipds/context_usage.py — 에이전트의 컨텍스트 창 사용량(화면의 "남은 %").
#
# 값의 출처는 CLI 자신이다: `ClaudeSDKClient.get_context_usage()`가 CLI의 `/context`와
# 같은 것을 돌려준다. 토큰 수를 모델의 창 크기로 나누지 않는 이유는 이 배포에서 그
# 나눗셈이 틀리기 때문이다 — 창은 `[1m]` 접미사(cli_settings)로 100만이 되고, 자동
# 압축 시점은 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`가 옮긴다. CLI는 둘 다 알고 계산한다.
#
# 실측(2026-09-29, SDK 0.2.157, Bedrock Haiku 4.5):
#   - 첫 호출은 ~650ms(API로 추정하는 것으로 보인다), 턴이 한 번 돈 뒤에는 4~5ms —
#     마지막 API 응답의 usage를 쓴다(input+cache 합과 totalTokens가 일치했다).
#   - 턴이 도는 중(도구 실행 중)에 불러도 턴이 깨지지 않는다.
#   - 자동 압축 창을 100000으로 두면 maxTokens=100000이고 categories에 "Autocompact
#     buffer"(33000)가 따로 나온다. 기본 설정에서는 그 항목이 없었다.
# 그래서 "남은 %"는 **압축까지** 남은 몫이다: 쓸 수 있는 공간(maxTokens − 압축 버퍼)
# 가운데 아직 비어 있는 비율. 0%가 곧 압축이 일어나는 지점이다.
#
# 호출은 드라이버가 한다(메인 에이전트의 응답마다 — 빌드 턴은 수십 분이라 턴 끝에만
# 재면 그동안 표시가 멈춘다). 마지막 값은 턴이 끝날 때 러너/세션이 S3에 남긴다
# (`ContextRecord`) — 연결된 클라이언트가 없을 때(세션이 닫힌 뒤, 백엔드 재시작 뒤)
# 화면이 보일 값이 그것이다.
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aipds.models import AgentEvent
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: 한 번의 조회에 쓸 수 있는 시간. 첫 조회가 ~650ms였다 — 그보다 넉넉하되, 이
#: 조회 때문에 턴이 눈에 띄게 멈추지 않을 만큼.
_PROBE_TIMEOUT_SECONDS = 3.0

_AUTOCOMPACT_BUFFER = "Autocompact buffer"

#: Discovery의 레코드(프로젝트 상대). 트랜스크립트(discovery/transcript/) 옆이다 —
#: 프로젝트 삭제가 함께 지우고, 번들이 활성 트랜스크립트와 함께 옮긴다. 대화가
#: 옮겨 가므로 그 대화의 사용량도 옮겨 가는 것이 맞다.
DISCOVERY_KEY = "discovery/context.json"


def wants_sample(msg: Any) -> bool:
    """이 메시지 뒤에 사용량을 잴 것인가. 메인 에이전트의 응답과 턴의 끝이다.

    서브에이전트의 메시지(parent_tool_use_id가 있다)는 **자기** 컨텍스트에서 돈다 —
    자동 압축이 일어나는 곳은 메인 쪽이고, 화면이 보이는 것도 그것이다.
    """
    tname = type(msg).__name__
    if tname == "ResultMessage":
        return True
    return tname == "AssistantMessage" and not getattr(msg, "parent_tool_use_id", None)


def summarize(resp: dict) -> dict | None:
    """`get_context_usage()` 응답 → 화면 payload. 모양이 다르면 None."""
    try:
        total = int(resp["totalTokens"])
        maximum = int(resp["maxTokens"])
        buffer = sum(int(c.get("tokens", 0)) for c in resp.get("categories", [])
                     if c.get("name") == _AUTOCOMPACT_BUFFER)
    except (KeyError, TypeError, ValueError):
        return None
    usable = maximum - buffer
    if maximum <= 0 or usable <= 0:
        return None
    left = max(0, usable - total)
    return {
        "total_tokens": total,
        "max_tokens": maximum,
        # 압축이 일어나는 사용량. 버퍼가 없으면 창 끝이다.
        "compact_at_tokens": usable,
        "left_pct": min(100, left * 100 // usable),
    }


class ContextMeter:
    """드라이버 하나의 측정기. 바뀐 값만 이벤트로 낸다.

    같은 API 응답의 블록마다 AssistantMessage가 따로 오므로(텍스트 하나, 도구 호출
    하나마다) 매번 내면 같은 값이 수십 번 흐른다. 비교는 화면이 보이는 정수(`left_pct`)로
    한다 — 토큰 수까지 비교하면 거의 매번 달라 걸러지는 것이 없다.
    """

    def __init__(self) -> None:
        self._shown: int | None = None
        self._warned = False

    async def sample(self, client: Any) -> AgentEvent | None:
        probe = getattr(client, "get_context_usage", None)
        if probe is None:
            return None  # 이 기능이 없는 SDK나 테스트 더블 — 표시가 없을 뿐이다
        try:
            resp = await asyncio.wait_for(probe(), _PROBE_TIMEOUT_SECONDS)
        except Exception:
            # 표시용 값이다. 턴을 막지 않고, 로그도 한 번만 남긴다 — 매 응답마다
            # 실패하면 빌드 한 번에 수백 줄이 된다.
            if not self._warned:
                self._warned = True
                _log.warning("context usage probe failed", exc_info=True)
            return None
        payload = summarize(resp) if isinstance(resp, dict) else None
        if payload is None or payload["left_pct"] == self._shown:
            return None
        self._shown = payload["left_pct"]
        return AgentEvent(kind="context",
                          payload=json.dumps(payload, ensure_ascii=False))


class ContextRecord:
    """턴의 마지막 사용량을 S3에 남긴다. 러너(Discovery)와 빌드 세션이 쓴다.

    `observe`는 이벤트 스트림을 그대로 받고, `flush`는 턴이 끝날 때 바뀐 값만 쓴다.
    `tag`는 레코드에 함께 실어 읽는 쪽이 "지금도 유효한가"를 판단하게 한다 —
    프로토타입은 SDK 세션 id를 싣는다(개선 세션은 새 대화라 이전 값이 틀린다).
    """

    def __init__(self, s3: S3StoreLike, key: str) -> None:
        self._s3 = s3
        self._key = key
        self._latest: dict | None = None
        self._dirty = False

    def observe(self, event: AgentEvent) -> None:
        if event.kind != "context" or not event.payload:
            return
        try:
            self._latest = json.loads(event.payload)
        except json.JSONDecodeError:
            return
        self._dirty = True

    async def flush(self, **tag: str | None) -> None:
        """실패는 삼킨다 — 표시용 값이 턴의 종결(동기화, done)을 막으면 안 된다."""
        if not self._dirty or self._latest is None:
            return
        self._dirty = False
        try:
            await self._s3.put(self._key, json.dumps({**self._latest, **tag},
                                                     ensure_ascii=False))
        except Exception:
            _log.exception("context usage record failed: %s", self._key)


async def load_record(s3: S3StoreLike, key: str) -> dict | None:
    """마지막으로 남긴 사용량. 없거나 읽을 수 없으면 None."""
    try:
        data = json.loads(await s3.get(key))
    except FileNotFoundError:
        return None
    except Exception:
        _log.warning("unreadable context usage record: %s", key, exc_info=True)
        return None
    return data if isinstance(data, dict) else None
