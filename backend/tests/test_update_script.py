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
    out = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout
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
