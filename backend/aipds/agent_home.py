# backend/aipds/agent_home.py — per-project state directories for agent and prototype processes.
#
# The agent CLI writes transcripts (`projects/<cwd>/<id>.jsonl`) and session state
# (`file-history/`, `todos/`, `shell-snapshots/` …) under `CLAUDE_CONFIG_DIR`. If every
# project shared that directory, one project's agent could read another project's
# conversations and file backups. So each CLI gets **its own project's** config dir and HOME:
#
#   <root>/discovery/<pid>/{config,home}
#   <root>/build/<pid>/<slug>/{config,home}
#   <root>/proto/<pid>/<slug>/home          (hosting npm — not a CLI, so no config)
#
# The shared config dirs (`discovery-config/`, `proto-config/`) are now only the **source
# of the content**. Each time a CLI is launched, the CLAUDE.md, agents/, and skills/ inside
# them are copied into the per-project config dir (`copy_config`). Copied rather than
# linked because under the execution wrapper (aipds/launcher.py) the CLI cannot see
# outside its own tree.
#
# The execution wrapper (infra/scripts/aipds-launch) also **computes the same layout
# itself** -- the root side does not accept paths from the caller. If the two computations
# disagree, the startup check turns the wrapper off (launcher.probe).
from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from aipds.pathsafe import reject_unsafe_segment, unlink_beneath, write_beneath

ROOT_ENV = "AIPDS_AGENT_HOME_DIR"

#: What is carried from the shared config dir into the per-project config dir. The CLI's
#: runtime state (transcripts, `.claude.json` …) is not included -- that is precisely what
#: must be separated per project.
CONFIG_CONTENT = ("CLAUDE.md", "agents", "skills")

#: CLI settings emptied on every launch. The agent can write to the config dir and the
#: workspace, so if it planted hooks, env, permissions, or MCP servers here, they would run
#: from the next launch on (`setting_sources` is user and project). Nothing in the normal flow
#: writes these files -- they are not in the shared config either.
USER_SETTINGS = "settings.json"
WORKSPACE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json", ".mcp.json")

#: Fragments in a command that point at the files above (and at the CLI state `.claude.json`).
#: Used by both agents' Bash gates (agent/discovery_guard, proto/build_guard) -- they are
#: emptied on every launch, but writing them within a session would hook that session's
#: subsequent tool calls.
SETTINGS_IN_COMMAND = re.compile(r"\.claude/settings[\w.-]*|\.mcp\.json|\.claude\.json")


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
        """Create the directories. The execution wrapper binds them only if they already exist (it does not create them)."""
        self.home.mkdir(parents=True, exist_ok=True)
        if with_config:
            self.config.mkdir(parents=True, exist_ok=True)
        return self


def copy_config(shared_config: str | Path, config_dir: str | Path,
                workspace: str | Path | None = None) -> None:
    """Freshly copy the shared config dir's content (CONFIG_CONTENT) into the per-project config dir.

    Called on every CLI launch. It deletes and re-copies every time -- if a skill was removed
    on the shared side but remained in the copy, that skill would stay enabled. Runtime state
    is not touched.

    CLI settings are emptied: the config dir's `settings.json` is written as an empty config,
    and when `workspace` is given, the project settings under it (WORKSPACE_SETTINGS) are
    deleted. This keeps whatever an agent planted in a previous run from carrying over into
    the next.

    Both trees are written by the agent, so links are not followed (`pathsafe`).
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
    """This prototype's build-agent and hosting state. So a reset does not resume from the old transcript."""
    await _rmtree(build(project_id, slug).base)
    await _rmtree(proto(project_id, slug).base)


async def purge_project(project_id: str) -> None:
    """All agent state for this project. So a project recreated with the same id does not continue old conversations."""
    reject_unsafe_segment(project_id)
    for kind in ("discovery", "build", "proto"):
        await _rmtree(root() / kind / project_id)
