# backend/aipds/agent/discovery_guard.py — Discovery의 쓰기 범위 판정.
#
# **왜 이 파일이 생겼는가(2026-08-16의 결함).** Discovery 에이전트가 워크스페이스에
# 프로토타입을 `prototype/index.html`로 만들어 버렸다. 빌드·호스팅은 Prototypes
# 탭의 일이고 Discovery는 스펙 작성에서 끝나는데(discovery-config/CLAUDE.md의
# "Prototypes" 절), 그 규칙이 **산문뿐**이었고 그 산문이 금지한 것은 빌드
# *명령*이었다: npm install / npm run dev / 서브프로세스 시작 / 포트 선택.
# 자기완결 HTML 한 장은 그중 아무것도 필요하지 않다 — 에이전트가 남긴 자기 보고
# ("API 키·패키지 설치·외부 통신이 모두 불필요합니다")가 곧 열거된 모든 조항을
# 만족했다는 증거다. **열거는 빠진 항목을 초대한다.**
#
# 강제 수단이 없었던 이유는 SDK가 직접 설명해 준다. Discovery는
# `bypassPermissions`로 돌고(claude_driver.DEFAULT_PERMISSION_MODE),
# claude_agent_sdk/types.py의 _get_can_use_tool_shadowed_warning이 이렇게 말한다:
#
#   "can_use_tool will not be invoked: permission_mode 'bypassPermissions'
#    auto-approves every tool call ... To gate every tool call, use a
#    PreToolUse hook instead."
#
# 이 모듈은 그 훅의 판정부다. 배선은 claude_driver._on_pre_tool_use에 있다.
#
# **언어 중립이다.** 거부 대상(경로 또는 명령 조각)만 돌려주고 문구는
# agent/prompts.py가 두 벌로 소유한다 — 모델이 읽는 텍스트는 프로젝트 언어여야
# 한다는 그 파일 헤더의 규약이고, 여기에 한국어를 넣으면 영어 프로젝트로 샌다.
#
# **Bash는 봉인이 아니라 좁히기다.** 경로 기반인 Write/Edit 판정과 달리 Bash는
# 임의 코드 실행이므로 거부목록으로 모든 우회를 막을 수 없다(예: `python3 -c`로
# 파일을 여는 것). 여기서 막는 것은 관측된 경로와 그 인접군이다. 완전히 봉인하려면
# Discovery에서 Bash를 아예 빼야 하는데(룰 탐색은 Read/Glob/Grep으로 충분하다)
# 그것은 별도 판단이므로 하지 않았다.
from __future__ import annotations

import re
from pathlib import PurePosixPath

from aipds.pathsafe import workspace_relative

#: Discovery가 쓸 수 있는 유일한 루트. 산출물 정의 자체다(Workspace.list_artifacts가
#: 같은 서브트리를 프로젝트 산출물로 본다).
DOCS_ROOT = "aiplc-docs"

#: 이 게이트가 보는 파일 쓰기 도구. claude_driver._FILE_TOOLS와 같은 집합이어야
#: 한다 — 한쪽에만 도구가 추가되면 그 도구는 관측되면서 막히지 않는다.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def write_denial(path: str | None, workspace: str) -> str | None:
    """`aiplc-docs/` 밖이면 거부 대상 경로를, 허용이면 None.

    경로를 알 수 없는 호출(file_path 없음)은 **허용**한다. 판단 근거가 없는데
    거부하면 우리가 모르는 도구 모양 하나가 턴 전체를 막는다 — 이 게이트의
    실패 방향은 "통과"여야 하고, 관측은 PostToolUse가 이미 하고 있다.
    """
    if not path or not isinstance(path, str):
        return None
    rel = workspace_relative(path, workspace)
    if rel is None:
        # 워크스페이스 탈출. 상대화할 수 없으니 원문을 그대로 지목한다.
        return path
    # 접두사 **문자열** 비교가 아니라 세그먼트 비교여야 한다 —
    # "aiplc-docs-backup/x.md"는 startswith("aiplc-docs")를 통과한다.
    if PurePosixPath(rel).parts[:1] == (DOCS_ROOT,):
        return None
    return rel


#: 인용부호 안의 내용. 리다이렉션·명령 탐지 전에 지운다 — `echo "a > b"`의 `>`를
#: 리다이렉션으로 읽으면 문서를 쓰는 정상 명령이 막히고, 그러면 게이트를 끄자는
#: 압력이 생긴다(오탐은 게이트의 수명을 줄인다).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

#: 패키지 매니저. 하위 명령을 열거하지 않고 호출 자체를 막는다 — Discovery는
#: 마크다운을 쓰므로 패키지 매니저가 필요한 일이 없고, 열거가 빠진 항목을
#: 초대한다는 것이 이 결함의 교훈이다.
_PKG = re.compile(r"(?:^|[;&|(]\s*|\s)(npm|npx|pnpm|yarn|bun)\b")

#: 개발 서버·런타임 서빙. `npx serve`류는 위 _PKG가 이미 잡으므로 여기서 맨
#: `serve`를 넣지 않는다 — 넣으면 "serve"를 담은 파일명·본문이 전부 걸린다.
_SERVERS = re.compile(
    r"\b(http\.server|SimpleHTTPServer|uvicorn|gunicorn|flask\s+run"
    r"|php\s+-S|vite|next\s+(?:dev|start))\b")

#: 파일로 향하는 리다이렉션. `(?<![0-9&])`가 fd 형태(`2>`, `>&`)를 뺀다 —
#: `2>/dev/null`·`2>&1`은 관용구이고 파일 생성이 아니다.
_REDIR = re.compile(r"(?<![0-9&])>{1,2}\s*(?!&)([^\s;|&<>]+)")

#: tee도 파일을 만든다. 리다이렉션만 막으면 이쪽으로 그대로 나간다.
_TEE = re.compile(r"\btee\b\s+(?:-a\s+)?([^\s;|&<>]+)")

#: 파일이 아니라 장치인 대상. 리다이렉션 검사에서 제외한다.
_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr")


def bash_denial(command: str | None) -> str | None:
    """빌드·서버 기동·`aiplc-docs/` 밖 파일 생성이면 거부 대상 조각을, 아니면 None.

    거부**목록**이다(허용목록이 아니다). Discovery는 룰 파일을 `ls`/`grep`/`find`로
    탐색하는 정상 경로가 있고, 허용목록으로 만들면 그쪽이 전부 막힌다.
    """
    if not command or not isinstance(command, str):
        return None
    # 인용부호 안을 지운 사본으로 판정한다. 원문은 보고하지 않는다 —
    # 지목은 아래에서 잡은 조각으로 한다.
    scrubbed = _QUOTED.sub(" ", command)

    pkg = _PKG.search(scrubbed)
    if pkg:
        return pkg.group(1)

    server = _SERVERS.search(scrubbed)
    if server:
        return server.group(1)

    for pattern in (_REDIR, _TEE):
        for match in pattern.finditer(scrubbed):
            target = match.group(1)
            if target in _DEVICES:
                continue
            # 리다이렉션 대상은 워크스페이스 상대 경로로 온다(cwd가 워크스페이스다).
            if PurePosixPath(target).parts[:1] == (DOCS_ROOT,):
                continue
            return target
    return None
