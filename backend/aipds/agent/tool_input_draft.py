# backend/aipds/agent/tool_input_draft.py — 모델이 쓰고 있는 파일의 "지금까지".
#
# **왜 있는가(2026-10-07 실측).** Discovery 턴 시간의 약 60%가 파일 내용을 생성하는
# 시간이었다(chicken 1709초 중 1029초, tobacco 1649초 중 964초). 쓰기 도구의 실행은
# 매번 0.1초다 — 걸리는 것은 모델이 도구 입력(파일 전문)을 흘려보내는 시간이고,
# `discovery-document.md` Part 1은 그것만 128~136초였다. 그동안 화면에는 아무 신호가
# 없었다: 드라이버가 텍스트 델타만 화면으로 보내고 도구 블록은 완성된 뒤에야 알았다.
# 가장 중요한 문서를 쓰는 순간이 가장 긴 침묵이었고, 사용자가 안내 문장을 보고 6초
# 만에 턴을 끊은 사례(TestRachnaCostcoDelivery)가 그 모양이다.
#
# Bedrock은 도구 입력을 `input_json_delta`로 잘게 보낸다(실측: 한국어 문서 하나에
# 583조각, 40초에 걸쳐 고르게). 그 조각은 JSON **문자열의 중간**에서 끊기므로 그대로
# 읽을 수 없다 — 이 모듈이 그것을 풀어 `file_path`와 본문 길이를 조금씩 얻는다.
#
# **화면에 싣는 것은 경로와 글자 수뿐이다.** "문서 작성 중 · business-context.md · 1,234자"
# 면 기다려도 되는지가 판단된다 — 글자 수가 올라가는 것이 살아 있음의 증거다. 본문을
# 실시간으로 미리 보여 주는 것은 필요하지 않다고 판단했다(2026-10-07). 본문을 싣지 않으므로
# 감사 로그 같은, 정본에 올릴 때 자격증명을 지우는 파일의 내용이 실시간으로 새는 경로도 없다.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from aipds.models import AgentEvent

#: 이벤트 kind. `models.AgentEvent.kind`의 Literal과 같은 값이어야 한다.
DRAFT = "draft"

#: 같은 초안의 이벤트 사이 최소 간격(초). 조각은 수십 ms마다 오므로 그대로 흘리면
#: 문서 하나에 수백 개다 — 화면은 이 간격이면 충분히 "써지고 있다"로 읽는다.
EMIT_INTERVAL_S = 0.25

#: 풀어낼 최상위 키. 그 밖의 값(MultiEdit의 `edits` 배열 등)은 건너뛴다.
_PATH_KEY = "file_path"
_BODY_KEYS = {"Write": "content", "Edit": "new_string"}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class JsonStringFields:
    """흘러 들어오는 JSON 객체에서 최상위 문자열 필드를 조금씩 풀어낸다.

    일반 JSON 파서가 아니다. 도구 입력은 평평한 객체이고, 필요한 것은 그 최상위
    문자열 값뿐이다. 중첩된 값은 깊이만 세고 건너뛴다. 이스케이프가 조각 경계에서
    끊겨도(`\\` 뒤, `\\u` 네 자리 중간, 서로게이트 쌍 사이) 다음 조각에서 잇는다.
    """

    def __init__(self, keys: set[str]):
        self._keys = keys
        self._depth = 0
        self._expect_key = False
        self._in_string = False
        self._role = "skip"          # "key" | "value" | "skip"
        self._key = ""               # 지금 읽는 키(role == "key")
        self._last_key = ""          # 직전에 끝난 키 — 이어지는 값의 이름
        self._value_key: str | None = None
        self._escape: str | None = None   # None | "\\" | "u" + 지금까지의 hex
        self._high: int | None = None     # 짝을 기다리는 상위 서로게이트
        self.values: dict[str, str] = {}
        self.complete: set[str] = set()

    def feed(self, chunk: str) -> dict[str, str]:
        """조각을 먹이고, 이번에 새로 풀린 텍스트를 키별로 돌려준다."""
        fresh: dict[str, str] = {}
        for ch in chunk:
            if self._in_string:
                self._string_char(ch, fresh)
            else:
                self._structure_char(ch)
        return fresh

    def _structure_char(self, ch: str) -> None:
        if ch == '"':
            self._in_string = True
            if self._depth == 1 and self._expect_key:
                self._role, self._key = "key", ""
            elif self._depth == 1 and self._last_key in self._keys:
                self._role, self._value_key = "value", self._last_key
                self.values.setdefault(self._last_key, "")
            else:
                self._role = "skip"
        elif ch in "{[":
            self._depth += 1
            if self._depth == 1:
                self._expect_key = True
        elif ch in "}]":
            self._depth -= 1
        elif self._depth == 1 and ch == ":":
            self._expect_key = False
        elif self._depth == 1 and ch == ",":
            self._expect_key = True

    def _string_char(self, ch: str, fresh: dict[str, str]) -> None:
        if self._escape is None:
            if ch == "\\":
                self._escape = "\\"
            elif ch == '"':
                self._end_string()
            else:
                self._emit(ch, fresh)
            return
        if self._escape == "\\":
            if ch == "u":
                self._escape = "u"
            else:
                self._escape = None
                self._emit(_ESCAPES.get(ch, ch), fresh)
            return
        self._escape += ch
        if len(self._escape) < 5:      # "u" + 네 자리
            return
        code, self._escape = int(self._escape[1:], 16), None
        if 0xD800 <= code <= 0xDBFF:
            self._high = code
            return
        if 0xDC00 <= code <= 0xDFFF and self._high is not None:
            code = 0x10000 + ((self._high - 0xD800) << 10) + (code - 0xDC00)
            self._high = None
        self._emit(chr(code), fresh)

    def _emit(self, text: str, fresh: dict[str, str]) -> None:
        if self._high is not None:     # 짝 없는 상위 서로게이트 — 버리지 않고 표시한다
            self._high = None
            text = "\ufffd" + text
        if self._role == "key":
            self._key += text
        elif self._role == "value" and self._value_key is not None:
            self.values[self._value_key] += text
            fresh[self._value_key] = fresh.get(self._value_key, "") + text

    def _end_string(self) -> None:
        self._in_string = False
        if self._role == "key":
            self._last_key = self._key
        elif self._role == "value" and self._value_key is not None:
            self.complete.add(self._value_key)
            self._value_key = None
        self._role = "skip"


@dataclass
class ToolInputDraft:
    """쓰기 도구 블록 하나. 드라이버가 블록 시작에 만들고 조각마다 `feed`한다.

    `to_rel`은 도구 입력의 절대 경로를 워크스페이스 상대 경로로 바꾼다(밖이면
    None) — 드라이버의 `_rel`이다.
    """
    tool_use_id: str
    tool: str
    to_rel: Callable[[str], str | None]
    path: str | None = None
    chars: int = 0
    _fields: JsonStringFields = field(init=False)
    _body_key: str | None = field(init=False)
    _last_emit: float | None = field(default=None, init=False)
    _emitted_chars: int = field(default=-1, init=False)

    def __post_init__(self) -> None:
        self._body_key = _BODY_KEYS.get(self.tool)
        keys = {_PATH_KEY} | ({self._body_key} if self._body_key else set())
        self._fields = JsonStringFields(keys)

    def start(self, now: float) -> list[AgentEvent]:
        """블록이 열렸다. 무엇을 쓰는지 모르는 채로도 "쓰기 시작"은 바로 알린다."""
        return [self._event("writing", now)]

    def feed(self, partial_json: str, now: float) -> list[AgentEvent]:
        fresh = self._fields.feed(partial_json)
        if self._body_key and self._body_key in fresh:
            self.chars += len(fresh[self._body_key])
        announced = False
        if self.path is None and _PATH_KEY in self._fields.complete:
            self.path = self.to_rel(self._fields.values[_PATH_KEY])
            announced = True
        due = (self._last_emit is None or now - self._last_emit >= EMIT_INTERVAL_S)
        if announced or (due and self.chars != self._emitted_chars):
            return [self._event("writing", now)]
        return []

    def finish(self, now: float) -> list[AgentEvent]:
        """블록이 닫혔다. 입력이 완성됐다고 알린다."""
        return [self._event("written", now)]

    def _event(self, state: str, now: float) -> AgentEvent:
        self._last_emit = now
        self._emitted_chars = self.chars
        payload = {"id": self.tool_use_id, "tool": self.tool,
                   "state": state, "chars": self.chars}
        return AgentEvent(kind=DRAFT, path=self.path, payload=json.dumps(payload))
