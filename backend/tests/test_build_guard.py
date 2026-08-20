# backend/tests/test_build_guard.py — 빌드 에이전트의 Bash 게이트.
#
# **왜 이 게이트가 생겼는가(2026-08-01의 사고).** 빌드 에이전트가 브라우저 검증을
# 위해 Playwright chromium을 띄웠고, 그 검증이 포트 3000을 겨냥해 Pathfinder
# 프론트엔드가 SIGKILL로 죽었다(journalctl status=9/KILL, 코어덤프의
# Unit=pathfinder-backend.service). 백엔드와 프론트엔드가 빌드 에이전트와 **같은
# 유저(`pathfinder`)로 도므로** 막을 것이 없었고, 워크숍 참가자 화면에는 "연결이
# 끊겼다"가 떴다.
#
# 그때의 완화책은 두 가지였고 **둘 다 코드가 아니었다**:
#   1. `skills=["shadcn-design"]`로 좁히기 — CLI 번들 스킬 `run`("Launch and drive
#      this project's app... browser-driven")이 `skills="all"`로 함께 켜졌던 것이
#      원인이었다. 그러나 builder.py의 그 주석이 스스로 적어 뒀듯 이것은
#      **컨텍스트 필터이지 샌드박스가 아니다** — 스킬을 숨길 뿐 Bash는 그대로다.
#   2. `proto-config/CLAUDE.md`의 "Processes and ports" 절 — **산문뿐이다.**
#
# 그래서 Bash를 실제로 막는 것이 지금까지 없었다. 이 모듈이 그것이다.
#
# 리포에서 같은 모양의 세 번째다: test_discovery_guard.py가 Discovery에서(자기완결
# HTML 한 장), test_proto_session.py의
# test_first_prompt_forbids_writing_files_before_approval이 빌더에서 같은 실패를
# 기록했고 둘 다 처음엔 문구로 막으려 했다. 세 번째는 코드로 시작한다.
#
# 판정 함수는 **언어 중립**이다: 거부 대상 조각만 돌려주고 문구는
# proto/prompts.py가 두 벌로 소유한다(그 파일 헤더의 규약 — 여기에 한국어를 넣으면
# 영어 프로젝트로 샌다).
from __future__ import annotations

from aipds.proto.build_guard import bash_denial


# ---- 빌드의 정상 경로는 통과해야 한다 ----
#
# 오탐이 이 게이트의 최대 위험이다. 빌드가 막히면 게이트를 끄자는 압력이 생기고,
# 그러면 사고를 막는 것이 다시 산문으로 돌아간다(discovery_guard의 같은 주석).

def test_the_build_itself_is_allowed():
    """`npm run build`가 검증 수단이다 — 이것이 막히면 빌드가 불가능하다."""
    assert bash_denial("npm run build") is None


def test_installing_dependencies_is_allowed():
    assert bash_denial("npm install") is None
    assert bash_denial("npm ci") is None


def test_ordinary_inspection_is_allowed():
    assert bash_denial("ls -la app") is None
    assert bash_denial("cat package.json") is None
    assert bash_denial("npm run lint") is None
    assert bash_denial("npx tsc --noEmit") is None


def test_a_command_mentioned_inside_quotes_is_not_a_command():
    """인용부호 안은 판정 전에 지운다.

    `echo`로 규약을 적거나 로그를 남기는 정상 명령이 막히면 오탐이고, 오탐은
    게이트의 수명을 줄인다(discovery_guard._QUOTED와 같은 이유).
    """
    assert bash_denial('echo "never run npm run dev"') is None
    assert bash_denial("echo 'playwright is forbidden'") is None


def test_no_command_is_allowed():
    """판단 근거가 없으면 통과다 — 이 게이트의 실패 방향은 '통과'여야 한다."""
    assert bash_denial(None) is None
    assert bash_denial("") is None
    assert bash_denial({"not": "a string"}) is None


# ---- 브라우저 자동화: 2026-08-01 사고의 직접 원인 ----

def test_playwright_is_denied():
    assert "playwright" in bash_denial("npx playwright test")


def test_the_e2e_script_is_denied():
    """`playwright.config.ts`가 포트 3000을 겨냥하므로 사고를 그대로 재현한다."""
    assert bash_denial("npm run test:e2e") is not None


def test_puppeteer_is_denied():
    assert "puppeteer" in bash_denial("node scripts/shot.js --puppeteer")


def test_headless_chrome_is_denied():
    assert bash_denial("chrome-headless --screenshot") is not None
    assert bash_denial("chromium --headless https://localhost") is not None


# ---- dev·production 서버: 턴이 끝나도 포트를 물고 남는다 ----

def test_npm_run_dev_is_denied():
    assert bash_denial("npm run dev") is not None


def test_npm_run_start_is_denied():
    """`npm run start`는 hosting의 일이다(proto/host.py). 빌드 에이전트가 띄우면
    포트를 물고 남고, 그 정리가 2026-08-01의 kill로 이어졌다."""
    assert bash_denial("npm run start") is not None


def test_other_package_managers_are_denied_too():
    assert bash_denial("pnpm dev") is not None
    assert bash_denial("yarn start") is not None


def test_the_framework_binary_is_denied():
    """`npm run`을 우회해 프레임워크를 직접 부르는 형태."""
    assert bash_denial("npx next dev") is not None
    assert bash_denial("npx next start -p 4123") is not None


def test_wrapping_the_server_in_setsid_is_still_denied():
    """`proto-config/CLAUDE.md`가 가르쳤던 우회 레시피 그대로다 — 그 절을 지우는
    근거가 이 테스트다."""
    assert bash_denial("setsid npm run start > /tmp/smoke.log 2>&1 &") is not None


# ---- 프로세스 종료: 내가 시작하지 않은 것을 죽이지 않는다 ----

def test_pkill_is_denied():
    assert "pkill" in bash_denial("pkill -f node")


def test_killall_is_denied():
    assert "killall" in bash_denial("killall node")


def test_fuser_kill_is_denied():
    assert bash_denial("fuser -k 3000/tcp") is not None


def test_killing_a_pid_from_a_substitution_is_denied():
    """`kill -9 $(lsof -ti:3000)` — 사고 당시의 정리 명령 모양이다.

    맨 `kill <pid>`는 막지 않는다: 서버 기동 자체가 막히므로 죽일 자기 프로세스가
    없고, 넓게 막으면 오탐이 늘어난다.
    """
    assert bash_denial("kill -9 $(lsof -ti:3000)") is not None


def test_killing_a_pid_you_hold_is_allowed():
    assert bash_denial("kill 12345") is None


# ---- 포트 3000·8000: Pathfinder 자신이다 ----
#
# hosting이 배정하는 범위는 range(4000, 8000)이므로(proto/host.py의 _scan_port)
# 3000·8000은 어느 프로토타입에도 배정되지 않는다 — 막아도 충돌하지 않는다.

def test_probing_the_frontend_port_is_denied():
    assert bash_denial("lsof -ti:3000") is not None


def test_probing_the_backend_port_is_denied():
    assert bash_denial("curl -s http://localhost:8000/health") is not None


def test_binding_the_frontend_port_is_denied():
    assert bash_denial("PORT=3000 npm run build") is not None


def test_a_hosted_prototype_port_is_allowed():
    """배정 범위(4000-7999) 안의 포트는 정상이다 — 3000·8000만 막는다."""
    assert bash_denial("curl -s http://localhost:4123/") is None
    assert bash_denial("lsof -ti:7999") is None


def test_a_number_that_merely_contains_the_port_is_allowed():
    """`13000`·`80000`이 걸리면 오탐이다."""
    assert bash_denial("curl -s http://localhost:13000/") is None
    assert bash_denial("echo 80000 > /tmp/n") is None
