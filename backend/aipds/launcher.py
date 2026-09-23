# backend/aipds/launcher.py — 에이전트·프로토타입 프로세스를 실행 래퍼로 띄우는 쪽.
#
# 래퍼(infra/scripts/aipds-launch, root)가 프로세스를 다른 uid·자기 트리만 보이는 샌드박스에
# 넣는다. 이 모듈은 그 래퍼를 부르는 argv와 넘길 env를 만든다. 래퍼는 이 쪽을 믿지 않으므로
# (경로를 스스로 계산하고 env를 다시 거른다) 여기는 "무엇을 띄울지"만 말한다.
#
# **스위치.** `AIPDS_LAUNCHER=1`일 때만 켠다. 꺼져 있으면 지금처럼 백엔드 uid로 직접 띄운다
# (로컬 개발, 단계적 롤아웃, 되돌리기). 켜져 있어도 기동 점검(`probe`)이 실패하면 경고를 남기고
# 직접 실행으로 돌아간다 — 권한 전환의 실패는 기능 정지로 드러나기 때문이다.
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass

from aipds import agent_home
from aipds import credentials
from aipds.credentials import CredentialBroker

_log = logging.getLogger("aipds.launcher")

SWITCH_ENV = "AIPDS_LAUNCHER"
LAUNCH = "/usr/local/libexec/aipds/launch"
#: SDK의 `cli_path`. SDK는 이 경로 뒤에 CLI 인자만 붙이므로 kind·project·slug는 env로 건넨다
#: (infra/scripts/aipds-claude).
CLAUDE_WRAPPER = "/usr/local/libexec/aipds/claude"

_TRUTHY = {"1", "true", "yes", "on"}


def switch_on() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip().lower() in _TRUTHY


@dataclass
class Launcher:
    broker: CredentialBroker | None
    launch: str = LAUNCH
    claude_wrapper: str = CLAUDE_WRAPPER

    def _sudo(self, *args: str) -> list[str]:
        return ["sudo", "-n", self.launch, *args]

    def claude(self, kind: str, project_id: str, slug: str | None) -> tuple[str, dict[str, str]]:
        """(cli_path, 더할 env). SDK 옵션에 그대로 넣는다."""
        env = {"AIPDS_LAUNCH_KIND": kind, "AIPDS_LAUNCH_PROJECT": project_id}
        if slug is not None:
            env["AIPDS_LAUNCH_SLUG"] = slug
        if self.broker is not None:
            env.update(self.broker.env_for(kind, project_id, slug))
        return self.claude_wrapper, env

    def npm_argv(self, kind: str, project_id: str, slug: str, *npm_args: str) -> list[str]:
        return self._sudo("run", kind, project_id, slug, "--", *npm_args)

    def npm_env(self, kind: str, project_id: str, slug: str,
                extra: dict[str, str]) -> dict[str, str]:
        """호스팅 npm의 env. 백엔드 env 전체(`os.environ`)를 넘기지 않는다 — 래퍼가 어차피
        거르지만, sudo까지 가는 프로세스에도 백엔드 설정을 싣지 않는다."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(extra)
        if self.broker is not None:
            env.update(self.broker.env_for(kind, project_id, slug))
        return env

    async def stop(self, kind: str, project_id: str, slug: str) -> None:
        """호스팅 unit을 멈춘다. systemd-run 클라이언트에 신호를 보내도 unit은 멈추지 않는다
        (실측) — 그래서 이름으로 멈춘다."""
        proc = await asyncio.create_subprocess_exec(
            *self._sudo("stop", kind, project_id, slug),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            _log.warning("launcher stop %s %s/%s exited %s: %s", kind, project_id, slug,
                         proc.returncode, err.decode(errors="replace").strip())

    def sweep(self) -> None:
        """직전 백엔드가 띄운 unit 전부. 백엔드가 재시작하면 클라이언트는 함께 죽지만 unit은 남는다."""
        subprocess.run(self._sudo("sweep"), check=False, timeout=60,
                       stdout=subprocess.DEVNULL)

    def revoke_project(self, project_id: str) -> None:
        if self.broker is not None:
            self.broker.revoke_project(project_id)


@dataclass(frozen=True)
class Launch:
    """드라이버·빌더가 받는 값: 이 CLI를 어떤 kind로, 어느 프로젝트로 띄울지."""
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
    """켜진 래퍼, 또는 None(= 직접 실행)."""
    return _current


def set_current(launcher: Launcher | None) -> None:
    global _current
    _current = launcher


def sweep_if_installed(run=subprocess.run) -> None:
    """래퍼가 꺼져 있어도, 설치돼 있으면 직전 백엔드가 래퍼로 띄운 unit을 멈춘다.

    `aipds-harden disable` 뒤의 첫 기동이 그 경우다: 스위치는 꺼졌지만 직전 백엔드가 띄운
    호스팅 unit은 살아 있고, 직접 실행 경로의 스윕(pid 파일 → killpg)은 그것을 못 본다.
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
    """기동 점검. 스위치가 켜져 있고 래퍼가 실제로 쓸 수 있을 때만 Launcher를 돌려준다.

    래퍼에 `check`를 시켜 sudoers·설치·번들 CLI를 한 번에 확인하고, 래퍼가 계산하는 경로가
    백엔드의 경로와 같은지 본다 — 다르면 CLI는 백엔드가 보지 않는 곳에 트랜스크립트를 쓰고
    호스팅은 엉뚱한 트리를 돈다.
    """
    if not switch_on():
        return None
    try:
        out = run(["sudo", "-n", LAUNCH, "check"], capture_output=True, text=True,
                  timeout=30, check=True).stdout
        report = json.loads(out)
    except Exception as exc:
        _log.warning("%s=1 but the launcher is not usable (%s) — running agents and "
                     "prototypes directly as the backend user", SWITCH_ENV, exc)
        return None
    expected = _expected_layout()
    mismatch = {k: (report.get(k), v) for k, v in expected.items() if report.get(k) != v}
    if mismatch or not report.get("claude") or not report.get("npm"):
        _log.warning("launcher layout does not match the backend (%s; claude=%s npm=%s) — "
                     "running directly", mismatch, report.get("claude"), report.get("npm"))
        return None
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
