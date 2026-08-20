# backend/aipds/tool_trace.py — 추론 과정 트레이스에 붙는 "무엇을 했는지".
#
# 화면에서 Write는 `📝 파일 변경: aiplc-docs/audit.md`로 보이는데(별도 `file_changed`
# 이벤트가 `path`를 들고 온다) Read/Bash는 `Read`, `Bash`만 보였다. 무엇을 읽었는지·
# 무슨 명령을 돌렸는지가 트레이스의 요점인데 그것이 빠져 있었다.
#
# **이 모듈이 단일 소유자인 이유.** 같은 값을 두 곳이 만든다 — 라이브
# (`agent/claude_driver._translate`)와 복원(`session_history`). 그 두 표현이
# 갈라지면 새로고침 전후로 화면이 달라진다. session_history의 해당 분기에
# "라이브의 status 이벤트와 같은 표현"이라는 주석이 이미 붙어 있고, 이 모듈이
# 그 주석을 코드로 바꾼 것이다.
#
# **라벨은 여기서 만들지 않는다.** 백엔드는 값만 준다("aiplc-docs/audit.md"),
# `🔍 Read · …`의 아이콘과 구분자는 프론트가 UI 언어로 그린다 — `file_changed`가
# 이미 그 규율이고(백엔드는 path만, "파일 변경"은 프론트) error_codes.py의
# "백엔드에 번역 시스템을 만들지 않는다"와 같은 판단이다.
from __future__ import annotations

#: 한 줄에 들어가야 한다. Bash 명령은 길이 제한이 없어서(실측: 수백 자) 자르지
#: 않으면 아코디언이 읽히지 않는다.
DETAIL_MAX = 120

#: 도구 이름 → 그 도구의 "무엇을" 담은 인자 키.
#:
#: **허용목록이다.** 모르는 도구의 인자를 아무렇게나 찍으면(예: 첫 값) 무엇이 의미
#: 있는 값인지 모르는 채로 내부 식별자가 화면에 새어 나온다.
#:
#: Write/Edit/MultiEdit이 없는 이유: 이미 `file_changed` 이벤트가 경로를 들고 오므로
#: 여기서 또 주면 같은 정보가 두 줄로 보인다. `mcp__pathfinder__*`도 없다 —
#: stage/document/build_complete 전용 이벤트가 구조화된 값을 이미 보낸다.
_DETAIL_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Bash": "command",
    "Glob": "pattern",
    "Grep": "pattern",
    "ToolSearch": "query",
    "WebFetch": "url",
}

#: 값이 경로인 도구 — 워크스페이스 아래 부분만 남긴다.
_PATH_TOOLS = frozenset({"Read"})

#: 워크스페이스 최상위 산출물 디렉터리. 절대 경로를 여기서부터 자른다.
#:
#: **워크스페이스 경로를 인자로 받지 않는 이유.** 이 함수는 라이브(워크스페이스를
#: 아는 드라이버)와 복원(트랜스크립트만 보는 session_history) 양쪽에서 불린다.
#: 한쪽만 워크스페이스를 알면 같은 호출이 두 표현을 만들고, 그것이 정확히 이
#: 모듈이 없애려는 갈라짐이다. 마커 기반은 양쪽에서 같은 값을 낸다.
#:
#: 목록은 runner._RESTORE_PREFIXES와 같은 자리를 가리킨다(그쪽이 S3↔로컬 동기화의
#: 최상위 프리픽스 목록이다).
_WORKSPACE_MARKERS = ("aiplc-docs/", "prototypes/", "prototype/", "uploads/")


def _shorten_path(value: str) -> str:
    for marker in _WORKSPACE_MARKERS:
        idx = value.find(marker)
        if idx > 0:
            return value[idx:]
    return value


def tool_detail(name: str, tool_input: object) -> str | None:
    """`name` 도구 호출에서 화면에 보일 한 줄. 보일 것이 없으면 None.

    `tool_input`은 **모델이 만든 값**이다 — 모양이 어긋나도 예외를 던지지 않는다.
    트레이스는 부수 정보이고, 그것 때문에 턴이 죽으면 안 된다.

    리댁션은 호출부의 책임이다: 라이브는 `routes/turns._redacted`가, 복원은
    `session_history`가 `redact_credentials`를 통과시킨다. 그래서 이 값은 반드시
    `text`나 `payload`로 실려야 한다 — `path`는 구조적 필드로 취급되어 리댁션을
    지나지 않고, Bash 명령은 자격증명이 나타나는 대표적인 자리다.
    """
    key = _DETAIL_KEYS.get(name)
    if key is None or not isinstance(tool_input, dict):
        return None
    raw = tool_input.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if name in _PATH_TOOLS:
        # 절대 경로를 그대로 찍으면 `/opt/aipds/workspaces/{pid}/`가 붙는다 —
        # 사용자에게 의미 없고 프로젝트 id가 트레이스에 새어 나온다. 워크스페이스
        # 밖의 경로는 손대지 않는다(그 자체가 신호다).
        value = _shorten_path(value)
    if len(value) > DETAIL_MAX:
        value = value[:DETAIL_MAX] + "…"
    return value
