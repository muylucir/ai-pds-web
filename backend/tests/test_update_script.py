# backend/tests/test_update_script.py — infra/scripts/aipds-update가 무엇을 "바뀜"으로 보는가.
#
# 백엔드 재시작은 진행 중인 Discovery 턴과 빌드를 끊고, 프론트 재빌드는 접속 중인 사용자에게
# 청크 404를 낸다. 그래서 테스트 파일만 바뀐 갱신은 둘 다 부르지 않아야 하고, 런타임 파일이
# 하나라도 바뀌면 반드시 불러야 한다. git pathspec(`:(exclude,glob)…`)은 손으로 쓰면 틀리기 쉬우므로
# 스크립트의 BACKEND_PATHS·FRONTEND_PATHS를 그대로 꺼내 실제 저장소의 두 커밋 사이에 돌린다.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "aipds-update"


def _arrays() -> str:
    """스크립트에서 두 배열 정의만 꺼낸다(나머지는 root·systemctl이 필요하다)."""
    text = SCRIPT.read_text(encoding="utf-8")
    found = re.findall(r"^(?:BACKEND|FRONTEND)_PATHS=\((?:[^()]|\([^()]*\))*\)", text, re.M)
    assert len(found) == 2, "aipds-update must define BACKEND_PATHS and FRONTEND_PATHS"
    return "\n".join(found)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for path in ("backend/aipds/app.py", "backend/tests/test_app.py", "backend/pyproject.toml",
                 "frontend/app/page.tsx", "frontend/components/Card.tsx",
                 "frontend/components/Card.test.tsx", "frontend/middleware.ts",
                 "frontend/middleware.test.ts", "frontend/test/msw/handlers.ts",
                 "frontend/e2e/projects.spec.ts", "frontend/vitest.config.ts",
                 "frontend/package.json", "README.md"):
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _changed(repo: Path, *edits: str) -> tuple[bool, bool]:
    """edits를 고친 커밋을 만들고 (백엔드 재시작?, 프론트 재빌드?)를 돌려준다."""
    before = _git(repo, "rev-parse", "HEAD")
    for path in edits:
        (repo / path).write_text("v2\n")
    _git(repo, "commit", "-qam", "edit")
    after = _git(repo, "rev-parse", "HEAD")
    script = f"""{_arrays()}
changed() {{ ! git -C "{repo}" diff --quiet {before} {after} -- "$@"; }}
changed "${{BACKEND_PATHS[@]}}" && echo backend
changed "${{FRONTEND_PATHS[@]}}" && echo frontend
true"""
    out = subprocess.run(["bash", "-s"], input=script, check=True, capture_output=True,
                         text=True).stdout
    return "backend" in out, "frontend" in out


@pytest.mark.parametrize("edits", [
    ("backend/tests/test_app.py",),
    ("frontend/components/Card.test.tsx", "frontend/middleware.test.ts"),
    ("frontend/test/msw/handlers.ts", "frontend/e2e/projects.spec.ts", "frontend/vitest.config.ts"),
    ("README.md",),
])
def test_test_only_updates_restart_nothing(repo, edits):
    assert _changed(repo, *edits) == (False, False)


@pytest.mark.parametrize("edits, expected", [
    (("backend/aipds/app.py",), (True, False)),
    (("backend/pyproject.toml", "backend/tests/test_app.py"), (True, False)),
    (("frontend/components/Card.tsx",), (False, True)),
    # 루트의 런타임 파일 — `**/*.test.ts` 제외가 이것까지 삼키면 안 된다.
    (("frontend/middleware.ts",), (False, True)),
    (("frontend/package.json", "frontend/components/Card.test.tsx"), (False, True)),
])
def test_runtime_changes_still_restart(repo, edits, expected):
    assert _changed(repo, *edits) == expected


# ---- 시험 브랜치(개발자용 모드) ----
#
# `aipds-update <브랜치>`는 문서에 적지 않는 모드라 안전장치가 스크립트 안에만 있다. 판정 두
# 함수는 부작용이 없게 분리돼 있고, 스크립트를 source하면 그 둘만 정의되고 멈춘다.

def _source(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {snippet}'],
                          capture_output=True, text=True)


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_sourcing_defines_the_planners_and_runs_nothing_else():
    """source가 root 검사·git·systemctl까지 내려가면 테스트가 운영 절차를 돌리게 된다."""
    r = _source("type -t valid_branch plan_switch")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["function", "function"]
    assert "run as root" not in r.stderr


@pytest.mark.parametrize("name", [
    "main", "handoff-package", "feat/handoff", "release-1.2", "user.name/x_y"])
def test_ordinary_branch_names_are_allowed(name):
    assert _source(f"valid_branch '{name}'").returncode == 0


@pytest.mark.parametrize("name", [
    "", "-h", "--help", "a b", "x;id", "$(id)", "`id`", "a'b", "a..b", "x.lock", "x/", "@",
    "../main", "a//b"])
def test_dangerous_or_invalid_branch_names_are_refused(name):
    # 값은 셸을 거치지 않게 위치 인자로 넘긴다 — 테스트 자신이 주입 경로가 되면 안 된다.
    r = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; valid_branch "$1"', "_", name],
                       capture_output=True, text=True)
    assert r.returncode != 0, name


def _plan(deploy: str, current: str, target: str, recorded: str, head: str) -> dict[str, str]:
    r = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; plan_switch "$@"', "_",
         deploy, current, target, recorded, head],
        capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in r.stdout.splitlines())


def test_a_plain_update_on_the_deploy_branch_is_unchanged():
    assert _plan("main", "main", "main", "", "aaa") == {
        "mode": "normal", "self_update": "yes", "record": ""}


def test_entering_a_test_branch_records_where_it_left_and_does_not_self_update():
    """시험 브랜치의 aipds-update는 옛 것일 수 있다(그 브랜치가 이 기능보다 먼저 갈라졌다면).
    그것을 깔면 돌아오는 일을 옛 스크립트가 한다."""
    assert _plan("main", "main", "handoff-package", "", "aaa") == {
        "mode": "enter", "self_update": "no", "record": "aaa"}


def test_moving_between_test_branches_keeps_the_original_commit():
    assert _plan("main", "handoff-package", "other", "aaa", "bbb") == {
        "mode": "switch", "self_update": "no", "record": "aaa"}


def test_going_back_clears_the_record_and_self_updates_again():
    assert _plan("main", "handoff-package", "main", "aaa", "bbb") == {
        "mode": "leave", "self_update": "yes", "record": ""}


def test_going_back_from_an_unrecorded_branch_is_still_a_return():
    """기록이 없어도(손으로 옮겨 둔 트리) 배포 브랜치가 아니면 돌아오는 것으로 알린다."""
    assert _plan("main", "(detached)", "main", "", "bbb")["mode"] == "leave"


def test_self_update_is_gated_by_the_plan():
    text = SCRIPT.read_text(encoding="utf-8")
    gate = text.index('if [ "$SELF_UPDATE" = yes ] && changed infra/scripts/aipds-update; then')
    install = text.index('install -m 755 "$APP/infra/scripts/aipds-update" /usr/local/bin/aipds-update')
    assert gate < install < text.index("fi", gate)


# ---- 시험 브랜치: 본체 흐름을 실제 git 저장소에서 ----
#
# root가 필요한 명령(id -u, runuser, install, systemctl)은 PATH의 가짜로 바꾸고, 스크립트
# 사본에서 /etc·/var/lib·/usr/local/bin 경로만 임시 디렉터리로 옮긴다. 나머지(git 전환,
# 기록, 출력, 자기 갱신 게이트)는 운영과 같은 코드가 돈다.

_SHIMS = {
    "id": '[ "$1" = "-u" ] && { echo 0; exit 0; }; exec /usr/bin/id "$@"',
    "runuser": 'while [ "$1" != "--" ]; do shift; done; shift; exec "$@"',
    "install": 'echo "install $*" >> "$SHIM_LOG"',
    "systemctl": 'echo "systemctl $*" >> "$SHIM_LOG"',
}


@pytest.fixture
def deployed(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True,
                   capture_output=True)
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "t")
    (seed / "infra" / "scripts").mkdir(parents=True)
    (seed / "infra" / "scripts" / "aipds-update").write_text("# main's script\n")
    (seed / "README.md").write_text("v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    # 시험 브랜치: 이 기능보다 먼저 갈라진 브랜치처럼 aipds-update가 다르다.
    _git(seed, "checkout", "-qb", "feature")
    (seed / "infra" / "scripts" / "aipds-update").write_text("# feature's older script\n")
    (seed / "README.md").write_text("feature\n")
    _git(seed, "commit", "-qam", "feature work")
    _git(seed, "push", "-q", "origin", "feature")
    _git(seed, "checkout", "-q", "main")

    app = tmp_path / "app"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(app)], check=True,
                   capture_output=True)
    env_file = tmp_path / "deploy.env"
    env_file.write_text(f"APP={app}\nSVC=nobody-unused\nBRANCH=main\n")
    state = tmp_path / "state"
    script = tmp_path / "aipds-update"
    script.write_text(SCRIPT.read_text(encoding="utf-8")
                      .replace("/etc/aipds-deploy.env", str(env_file))
                      .replace("/var/lib/aipds-update", str(state))
                      .replace("/usr/local/bin/aipds-update", str(tmp_path / "installed")))
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in _SHIMS.items():
        (shims / name).write_text(f"#!/bin/bash\n{body}\n")
        (shims / name).chmod(0o755)
    log = tmp_path / "shim.log"
    log.write_text("")

    def run(*args: str) -> subprocess.CompletedProcess:
        log.write_text("")
        env = {"PATH": f"{shims}:/usr/bin:/bin", "SHIM_LOG": str(log), "HOME": str(tmp_path)}
        return subprocess.run(["bash", str(script), *args], capture_output=True, text=True,
                              env=env)

    return {"run": run, "seed": seed, "app": app, "state": state / "pre-test-commit",
            "log": log}


def test_enter_a_test_branch_then_come_back_to_a_moved_main(deployed):
    run, seed, app = deployed["run"], deployed["seed"], deployed["app"]
    start = _git(app, "rev-parse", "HEAD")

    r = run("feature")
    assert r.returncode == 0, r.stderr
    assert "aipds-update: branch main -> feature" in r.stdout
    assert f"entering test branch 'feature' from main at {start}" in r.stdout
    assert r.stdout.rstrip().endswith("Run 'sudo aipds-update' to go back.")
    assert _git(app, "rev-parse", "HEAD") == _git(seed, "rev-parse", "origin/feature")
    assert deployed["state"].read_text().strip() == start
    # 시험 브랜치의 (옛) 스크립트를 깔지 않는다.
    assert "aipds-update" not in deployed["log"].read_text()

    # 시험하는 사이 main이 움직였다.
    (seed / "README.md").write_text("v2\n")
    (seed / "infra" / "scripts" / "aipds-update").write_text("# main's newer script\n")
    _git(seed, "commit", "-qam", "main moved")
    _git(seed, "push", "-q", "origin", "main")

    r = run()
    assert r.returncode == 0, r.stderr
    assert "aipds-update: branch feature -> main" in r.stdout
    assert "leaving test branch 'feature' — back to 'main'" in r.stdout
    assert f"before testing it was at {start}" in r.stdout
    assert "leaving these commits:" in r.stdout and "feature work" in r.stdout
    assert "TEST BRANCH" not in r.stdout
    assert _git(app, "rev-parse", "HEAD") == _git(seed, "rev-parse", "main")
    assert not deployed["state"].exists()
    # 돌아올 때는 배포 브랜치의 스크립트를 깐다.
    assert "infra/scripts/aipds-update" in deployed["log"].read_text()


def test_an_unpushed_branch_stops_before_touching_the_tree(deployed):
    app = deployed["app"]
    before = _git(app, "rev-parse", "HEAD")
    r = deployed["run"]("not-pushed")
    assert r.returncode == 1
    assert "origin/not-pushed not found — push the branch first" in r.stderr
    assert _git(app, "rev-parse", "HEAD") == before
    assert not deployed["state"].exists()


def test_a_refused_branch_name_stops_before_git(deployed):
    r = deployed["run"]("x;id")
    assert r.returncode == 2
    assert "not an allowed branch name" in r.stderr


def test_a_plain_update_at_the_same_commit_says_nothing_changed(deployed):
    r = deployed["run"]()
    assert r.returncode == 0, r.stderr
    assert "already at" in r.stdout and "TEST BRANCH" not in r.stdout
