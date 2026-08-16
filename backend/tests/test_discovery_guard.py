# backend/tests/test_discovery_guard.py — Discovery의 쓰기 범위 게이트.
#
# **왜 이 게이트가 생겼는가(2026-08-16의 결함).** Discovery 에이전트가 워크스페이스에
# 프로토타입을 `prototype/index.html`로 만들어 버렸다. 빌드·호스팅은 Prototypes
# 탭의 일이고 Discovery의 역할은 스펙 작성에서 끝나는데(discovery-config/CLAUDE.md),
# 그 규칙이 **산문뿐**이었고 그 산문이 금지한 것은 빌드 *명령*이었다:
# npm install / npm run dev / 서브프로세스 / 포트 선택. 자기완결 HTML 한 장은
# 그중 아무것도 필요하지 않다 — 에이전트의 자기 보고("API 키·패키지 설치·외부
# 통신이 모두 불필요합니다")가 곧 모든 조항을 만족했다는 증거다.
#
# 강제 수단이 없었던 이유: Discovery는 `bypassPermissions`로 돈다
# (claude_driver.py의 DEFAULT_PERMISSION_MODE). SDK가 이 상황을 직접 경고하며
# 해법까지 지정한다(claude_agent_sdk/types.py의 _get_can_use_tool_shadowed_warning):
# "can_use_tool will not be invoked ... To gate every tool call, use a PreToolUse
# hook instead." 이 모듈이 그 훅의 판정부다.
#
# 같은 실패가 리포에서 두 번째다 — test_proto_session.py의
# test_first_prompt_forbids_writing_files_before_approval이 빌더에서 같은 모양을
# 기록했고, 그때 택한 완화책도 프롬프트 문구였다. 그래서 이번에는 코드로 막는다.
#
# 판정 함수는 언어 중립이다: 거부 대상(경로/명령 조각)만 돌려주고 문구는
# agent/prompts.py가 두 벌로 소유한다(그 파일 헤더의 규약).
from __future__ import annotations

from pathfinder.agent.discovery_guard import bash_denial, write_denial

WS = "/ws"


# ---- Write/Edit/MultiEdit: aiplc-docs/ 안만 허용 ----

def test_writing_under_aiplc_docs_is_allowed():
    assert write_denial("/ws/aiplc-docs/discovery/discovery-document.md", WS) is None
    assert write_denial("aiplc-docs/audit.md", WS) is None


def test_the_slugged_prototype_spec_is_allowed():
    """이 게이트와 슬러그 규약의 경계가 맞물린다는 확인.

    Prototypes 탭이 카드를 만드는 유일한 경로(routes/prototypes.py의 _SPEC_RE)는
    aiplc-docs/ 안이므로 통과해야 한다 — 게이트가 이것을 막으면 슬러그 산출물을
    아예 쓸 수 없게 되어 문제가 뒤바뀐다.
    """
    path = "aiplc-docs/discovery/prototypes/maint-support/PROTOTYPE-maint-support.md"
    assert write_denial(path, WS) is None


def test_the_html_that_caused_this_gate_is_denied():
    """이 결함의 실제 산출물. 거부 대상으로 상대 경로를 돌려준다."""
    assert write_denial("/ws/prototype/index.html", WS) == "prototype/index.html"
    assert write_denial("prototype/app.js", WS) == "prototype/app.js"


def test_any_path_outside_aiplc_docs_is_denied():
    # 확장자를 보지 않는다 — .md라도 aiplc-docs/ 밖이면 산출물이 아니다.
    assert write_denial("notes.md", WS) == "notes.md"
    assert write_denial("src/main.py", WS) == "src/main.py"


def test_a_lookalike_prefix_does_not_slip_through():
    """`aiplc-docs`로 시작하는 **다른** 디렉터리는 밖이다. 접두사 문자열
    비교로 구현하면 여기가 통과한다."""
    assert write_denial("aiplc-docs-backup/x.md", WS) == "aiplc-docs-backup/x.md"


def test_workspace_escapes_are_denied():
    """탈출은 워크스페이스 밖이므로 당연히 거부다. pathsafe.workspace_relative가
    None을 주는 경로이고, 그때는 원문을 그대로 거부 대상으로 보고한다."""
    assert write_denial("/etc/passwd", WS) == "/etc/passwd"
    assert write_denial("../outside.md", WS) == "../outside.md"


def test_a_missing_path_is_not_denied():
    """file_path가 없는 호출은 이 게이트의 관심사가 아니다 — 판단 근거가 없는데
    거부하면 알 수 없는 도구 모양 하나가 턴을 막는다."""
    assert write_denial("", WS) is None
    assert write_denial(None, WS) is None


# ---- Bash: 빌드·서버·워크스페이스 밖 리다이렉션 거부 ----

def test_package_managers_are_denied_outright():
    """Discovery는 마크다운을 쓴다 — 패키지 매니저가 필요한 일이 없다.
    하위 명령을 열거하지 않고 호출 자체를 막는 이유는, 열거가 곧 빠진 항목을
    초대한다는 것이 이 결함의 교훈이기 때문이다."""
    for cmd in ("npm install", "npm run dev", "npm ci", "npx create-next-app x",
                "pnpm build", "yarn dev", "bun install"):
        assert bash_denial(cmd) is not None, cmd


def test_dev_servers_are_denied():
    for cmd in ("python3 -m http.server 8000",
                "cd prototype && python3 -m http.server 8000",
                "python -m SimpleHTTPServer",
                "uvicorn app:app", "gunicorn app:app", "flask run",
                "npx serve .", "php -S localhost:8000", "vite"):
        assert bash_denial(cmd) is not None, cmd


def test_redirection_outside_aiplc_docs_is_denied():
    """Write를 막아도 Bash 한 줄이 게이트를 우회한다 — 같은 경계를 여기서도 본다."""
    assert bash_denial("echo '<html>' > prototype/index.html") is not None
    assert bash_denial("cat <<EOF >> app.js") is not None
    assert bash_denial("echo x | tee prototype/style.css") is not None


def test_redirection_into_aiplc_docs_is_allowed():
    """산출물을 셸로 쓰는 것 자체는 막을 이유가 없다."""
    assert bash_denial("echo '# note' > aiplc-docs/audit.md") is None
    assert bash_denial("echo x >> aiplc-docs/discovery/notes.md") is None


def test_fd_redirections_are_not_mistaken_for_files():
    """`2>&1`은 파일이 아니라 fd 복제다. 이것을 파일로 읽으면 흔한 관용구가
    전부 막히고, 그러면 게이트를 끄자는 압력이 생긴다."""
    assert bash_denial("grep -r foo aiplc-docs/ 2>&1") is None
    assert bash_denial("ls -la 2>/dev/null") is None
    assert bash_denial("find . -name '*.md' 2>/dev/null | head") is None


def test_ordinary_read_only_commands_are_allowed():
    """게이트는 거부목록이다 — Discovery가 룰을 탐색하는 정상 경로를 막으면
    안 된다(허용목록으로 만들면 여기가 전부 막힌다)."""
    for cmd in ("ls aiplc-docs", "cat aiplc-docs/audit.md", "wc -l x.md",
                "grep -n Question aiplc-docs/a-questions.md",
                "find aiplc-docs -name '*-questions.md'", "pwd"):
        assert bash_denial(cmd) is None, cmd


def test_an_empty_command_is_not_denied():
    assert bash_denial("") is None
    assert bash_denial(None) is None


def test_the_denial_names_what_was_refused():
    """거부 이유가 모델에게 돌아가므로 무엇이 걸렸는지 지목해야 한다 — 그러지
    않으면 모델이 같은 명령을 형태만 바꿔 재시도하며 루프에 빠진다."""
    assert "npm" in bash_denial("npm run dev")
    assert "prototype/index.html" in bash_denial("echo x > prototype/index.html")
