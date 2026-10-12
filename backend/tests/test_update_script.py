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
                      .replace("APPLIED_FILE=/var/lib/aipds/update-applied",
                               f"APPLIED_FILE={applied}")
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
