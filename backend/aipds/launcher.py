# backend/aipds/launcher.py — the side that launches agent and prototype processes through the
# execution wrapper.
#
# The wrapper (infra/scripts/aipds-launch, root) puts the process in a sandbox with a
# different uid that sees only its own tree. This module builds the argv that calls that
# wrapper and the env to pass. The wrapper does not trust this side (it computes paths itself
# and filters the env again), so this side only says "what to launch".
#
# **Switch.** On only when `AIPDS_LAUNCHER=1`. When off, processes are launched directly under the
# backend uid (local development, rollback — `aipds-harden disable`).
#
# **If it is on and the startup check (`probe`) fails, nothing is launched.** Falling back to
# direct execution would run the agent and prototypes holding the entire instance role, while
# outwardly nothing seems wrong. The switch is turned on at boot on a new instance
# (`aipds-harden boot`), so nobody is watching that failure — agent turns and hosting stopping
# is more visible than isolation silently going missing. The way back is `disable`.
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from aipds import agent_home
from aipds import credentials
from aipds.credentials import CredentialBroker

_log = logging.getLogger("aipds.launcher")

SWITCH_ENV = "AIPDS_LAUNCHER"
LAUNCH = "/usr/local/libexec/aipds/launch"
#: The SDK's `cli_path`. The SDK appends only CLI arguments after this path, so kind, project,
#: and slug are passed via env (infra/scripts/aipds-claude).
CLAUDE_WRAPPER = "/usr/local/libexec/aipds/claude"

_TRUTHY = {"1", "true", "yes", "on"}


def switch_on() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip().lower() in _TRUTHY


class LauncherUnavailable(RuntimeError):
    """The switch is on but the wrapper cannot be used. Does not fall back to direct execution
    (header)."""


@dataclass
class Launcher:
    broker: CredentialBroker | None
    launch: str = LAUNCH
    claude_wrapper: str = CLAUDE_WRAPPER
    #: Why the startup check failed. If set, every launch is refused and only stop and sweep are
    #: done — units the previous backend launched through the wrapper still need to be cleaned up.
    refused: str | None = None

    def require(self) -> None:
        if self.refused is not None:
            raise LauncherUnavailable(self.refused)

    def claude(self, kind: str, project_id: str, slug: str | None) -> tuple[str, dict[str, str]]:
        """(cli_path, env to add). Put into the SDK options as is."""
        self.require()
        env = {"AIPDS_LAUNCH_KIND": kind, "AIPDS_LAUNCH_PROJECT": project_id}
        if slug is not None:
            env["AIPDS_LAUNCH_SLUG"] = slug
        if self.broker is not None:
            env.update(self.broker.env_for(kind, project_id, slug))
        return self.claude_wrapper, env

    async def exec_npm(self, kind: str, project_id: str, slug: str, *npm_args: str,
                       **spawn: Any) -> asyncio.subprocess.Process:
        """Launch `npm *npm_args` under the wrapper. `spawn` is the kwargs of
        create_subprocess_exec.

        Every place that calls the wrapper (here, stop, sweep, probe) writes argv literally in
        the spawn position — building a list and spreading it would make command injection
        analysis read it as a non-static argv (the reason is in proto/host.py `_npm_install`).
        The only things that vary are kind, project, slug, and the npm arguments, and the
        wrapper checks kind, project, and slug again."""
        self.require()
        return await asyncio.create_subprocess_exec(
            "sudo", "-n", self.launch, "run", kind, project_id, slug, "--", *npm_args, **spawn)

    def npm_env(self, kind: str, project_id: str, slug: str,
                extra: dict[str, str]) -> dict[str, str]:
        """The env for hosting npm. The whole backend env (`os.environ`) is not passed — the
        wrapper filters it anyway, but backend settings are not loaded onto a process that
        goes as far as sudo either."""
        self.require()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(extra)
        if self.broker is not None:
            env.update(self.broker.env_for(kind, project_id, slug))
        return env

    async def stop(self, kind: str, project_id: str, slug: str) -> None:
        """Stop the hosting unit. Signaling the systemd-run client does not stop the unit
        (measured) — so it is stopped by name."""
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", self.launch, "stop", kind, project_id, slug,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            _log.warning("launcher stop %s %s/%s exited %s: %s", kind, project_id, slug,
                         proc.returncode, err.decode(errors="replace").strip())

    def sweep(self) -> None:
        """All units the previous backend launched. When the backend restarts the clients die
        with it, but the units remain."""
        subprocess.run(["sudo", "-n", self.launch, "sweep"], check=False, timeout=60,
                       stdout=subprocess.DEVNULL)

    def revoke_project(self, project_id: str) -> None:
        if self.broker is not None:
            self.broker.revoke_project(project_id)


@dataclass(frozen=True)
class Launch:
    """The values the driver and builder receive: what kind to launch this CLI as, and for
    which project."""
    launcher: Launcher
    kind: str
    project_id: str
    slug: str | None = None

    def claude(self) -> tuple[str, dict[str, str]]:
        return self.launcher.claude(self.kind, self.project_id, self.slug)


def launch_for(kind: str, project_id: str, slug: str | None = None) -> Launch | None:
    launcher = current()
    return None if launcher is None else Launch(launcher, kind, project_id, slug)


_current: Launcher | None = None


def current() -> Launcher | None:
    """The enabled wrapper (it may be refusing — `Launcher.refused`), or None (= switch off,
    direct execution)."""
    return _current


def set_current(launcher: Launcher | None) -> None:
    global _current
    _current = launcher


def sweep_if_installed(run=subprocess.run) -> None:
    """Even if the wrapper is off, if it is installed, stop the units the previous backend
    launched through it.

    The first startup after `aipds-harden disable` is that case: the switch is off, but the
    hosting units the previous backend launched are alive, and the direct-execution sweep (pid
    file → killpg) cannot see them.
    """
    if not os.path.exists(LAUNCH):
        return
    try:
        run(["sudo", "-n", LAUNCH, "sweep"], check=False, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        _log.exception("launcher sweep failed")


def _expected_layout() -> dict[str, str]:
    import aipds.app as app_module
    return {
        "workspaces": str(app_module._workspaces_dir()),
        "protos": str(app_module._proto_root()),
        "agent_home": str(agent_home.root()),
    }


def probe(run=subprocess.run) -> Launcher | None:
    """Startup check. None if the switch is off (direct execution). If on, it always returns a
    Launcher — one with `refused` filled in if the wrapper cannot actually be used (header).

    Has the wrapper run `check` to confirm sudoers, installation, and the bundled CLI in one
    go, and checks that the paths the wrapper computes match the backend's — if they differ,
    the CLI writes transcripts where the backend does not look and hosting runs the wrong
    tree.
    """
    if not switch_on():
        return None
    try:
        out = run(["sudo", "-n", LAUNCH, "check"], capture_output=True, text=True,
                  timeout=30, check=True).stdout
        report = json.loads(out)
    except Exception as exc:
        return _refuse(f"the launcher is not usable ({exc})")
    expected = _expected_layout()
    mismatch = {k: (report.get(k), v) for k, v in expected.items() if report.get(k) != v}
    if mismatch or not report.get("claude") or not report.get("npm"):
        return _refuse(f"launcher layout does not match the backend ({mismatch}; "
                       f"claude={report.get('claude')} npm={report.get('npm')})")
    broker = None
    role = os.environ.get(credentials.ROLE_ENV, "").strip()
    if role:
        broker = CredentialBroker(role, port=int(
            os.environ.get(credentials.PORT_ENV, str(credentials.DEFAULT_PORT))))
    else:
        _log.warning("launcher on without %s — sandboxed processes get "
                     "no credential endpoint (Bedrock works only while IMDS is reachable)",
                     credentials.ROLE_ENV)
    if report.get("imds_block") and broker is None:
        _log.error("IMDS is blocked for sandboxed processes but there is no credential "
                   "endpoint — every agent turn and prototype LLM call will fail")
    _log.info("launcher on (imds_block=%s, credentials=%s)", report.get("imds_block"),
              "on" if broker else "off")
    return Launcher(broker=broker)


def _refuse(reason: str) -> Launcher:
    _log.error("%s=1 but %s — agent turns and prototype hosting are refused until it is "
               "fixed (or `aipds-harden disable` runs them directly)", SWITCH_ENV, reason)
    return Launcher(broker=None, refused=reason)
