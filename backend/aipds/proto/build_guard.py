# backend/aipds/proto/build_guard.py — 빌드 에이전트의 Bash 판정부.
#
# **왜 이 파일이 생겼는가(2026-08-01의 사고).** 빌드 에이전트가 브라우저 검증을 위해
# Playwright chromium을 띄웠고, 그 검증이 포트 3000을 겨냥해 AI-PDS 프론트엔드가
# SIGKILL로 죽었다. 백엔드·프론트엔드가 빌드 에이전트와 **같은 유저(`aipds`)로
# 도므로** 신호를 막을 것이 없었고, 워크숍 참가자 화면에는 "연결이 끊겼다"가 떴다.
#
# 그때의 완화책 둘은 모두 코드가 아니었다. `skills=["shadcn-design"]`(builder.py)은
# 스스로 적어 뒀듯 **컨텍스트 필터이지 샌드박스가 아니고**, 나머지 하나는
# `proto-config/CLAUDE.md`의 산문이다. 그 산문은 금지하면서 우회 레시피까지
# 가르쳤다("If you really must start a server": `setsid npm run start ...`). 이
# 모듈이 그 자리를 코드로 대체한다.
#
# 강제 수단이 훅인 이유는 SDK가 직접 설명한다. 빌드는 `bypassPermissions`로 돌고
# (builder.DEFAULT_PERMISSION_MODE) claude_agent_sdk/types.py의
# _get_can_use_tool_shadowed_warning이 이렇게 말한다: "To gate every tool call, use a
# PreToolUse hook instead." Discovery가 이미 같은 배선으로 돌고 있다
# (claude_driver.py의 hooks 주석 — "PreToolUse가 이 제품의 유일한 실효 게이트다").
#
# **언어 중립이다.** 거부 대상 조각만 돌려주고 문구는 proto/prompts.py가 두 벌로
# 소유한다 — 모델이 읽는 텍스트는 프로젝트 언어여야 한다는 그 파일 헤더의 규약이고,
# 여기에 한국어를 넣으면 영어 프로젝트로 샌다.
#
# **거부목록이지 허용목록이 아니다.** 빌드 에이전트는 파일을 읽고 쓰고 `npm run
# build`를 돌리는 정상 경로가 넓다. 허용목록으로 만들면 그쪽이 전부 막히고, 막힌
# 빌드는 게이트를 끄자는 압력이 된다 — 오탐이 게이트의 수명을 줄인다는 것이
# discovery_guard가 같은 자리에 적어 둔 교훈이다.
#
# **봉인이 아니라 좁히기다.** Bash는 임의 코드 실행이므로 거부목록으로 모든 우회를
# 막을 수 없다(`node -e`로 브라우저를 띄우는 것). 여기서 막는 것은 **관측된 경로와
# 그 인접군**이다. 근본 격리는 빌드 에이전트를 별도 유저로 분리하는 것이고
# (builder.py의 skills 주석이 그것을 "별건"으로 남겨 뒀다) 이 게이트는 그것을
# 대체하지 않는다.
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import PurePosixPath
from typing import Collection
from urllib.parse import urlsplit

from aipds.agent_home import SETTINGS_IN_COMMAND

#: 인용부호 안의 내용. 판정 전에 지운다 — `echo "npm run dev는 금지"`처럼 규약을
#: 적거나 로그를 남기는 정상 명령이 막히면 오탐이다(discovery_guard._QUOTED와 같은
#: 이유이며 같은 위험).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: 브라우저 자동화. 2026-08-01 사고의 직접 원인이며, 화면 확인은 프로토타입 탭의
#: 라이브 프리뷰가 하는 일이므로 빌드 에이전트에게는 정당한 용도가 없다.
#:
#: `test:e2e`가 목록에 있는 이유: 리포의 `playwright.config.ts`가 **포트 3000을
#: 겨냥하므로** 이 스크립트 하나로 사고가 그대로 재현된다.
_BROWSER = re.compile(
    r"\b(playwright|puppeteer|chrome-headless|chromium(?:-browser)?"
    r"|test:e2e)\b")

#: dev·production 서버. `npm run start`는 hosting의 일이고(proto/host.py의
#: `start()`), 빌드 에이전트가 띄우면 턴이 끝나도 포트를 물고 남는다 — 그 정리
#: 명령이 2026-08-01의 kill이었다. 빌드 검증은 `npm run build`로 충분하다.
#:
#: `(?:run\s+)?`가 선택인 이유: `pnpm dev`·`yarn start`는 `run`이 없다. 두 번째
#: 분기는 `npm run`을 우회해 프레임워크 바이너리를 직접 부르는 형태다.
_SERVER = re.compile(
    r"\b(?:npm|npx|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b"
    r"|\bnext\s+(?:dev|start)\b")

#: 내가 시작하지 않은 프로세스의 종료.
#:
#: 맨 `kill <pid>`는 **막지 않는다**: 위 `_SERVER`가 서버 기동을 이미 막으므로 죽일
#: 자기 프로세스가 없고, 넓게 막으면 오탐만 늘어난다. 막는 것은 대상을 스스로
#: 찾아내는 형태다 — `pkill`/`killall`(패턴 매칭), `fuser -k`(포트 점유자),
#: 그리고 명령 치환으로 PID를 얻는 `kill -9 $(lsof -ti:...)`(사고 당시의 모양).
#:
#: `\bkill\b`가 `pkill`을 잡지 않는다: `p`와 `k`가 모두 단어 문자라 그 사이에
#: 경계가 없다.
_KILL = re.compile(
    r"\b(?:pkill|killall)\b"
    r"|\bfuser\b[^;|&]*\s-k\b"
    r"|\bkill\b[^;|&]*\$\(")

#: AI-PDS 자신의 포트. 3000은 프론트엔드, 8000은 백엔드다.
#:
#: **hosting이 배정하는 범위와 겹치지 않는다.** `_scan_port`가 쓰는 것은
#: `range(4000, 8000)`이므로(proto/host.py) 3000·8000은 어느 프로토타입에도
#: 배정되지 않는다 — 두 포트를 막아도 정상 프로토타입 포트와 충돌하지 않는다.
#:
#: 숫자 경계를 lookaround로 잡는다. `\b`로는 `13000`·`80000`이 함께 걸리고, 그
#: 오탐은 무해한 명령을 막는다.
_PORT = re.compile(r"(?<![0-9])(?:3000|8000)(?![0-9])")

#: CLI 설정 파일(agent_home.SETTINGS_IN_COMMAND). 빌드 에이전트가 만질 이유가 없다.
#: 읽기(`cat`)도 함께 걸리지만 그것도 정당한 용도가 없다.
_SETTINGS = SETTINGS_IN_COMMAND

#: 판정 순서. 먼저 걸린 것을 지목한다 — 한 명령이 여러 조항을 어길 수 있고
#: (`fuser -k 3000/tcp`는 종료와 포트 둘 다), 그때 무엇을 이름으로 부를지는
#: 결정적이어야 한다(테스트가 그 순서를 고정한다).
_PATTERNS = (_BROWSER, _SERVER, _KILL, _PORT, _SETTINGS)


def bash_denial(command: str | None) -> str | None:
    """브라우저 자동화·서버 기동·타 프로세스 종료·AI-PDS 포트면 거부 대상
    조각을, 아니면 None.

    문자열이 아닌 입력과 빈 명령은 **허용**한다. 판단 근거가 없는데 거부하면
    우리가 모르는 호출 모양 하나가 빌드 전체를 막는다 — 이 게이트의 실패 방향은
    "통과"여야 한다.
    """
    if not command or not isinstance(command, str):
        return None
    scrubbed = _QUOTED.sub(" ", command)
    for pattern in _PATTERNS:
        match = pattern.search(scrubbed)
        if match:
            return match.group(0).strip()
    return None


#: Write 계열 도구. 빌드 에이전트는 작업 디렉터리 어디든 쓴다 — 막는 것은 CLI 설정뿐이다.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

#: CLI가 설정으로 읽는 파일 이름. `.claude/` 아래의 `settings*.json`은 프로젝트 설정,
#: 작업 디렉터리 밖의 `settings*.json`은 config dir(사용자 설정)이다.
_SETTINGS_NAMES = frozenset({".mcp.json", ".claude.json"})


def settings_write_denial(path: str | None, workspace: str) -> str | None:
    """CLI 설정 파일이면 그 경로를, 아니면 None. 경로를 알 수 없으면 통과한다."""
    if not path or not isinstance(path, str):
        return None
    p = PurePosixPath(path)
    if p.name in _SETTINGS_NAMES:
        return path
    if p.name.startswith("settings") and p.suffix == ".json":
        inside = not p.is_absolute() or p.is_relative_to(workspace)
        if p.parent.name == ".claude" or not inside:
            return path
    return None


#: WebFetch로 열 수 있는 호스트(와 그 하위 도메인). 빌드 스킬이 가리키는 문서다
#: (proto-config/skills/shadcn-design: 컴포넌트·블록은 ui.shadcn.com, AI 채팅 요소는
#: ai-sdk.dev). 그 밖으로 나가는 WebFetch는 URL에 워크스페이스 내용을 실어 내보내는
#: 길이고, 빌드에 필요한 것은 위 문서와 npm(Bash)뿐이다.
FETCH_HOSTS = ("ui.shadcn.com", "ai-sdk.dev")

#: 사용자가 쓴 말에서 URL로 읽는 모양. 스킴이 있어야 한다 — `store.example.com/x`처럼
#: 스킴 없는 조각까지 잡으면 파일 경로·버전 문자열이 호스트로 둔갑한다. 끝의 닫는
#: 괄호·따옴표는 URL이 아니라 문장의 일부다(`(https://a.com)`).
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.IGNORECASE)

#: 이름만으로 내부를 가리키는 접미사. 사용자가 쓴 URL이라도 이 서버 안쪽(백엔드·다른
#: 프로젝트의 프리뷰 포트)을 여는 길이 되면 안 된다.
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal")


def _internal_host(host: str) -> bool:
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # `127.1`·`2130706433`·`0x7f.1`도 IPv4다 — WebFetch 쪽 URL 파서는 이것들을
        # 127.0.0.1로 읽는데 `ip_address`는 이름으로 본다. `inet_aton`이 같은 축약을 읽는다.
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False  # 이름이다 — 해석 결과까지는 보지 않는다(Bash로도 닿는 범위다)
    return not ip.is_global


def user_url_hosts(text: str | None) -> set[str]:
    """사용자가 **직접 쓴** 말 속 http(s) URL의 호스트. 내부 주소는 뺀다.

    WebFetch를 넓히는 유일한 근거다: 사용자가 이 사이트를 보라고 했다. 그래서 입력은
    사용자 원문이어야 한다 — 서버가 감싼 개시 프롬프트에는 모델이 쓴 handoff 요약과
    명세 경로가 섞여 있고, 거기서 뽑으면 모델이 적어 둔 URL이 다음 세션의 허용이 된다.
    """
    hosts: set[str] = set()
    for match in _URL_IN_TEXT.finditer(text or ""):
        try:
            host = (urlsplit(match.group(0)).hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if host and not _internal_host(host):
            hosts.add(host)
    return hosts


def fetch_denial(url: str | None,
                 user_hosts: Collection[str] = ()) -> str | None:
    """허용 호스트 밖이면 그 호스트(또는 URL)를, 아니면 None.

    허용은 문서 호스트(`FETCH_HOSTS`, 하위 도메인까지)와 `user_hosts` — 이 세션에서
    사용자가 쓴 URL의 호스트(`user_url_hosts`) — 다. 사용자 호스트는 **정확히 그
    호스트만**이다: 하위 도메인까지 열면 `https://co.kr` 한 줄이 `*.co.kr` 전부가 된다.

    URL이 없거나 문자열이 아니면 **거부**한다 — 이 도구의 입력은 URL 하나이고, 알 수
    없는 모양을 통과시키면 그것이 우회로다(Bash 판정과 실패 방향이 다른 이유).
    """
    if not url or not isinstance(url, str):
        return str(url)
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return url
    if any(host == h or host.endswith("." + h) for h in FETCH_HOSTS):
        return None
    if host in user_hosts:
        return None
    return host
