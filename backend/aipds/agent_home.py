# backend/aipds/agent_home.py — 에이전트·프로토타입 프로세스의 프로젝트별 상태 디렉터리.
#
# 에이전트 CLI는 `CLAUDE_CONFIG_DIR` 아래에 트랜스크립트(`projects/<cwd>/<id>.jsonl`)와
# 세션 상태(`file-history/`, `todos/`, `shell-snapshots/` …)를 쓴다. 그 디렉터리를 모든
# 프로젝트가 공유하면 한 프로젝트의 에이전트가 다른 프로젝트의 대화와 파일 백업을 읽을 수
# 있다. 그래서 CLI마다 **자기 프로젝트의** config dir과 HOME을 준다:
#
#   <root>/discovery/<pid>/{config,home}
#   <root>/build/<pid>/<slug>/{config,home}
#   <root>/proto/<pid>/<slug>/home          (호스팅 npm — CLI가 아니므로 config가 없다)
#
# 공유 config dir(`discovery-config/`, `proto-config/`)은 이제 **내용의 출처**일 뿐이다.
# CLI를 띄울 때마다 그 안의 CLAUDE.md·agents/·skills/를 프로젝트별 config dir로 복사한다
# (`copy_config`). 복사이지 링크가 아닌 이유: 실행 래퍼(aipds/launcher.py) 아래에서 CLI는
# 자기 트리 밖이 보이지 않는다.
#
# 실행 래퍼(infra/scripts/aipds-launch)도 같은 배치를 **스스로 계산한다** — root 쪽은
# 호출자가 준 경로를 받지 않기 때문이다. 두 계산이 어긋나면 기동 점검이 래퍼를 끈다
# (launcher.probe).
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from aipds.pathsafe import reject_unsafe_segment, unlink_beneath, write_beneath

ROOT_ENV = "AIPDS_AGENT_HOME_DIR"

#: 공유 config dir에서 프로젝트별 config dir로 옮기는 것. CLI의 런타임 상태(트랜스크립트,
#: `.claude.json` …)는 여기 넣지 않는다 — 그것이 프로젝트마다 갈라야 하는 것이다.
CONFIG_CONTENT = ("CLAUDE.md", "agents", "skills")

#: 띄울 때마다 비워 두는 CLI 설정. 에이전트는 config dir과 워크스페이스에 쓸 수 있으므로,
#: 여기 hook·env·권한·MCP 서버를 심으면 다음 실행부터 그것이 돈다(`setting_sources`가
#: user·project다). 정상 흐름에서 이 파일들을 쓰는 곳은 없다 — 공유 config에도 없다.
USER_SETTINGS = "settings.json"
WORKSPACE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json", ".mcp.json")


def root() -> Path:
    return Path(os.environ.get(ROOT_ENV, "~/aipds-agent-home")).expanduser()


@dataclass(frozen=True)
class AgentHome:
    base: Path

    @property
    def config(self) -> Path:
        return self.base / "config"

    @property
    def home(self) -> Path:
        return self.base / "home"

    def prepare(self, with_config: bool = True) -> "AgentHome":
        """디렉터리를 만든다. 실행 래퍼는 이것들이 이미 있어야 bind한다(스스로 만들지 않는다)."""
        self.home.mkdir(parents=True, exist_ok=True)
        if with_config:
            self.config.mkdir(parents=True, exist_ok=True)
        return self


def copy_config(shared_config: str | Path, config_dir: str | Path,
                workspace: str | Path | None = None) -> None:
    """공유 config dir의 내용(CONFIG_CONTENT)을 프로젝트별 config dir에 새로 복사한다.

    CLI를 띄울 때마다 부른다. 매번 지우고 다시 복사한다 — 공유 쪽에서 스킬 하나가 빠졌는데
    사본에 남아 있으면 그 스킬이 계속 켜진다. 런타임 상태는 건드리지 않는다.

    CLI 설정은 비운다: config dir의 `settings.json`을 빈 설정으로 쓰고, `workspace`를
    주면 그 아래의 프로젝트 설정(WORKSPACE_SETTINGS)을 지운다. 이전 실행의 에이전트가
    심은 것이 다음 실행에 이어지지 않게 하기 위해서다.

    두 트리 모두 에이전트가 쓰므로 링크를 따라가지 않는다(`pathsafe`).
    """
    shared, config = Path(shared_config), Path(config_dir)
    config.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_CONTENT:
        src, dst = shared / name, config / name
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        elif dst.exists() or dst.is_symlink():
            dst.unlink()
        if src.is_dir():
            for path in sorted(src.rglob("*")):
                if path.is_file():
                    rel = f"{name}/{path.relative_to(src).as_posix()}"
                    write_beneath(config, rel, path.read_bytes())
        elif src.is_file():
            write_beneath(config, name, src.read_bytes())
    write_beneath(config, USER_SETTINGS, "{}\n")
    if workspace is not None:
        for rel in WORKSPACE_SETTINGS:
            unlink_beneath(Path(workspace), rel)


def discovery(project_id: str) -> AgentHome:
    reject_unsafe_segment(project_id)
    return AgentHome(root() / "discovery" / project_id)


def build(project_id: str, slug: str) -> AgentHome:
    reject_unsafe_segment(project_id)
    reject_unsafe_segment(slug)
    return AgentHome(root() / "build" / project_id / slug)


def proto(project_id: str, slug: str) -> AgentHome:
    reject_unsafe_segment(project_id)
    reject_unsafe_segment(slug)
    return AgentHome(root() / "proto" / project_id / slug)


async def _rmtree(target: Path) -> None:
    if not target.exists():
        return
    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    if target.exists():
        raise RuntimeError(f"agent home purge left residue: {target}")


async def purge_prototype(project_id: str, slug: str) -> None:
    """이 프로토타입의 빌드 에이전트·호스팅 상태. 리셋 뒤 옛 트랜스크립트로 재개하지 않게."""
    await _rmtree(build(project_id, slug).base)
    await _rmtree(proto(project_id, slug).base)


async def purge_project(project_id: str) -> None:
    """이 프로젝트의 모든 에이전트 상태. 같은 id로 다시 만든 프로젝트가 옛 대화를 잇지 않게."""
    reject_unsafe_segment(project_id)
    for kind in ("discovery", "build", "proto"):
        await _rmtree(root() / kind / project_id)
