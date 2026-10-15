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


# ---- nginx 바디 상한 맞추기 ------------------------------------------------------

def _nginx_step(conf: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    """스크립트의 nginx 단계만 꺼내 임시 conf에 돌린다. nginx·systemctl은 스텁이다."""
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^NGINX_CONF=.*?^fi$", text, re.M | re.S)
    assert m, "aipds-update must keep the nginx body-limit step"
    step = m.group(0).replace("NGINX_CONF=/etc/nginx/conf.d/aipds.conf",
                              f"NGINX_CONF={conf}", 1)
    return subprocess.run(["bash", "-euo", "pipefail", "-c", step], capture_output=True,
                          text=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin"})


def _stubs(tmp_path: Path, nginx_ok: bool) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for name, code in (("nginx", 0 if nginx_ok else 1), ("systemctl", 0)):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\nexit {code}\n')
        stub.chmod(0o755)
    return bin_dir


_CONF = ("server {\n  client_max_body_size 6m;\n"
         '  if ($http_x_origin_verify != "s3cr3t") { return 403; }\n}\n')


def test_nginx_step_raises_the_body_limit_and_keeps_the_rest(tmp_path):
    conf = tmp_path / "aipds.conf"
    conf.write_text(_CONF)
    r = _nginx_step(conf, _stubs(tmp_path, nginx_ok=True))
    assert r.returncode == 0, r.stderr
    assert conf.read_text() == _CONF.replace("6m;", "11m;")
    assert "systemctl reload nginx" in (tmp_path / "calls").read_text()
    assert not (tmp_path / "aipds.conf.prev").exists()


def test_nginx_step_is_a_no_op_when_already_right(tmp_path):
    conf = tmp_path / "aipds.conf"
    conf.write_text(_CONF.replace("6m;", "11m;"))
    r = _nginx_step(conf, _stubs(tmp_path, nginx_ok=True))
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "calls").exists()        # 검사도 reload도 하지 않았다


def test_nginx_step_restores_the_conf_when_nginx_rejects_it(tmp_path):
    conf = tmp_path / "aipds.conf"
    conf.write_text(_CONF)
    r = _nginx_step(conf, _stubs(tmp_path, nginx_ok=False))
    assert r.returncode == 0, r.stderr
    assert conf.read_text() == _CONF
    assert "reload" not in (tmp_path / "calls").read_text()
    assert "kept the previous conf" in r.stderr


# ---- 끝까지 가지 못한 갱신은 다음 실행이 다시 한다 -----------------------------

def _install_stubs(tmp_path: Path, pip_ok: bool) -> Path:
    """root·runuser·systemctl·install·pip·npm 없이 스크립트 전체를 돌리는 스텁."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls"
    stubs = {
        "id": 'echo 0',
        # `runuser -u SVC -- cmd…` → 작업 디렉터리를 바꾸지 않고 cmd를 그대로 돌린다.
        "runuser": 'shift 3; exec "$@"',
        "systemctl": f'echo "systemctl $*" >> "{log}"',
        "install": f'echo "install $*" >> "{log}"',
        "npm": f'echo "npm $*" >> "{log}"',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/bash\n{body}\n")
        stub.chmod(0o755)
    return bin_dir


def _deployed(tmp_path: Path):
    """origin(bare) + 그것을 추적하는 앱 트리 + 경로를 임시로 바꾼 스크립트 사본."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    for path in ("backend/pyproject.toml", "backend/aipds/app.py", "README.md"):
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text("v1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "v1")
    _git(work, "push", "-q", "origin", "HEAD:main")
    app = tmp_path / "app"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(app)], check=True)
    env_file = tmp_path / "deploy.env"
    env_file.write_text(f"APP={app}\nSVC=svc\nBRANCH=main\n")
    applied = tmp_path / "state" / "update-applied"
    script = tmp_path / "aipds-update"
    script.write_text(SCRIPT.read_text(encoding="utf-8")
                      .replace("ENV_FILE=/etc/aipds-deploy.env", f"ENV_FILE={env_file}")
                      .replace("/var/lib/aipds", str(applied.parent))
                      .replace("NGINX_CONF=/etc/nginx/conf.d/aipds.conf",
                               f"NGINX_CONF={tmp_path / 'absent.conf'}"))
    return work, app, script, applied


def _run_update(tmp_path: Path, app: Path, script: Path, pip_ok: bool):
    pip = app / "backend/.venv/bin/pip"
    pip.parent.mkdir(parents=True, exist_ok=True)
    pip.write_text(f'#!/bin/bash\necho "pip $*" >> "{tmp_path / "calls"}"\nexit {0 if pip_ok else 1}\n')
    pip.chmod(0o755)
    bin_dir = _install_stubs(tmp_path, pip_ok)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, cwd="/",
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)})


def test_an_update_that_failed_midway_is_redone_by_the_next_run(tmp_path):
    work, app, script, applied = _deployed(tmp_path)
    (work / "backend/pyproject.toml").write_text("v2\n")
    _git(work, "commit", "-qam", "deps")
    _git(work, "push", "-q", "origin", "HEAD:main")

    first = _run_update(tmp_path, app, script, pip_ok=False)
    assert first.returncode != 0
    assert not applied.exists()                  # 끝까지 가지 못했다 — 기록하지 않는다
    assert "restart aipds-backend" not in (tmp_path / "calls").read_text()

    # checkout은 이미 끝났다. HEAD를 기준으로 삼으면 여기서 "already at"으로 끝난다.
    (tmp_path / "calls").unlink()
    applied.parent.mkdir(parents=True, exist_ok=True)
    head_before = _git(work, "rev-parse", "HEAD~1")
    applied.write_text(head_before + "\n")       # 실패 전 기록(첫 실행 이후라면 있었을 값)
    second = _run_update(tmp_path, app, script, pip_ok=True)
    assert second.returncode == 0, second.stderr
    calls = (tmp_path / "calls").read_text()
    assert "pip install -e" in calls and "systemctl restart aipds-backend" in calls
    assert applied.read_text().strip() == _git(work, "rev-parse", "HEAD")


def test_a_run_with_nothing_new_records_nothing(tmp_path):
    """조기 종료는 기록하지 않는다 — 기록 전에 반쯤 적용된 HEAD를 "다 적용됨"으로 굳히지 않게."""
    _work, app, script, applied = _deployed(tmp_path)
    r = _run_update(tmp_path, app, script, pip_ok=True)
    assert r.returncode == 0, r.stderr
    assert "already at" in r.stdout
    assert not applied.exists()


def test_svc_commands_run_inside_the_app_tree(tmp_path):
    """sudo를 부른 자리(/root, 700인 홈)를 SVC가 못 읽으면 pip이 EACCES로 멈춘다."""
    code = [line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]
    first_runuser = next(i for i, line in enumerate(code) if "runuser" in line)
    assert 'cd "$APP"' in code[:first_runuser]


# ---- 시험 브랜치(개발자용 모드) ----
#
# `aipds-update <브랜치>`는 문서에 적지 않는 모드라 안전장치가 스크립트 안에만 있다. 판정 두
# 함수는 부작용이 없게 분리돼 있고, 스크립트를 source하면 그 둘만 정의되고 멈춘다.

def _call(*argv: str, check: bool = False) -> subprocess.CompletedProcess:
    """스크립트를 source한 셸에서 argv를 그대로 실행한다. 셸 문자열은 고정이고 경로와 값은 모두
    위치 인자로 넘긴다 — 테스트 자신이 주입 경로가 되면 안 된다. $0을 스크립트 경로로 두면
    source 가드가 직접 실행으로 보고 끝까지 돌기 때문에 경로는 $1로 넘긴다."""
    return subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"', "_", str(SCRIPT), *argv],
                          capture_output=True, text=True, check=check)


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_sourcing_defines_the_planners_and_runs_nothing_else():
    """source가 root 검사·git·systemctl까지 내려가면 테스트가 운영 절차를 돌리게 된다."""
    r = _call("type", "-t", "valid_branch", "plan_switch")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["function", "function"]
    assert "run as root" not in r.stderr


@pytest.mark.parametrize("name", [
    "main", "handoff-package", "feat/handoff", "release-1.2", "user.name/x_y"])
def test_ordinary_branch_names_are_allowed(name):
    assert _call("valid_branch", name).returncode == 0


@pytest.mark.parametrize("name", [
    "", "-h", "--help", "a b", "x;id", "$(id)", "`id`", "a'b", "a..b", "x.lock", "x/", "@",
    "../main", "a//b"])
def test_dangerous_or_invalid_branch_names_are_refused(name):
    assert _call("valid_branch", name).returncode != 0, name


def _plan(deploy: str, current: str, target: str, recorded: str, head: str) -> dict[str, str]:
    r = _call("plan_switch", deploy, current, target, recorded, head, check=True)
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
                      .replace("/var/lib/aipds", str(state))
                      .replace("/usr/local/bin/aipds-update", str(tmp_path / "installed"))
                      .replace("NGINX_CONF=/etc/nginx/conf.d/aipds.conf",
                               f"NGINX_CONF={tmp_path / 'absent.conf'}"))
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


def test_more_than_one_argument_is_refused(deployed):
    """인자를 조용히 무시하면 시험했다고 착각한다 — 옛 스크립트가 실제로 그랬다."""
    app = deployed["app"]
    before = _git(app, "rev-parse", "HEAD")
    r = deployed["run"]("feature", "extra")
    assert r.returncode == 2
    assert "usage: aipds-update [branch]" in r.stderr
    assert _git(app, "rev-parse", "HEAD") == before


def test_the_test_branch_record_lives_next_to_the_applied_record():
    """두 기록이 같은 root 소유 디렉터리에 있다(/var/lib/aipds)."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "STATE_DIR=/var/lib/aipds\n" in text
    assert "APPLIED_FILE=/var/lib/aipds/update-applied" in text
