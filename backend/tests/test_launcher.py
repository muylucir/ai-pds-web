# backend/tests/test_launcher.py — 실행 래퍼를 부르는 쪽(aipds/launcher.py)과 그 배선.
#
# 래퍼 자체는 test_launch_script.py가 본다. 여기는 백엔드가 (1) 켜도 되는지 판단하는 기동 점검,
# (2) 드라이버·빌더·호스팅이 래퍼를 실제로 거쳐 띄우는지, (3) 백엔드 env가 새지 않는지를 본다.
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from aipds import agent_home, launcher
from aipds.launcher import Launch, Launcher

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "proto_npm_stub"


# ---- probe ----

def _check_ok(report):
    def run(argv, **kwargs):
        assert argv == ["sudo", "-n", launcher.LAUNCH, "check"]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(report), stderr="")
    return run


def _layout_report(**over):
    import aipds.app as app_module
    report = {"version": 1, "workspaces": str(app_module._workspaces_dir()),
              "protos": str(app_module._proto_root()),
              "agent_home": str(agent_home.root()),
              "claude": "/opt/aipds/.../claude", "npm": True, "imds_block": False}
    report.update(over)
    return report


def test_probe_is_off_without_the_switch(monkeypatch):
    monkeypatch.delenv(launcher.SWITCH_ENV, raising=False)
    assert launcher.probe(run=lambda *a, **k: pytest.fail("must not call sudo")) is None


def test_probe_refuses_when_sudo_fails(monkeypatch):
    """켜졌는데 쓸 수 없으면 직접 실행으로 돌아가지 않는다 — 격리가 조용히 빠진다."""
    monkeypatch.setenv(launcher.SWITCH_ENV, "1")

    def run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="sudo: a password is required")
    active = launcher.probe(run=run)
    assert active is not None and active.refused and active.broker is None


def test_probe_refuses_on_a_layout_mismatch(monkeypatch):
    """경로가 다르면 CLI는 백엔드가 보지 않는 곳에 트랜스크립트를 쓴다."""
    monkeypatch.setenv(launcher.SWITCH_ENV, "1")
    report = _layout_report(agent_home="/opt/aipds/agent-home-elsewhere")
    assert launcher.probe(run=_check_ok(report)).refused
    assert launcher.probe(run=_check_ok(_layout_report(claude=None))).refused


def test_a_refused_launcher_starts_nothing_but_still_sweeps(monkeypatch):
    refused = Launcher(broker=None, refused="no sudoers")
    with pytest.raises(launcher.LauncherUnavailable):
        refused.claude("discovery", "p1", None)
    with pytest.raises(launcher.LauncherUnavailable):
        refused.npm_argv("proto", "p1", "todo", "run", "start")
    with pytest.raises(launcher.LauncherUnavailable):
        refused.npm_env("proto", "p1", "todo", {})
    # 스윕은 거부하지 않는다 — 직전 백엔드가 래퍼로 띄운 unit을 치워야 한다.
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv))
    refused.sweep()
    assert calls == [["sudo", "-n", launcher.LAUNCH, "sweep"]]


def test_probe_turns_on_with_a_credential_broker(monkeypatch):
    monkeypatch.setenv(launcher.SWITCH_ENV, "1")
    monkeypatch.setenv("AIPDS_AGENT_ROLE_ARN", "arn:aws:iam::1:role/AgentRole")
    monkeypatch.setenv("AIPDS_CREDENTIALS_PORT", "18002")
    active = launcher.probe(run=_check_ok(_layout_report()))
    assert active is not None
    assert active.broker is not None and active.broker.port == 18002


def test_probe_without_a_role_has_no_broker(monkeypatch):
    monkeypatch.setenv(launcher.SWITCH_ENV, "1")
    monkeypatch.delenv("AIPDS_AGENT_ROLE_ARN", raising=False)
    active = launcher.probe(run=_check_ok(_layout_report()))
    assert active is not None and active.broker is None


# ---- Launcher ----

def _broker():
    from aipds.credentials import CredentialBroker
    return CredentialBroker("arn:aws:iam::1:role/AgentRole", port=18003,
                            sts_factory=lambda: None)


def test_claude_passes_kind_and_ids_through_env():
    path, env = Launcher(broker=_broker()).claude("build", "p1", "todo")
    assert path == launcher.CLAUDE_WRAPPER
    assert env["AIPDS_LAUNCH_KIND"] == "build"
    assert env["AIPDS_LAUNCH_PROJECT"] == "p1" and env["AIPDS_LAUNCH_SLUG"] == "todo"
    assert env["AWS_CONTAINER_CREDENTIALS_FULL_URI"].startswith("http://127.0.0.1:18003/")


def test_npm_env_does_not_carry_the_backend_env(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-2")
    env = Launcher(broker=_broker()).npm_env("proto", "p1", "todo", {"PORT": "4001"})
    assert "AIPDS_S3_BUCKET" not in env
    assert env["AWS_REGION"] == "ap-northeast-2" and env["PORT"] == "4001"
    assert "AWS_CONTAINER_AUTHORIZATION_TOKEN" in env
    # npm install/build는 자격증명을 받지 않는다.
    assert "AWS_CONTAINER_AUTHORIZATION_TOKEN" not in Launcher(broker=_broker()).npm_env(
        "host-build", "p1", "todo", {})


def test_launch_for_follows_the_current_launcher():
    assert launcher.launch_for("discovery", "p1") is None
    active = Launcher(broker=None)
    launcher.set_current(active)
    assert launcher.launch_for("build", "p1", "s") == Launch(active, "build", "p1", "s")


# ---- driver / builder wiring ----

class _Capture:
    def __init__(self):
        self.options = None

    def client(self):
        capture = self

        class FakeClient:
            def __init__(self, options=None):
                capture.options = options
        return FakeClient


def _shared_config(tmp_path):
    shared = tmp_path / "shared-config"
    (shared / "skills" / "s").mkdir(parents=True)
    (shared / "skills" / "s" / "SKILL.md").write_text("x", encoding="utf-8")
    (shared / "CLAUDE.md").write_text("contract", encoding="utf-8")
    return shared


def test_discovery_client_goes_through_the_launcher(tmp_path, monkeypatch):
    import claude_agent_sdk
    from aipds.agent.claude_driver import ClaudeDriver, _default_client_factory
    from aipds.transcript_restore import MirrorOnlyStore
    from tests.fakes.in_memory_s3 import FakeS3Store

    capture = _Capture()
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", capture.client())
    home = agent_home.discovery("p1").prepare()
    active = Launcher(broker=_broker())
    driver = ClaudeDriver(workspace=str(tmp_path / "ws"), rules_dir=str(tmp_path),
                          config_dir=str(home.config), s3=FakeS3Store(),
                          shared_config_dir=str(_shared_config(tmp_path)),
                          launch=Launch(active, "discovery", "p1"))
    _default_client_factory(driver)({"session_id": "p1", "resume": False})

    options = capture.options
    assert options.cli_path == launcher.CLAUDE_WRAPPER
    assert options.env["AIPDS_LAUNCH_KIND"] == "discovery"
    assert options.env["CLAUDE_CONFIG_DIR"] == str(home.config)
    assert isinstance(options.session_store, MirrorOnlyStore)
    # 공유 config의 내용이 프로젝트별 config dir에 와 있다 — 재개해도 CLAUDE.md가 있다.
    assert (home.config / "CLAUDE.md").read_text(encoding="utf-8") == "contract"
    assert (home.config / "skills" / "s" / "SKILL.md").is_file()


def test_discovery_client_without_the_launcher_runs_the_bundled_cli(tmp_path, monkeypatch):
    import claude_agent_sdk
    from aipds.agent.claude_driver import ClaudeDriver, _default_client_factory
    from tests.fakes.in_memory_s3 import FakeS3Store

    capture = _Capture()
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", capture.client())
    driver = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store())
    _default_client_factory(driver)({"session_id": "p1", "resume": False})
    assert capture.options.cli_path is None
    assert "AIPDS_LAUNCH_KIND" not in capture.options.env


def test_builder_client_goes_through_the_launcher(tmp_path, monkeypatch):
    import claude_agent_sdk
    from aipds.proto.builder import PrototypeBuilder, _default_client_factory
    from aipds.transcript_restore import MirrorOnlyStore
    from claude_agent_sdk import InMemorySessionStore

    capture = _Capture()
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", capture.client())
    home = agent_home.build("p1", "todo").prepare()
    b = PrototypeBuilder(
        workspace=str(tmp_path), config_dir=str(home.config),
        session_id="11111111-2222-3333-4444-555555555555", resume=False,
        session_store=InMemorySessionStore(),
        shared_config_dir=str(_shared_config(tmp_path)),
        launch=Launch(Launcher(broker=None), "build", "p1", "todo"))
    _default_client_factory(b)()
    options = capture.options
    assert options.cli_path == launcher.CLAUDE_WRAPPER
    assert options.env["AIPDS_LAUNCH_SLUG"] == "todo"
    assert isinstance(options.session_store, MirrorOnlyStore)
    assert (home.config / "skills" / "s" / "SKILL.md").is_file()


def test_app_factories_use_per_project_config_dirs(monkeypatch):
    import aipds.app as app_module
    app_module.registry.register("lw-a", None)
    app_module.registry.register("lw-b", None)
    a = app_module.driver_factory("lw-a", Path("/tmp/lw-a"))
    b = app_module.driver_factory("lw-b", Path("/tmp/lw-b"))
    assert a._config_dir != b._config_dir
    assert a._config_dir == str(agent_home.discovery("lw-a").config)
    assert a._shared_config_dir == str(app_module._discovery_config_dir())
    assert a._launch is None
    session = app_module.proto_session_factory("lw-a", "todo")
    builder = session._builder_factory("11111111-2222-3333-4444-555555555555", False)
    assert builder._config_dir == str(agent_home.build("lw-a", "todo").config)


async def test_discovery_restores_a_missing_transcript_before_connecting(tmp_path):
    """인스턴스 교체 뒤: 로컬 사본은 없고 S3 미러에만 대화가 있다 → 이어서 재개한다."""
    import uuid

    from claude_agent_sdk import InMemorySessionStore, project_key_for_directory

    from aipds.agent.claude_driver import (ClaudeDriver, _sdk_session_id,
                                           _transcript_exists)
    from tests.fakes.in_memory_s3 import FakeS3Store

    ws = tmp_path / "ws"
    ws.mkdir()
    store = InMemorySessionStore()
    driver = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                          session_store=store, client_factory=lambda s: None)
    session = {"session_id": "proj-x"}
    sid, _ = _sdk_session_id(session)
    await store.append({"project_key": project_key_for_directory(str(ws)),
                        "session_id": sid},
                       [{"type": "user", "uuid": str(uuid.uuid4())}])

    await driver._restore_transcript(session)

    assert _transcript_exists(driver._config_dir, str(ws), sid)
    assert driver._resolve_resume(dict(session, resume=False))["resume"] is True


# ---- hosting ----

class FakeLauncher(Launcher):
    """sudo 없이 npm을 그대로 부르는 래퍼. 호스팅이 래퍼 경로를 타는지(argv, env, stop)를 본다."""

    def __init__(self):
        super().__init__(broker=None)
        self.argvs = []
        self.envs = []
        self.stopped = []
        self.swept = 0
        self.procs = []

    def npm_argv(self, kind, project_id, slug, *npm_args):
        self.argvs.append((kind, project_id, slug, npm_args))
        return ["npm", *npm_args]

    def npm_env(self, kind, project_id, slug, extra):
        env = super().npm_env(kind, project_id, slug, extra)
        self.envs.append((kind, env))
        return env

    async def stop(self, kind, project_id, slug):
        self.stopped.append((kind, project_id, slug))
        # 실제 래퍼는 unit을 멈추고, 그러면 클라이언트도 끝난다. 여기서는 트리를 끝낸다.
        for proc in self.procs:
            if proc.returncode is None:
                os.killpg(os.getpgid(proc.pid), 15)

    def sweep(self):
        self.swept += 1


async def test_hosting_goes_through_the_launcher(tmp_path, monkeypatch):
    from aipds.proto.host import ProtoHost

    monkeypatch.setenv("AIPDS_S3_BUCKET", "must-not-leak")
    root = tmp_path / "protos"
    served = root / "p1" / "todo" / "prototype"
    served.mkdir(parents=True)
    for path in FIXTURE_DIR.iterdir():
        (served / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    fake = FakeLauncher()
    host = ProtoHost(root=root, port_range=range(4101, 4110), launcher=fake)

    real_exec = asyncio.create_subprocess_exec

    async def spy(*argv, **kwargs):
        proc = await real_exec(*argv, **kwargs)
        fake.procs.append(proc)
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    info = await host.start("p1", "todo", cwd=served, base_path="/api/proto/p1/todo")
    try:
        assert info.state == "running"
        assert [a[0] for a in fake.argvs] == ["host-build", "proto"]
        assert fake.argvs[-1][3] == ("run", "start")
        for _, env in fake.envs:
            assert "AIPDS_S3_BUCKET" not in env
        assert fake.envs[-1][1]["PORT"] == str(info.port)
        # 래퍼가 bind할 HOME이 미리 만들어져 있다.
        assert agent_home.proto("p1", "todo").home.is_dir()
    finally:
        await host.stop("p1", "todo")
    assert fake.stopped == [("proto", "p1", "todo")]
    assert host.status("p1", "todo").state == "stopped"


async def test_the_launcher_refuses_a_tree_it_would_not_serve(tmp_path):
    from aipds.proto.host import ProtoHost

    root = tmp_path / "protos"
    elsewhere = root / "p1" / "todo"
    elsewhere.mkdir(parents=True)
    host = ProtoHost(root=root, launcher=FakeLauncher())
    with pytest.raises(ValueError):
        await host.start("p1", "todo", cwd=elsewhere)


async def test_hosting_is_refused_before_touching_what_runs(tmp_path):
    """거부 중이면 떠 있는 호스팅을 멈추지도 않는다 — 멈추고 못 띄우면 링크까지 죽는다."""
    from aipds.proto.host import ProtoHost

    root = tmp_path / "protos"
    served = root / "p1" / "todo" / "prototype"
    served.mkdir(parents=True)
    fake = FakeLauncher()
    fake.refused = "no sudoers"
    host = ProtoHost(root=root, launcher=fake)
    with pytest.raises(launcher.LauncherUnavailable):
        await host.start("p1", "todo", cwd=served)
    assert fake.stopped == [] and fake.argvs == []


def test_sweep_goes_through_the_launcher(tmp_path):
    from aipds.proto.host import ProtoHost

    root = tmp_path / "protos"
    pid_file = root / "p1" / "todo" / "prototype" / ".proto-host.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("999999", encoding="utf-8")
    fake = FakeLauncher()
    host = ProtoHost(root=root, launcher=fake)
    assert host.previously_running() == [("p1", "todo")]
    assert host.sweep_orphans() == 1
    assert fake.swept == 1 and not pid_file.exists()


def test_sweep_if_installed_only_when_the_launcher_exists(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(launcher, "LAUNCH", str(tmp_path / "missing"))
    launcher.sweep_if_installed(run=lambda argv, **k: calls.append(argv))
    assert calls == []
    installed = tmp_path / "launch"
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "LAUNCH", str(installed))
    launcher.sweep_if_installed(run=lambda argv, **k: calls.append(argv))
    assert calls == [["sudo", "-n", str(installed), "sweep"]]
