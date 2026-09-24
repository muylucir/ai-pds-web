# backend/tests/test_launch_script.py — infra/scripts/aipds-launch(root 실행 래퍼)의 판정부.
#
# 래퍼는 sudo로 root가 되는 길이므로 "무엇을 거부하는가"가 곧 사양이다. 실제 systemd-run은
# 부르지 않는다 — `build_run`이 만드는 argv와 env를 본다. 샌드박스 자체의 효과(다른 트리가
# 안 보이는지, IMDS가 막히는지)는 인스턴스에서 실측으로 확인했다(docs 계획 3·4-BC §6.1).
from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "aipds-launch"


def _load():
    loader = importlib.machinery.SourceFileLoader("aipds_launch", str(SCRIPT))
    spec = importlib.util.spec_from_loader("aipds_launch", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def launch(tmp_path, monkeypatch):
    mod = _load()
    app = tmp_path / "opt-aipds"
    monkeypatch.setattr(mod, "APP", app)
    monkeypatch.setattr(mod, "WORKSPACES", app / "workspaces")
    monkeypatch.setattr(mod, "PROTOS", app / "protos")
    monkeypatch.setattr(mod, "AGENT_HOME", app / "agent-home")
    return mod


def _tree(mod, kind, pid="p1", slug=None):
    work, home = mod.layout(kind, pid, slug)
    work.mkdir(parents=True, exist_ok=True)
    (home / "home").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    return work, home


CLAUDE = Path("/fake/claude")


def _run(mod, kind, pid="p1", slug=None, args=(), environ=None, conf=None):
    return mod.build_run(kind, pid, slug, list(args), environ or {}, conf or {},
                         claude=CLAUDE)


def _props(argv):
    return [a.split("=", 1)[1] for a in argv if a.startswith("--property=")]


def _command(argv):
    return argv[argv.index("--") + 1:]


def test_discovery_runs_the_bundled_cli_as_the_agent_uid_in_its_own_tree(launch):
    work, home = _tree(launch, "discovery")
    argv, env = _run(launch, "discovery", args=["--output-format", "stream-json"])

    assert argv[0] == "/usr/bin/systemd-run"
    assert "--pipe" in argv and "--wait" in argv
    props = _props(argv)
    assert f"WorkingDirectory={work}" in props
    assert f"BindPaths={work}" in props and f"BindPaths={home}" in props
    # 다른 프로젝트 트리는 빈 tmpfs 아래로 사라진다.
    for hidden in (launch.WORKSPACES, launch.PROTOS, launch.AGENT_HOME):
        assert f"TemporaryFileSystem={hidden}" in props
    cmd = _command(argv)
    # PID 네임스페이스 → uid 전환 → CLI. 같은 uid의 다른 unit이 /proc/<pid>/root로 보이지 않게.
    assert cmd[:2] == ["/usr/bin/unshare", "--pid"]
    assert "--reuid=aipds-agent" in cmd and "--regid=aipds-work" in cmd
    assert cmd[-3:] == [str(CLAUDE), "--output-format", "stream-json"]
    assert env["CLAUDE_CONFIG_DIR"] == str(home / "config")
    assert env["HOME"] == str(home / "home")


def test_config_dir_from_the_caller_is_ignored(launch):
    """공유 config dir을 가리키게 하면 다른 프로젝트의 트랜스크립트가 보인다."""
    _, home = _tree(launch, "discovery")
    _, env = _run(launch, "discovery",
                  environ={"CLAUDE_CONFIG_DIR": "/opt/aipds/discovery-config"})
    assert env["CLAUDE_CONFIG_DIR"] == str(home / "config")


def test_env_is_an_allowlist(launch):
    _tree(launch, "discovery")
    environ = {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "750000",
        "CLAUDE_AGENT_SDK_VERSION": "0.2.158",
        "ANTHROPIC_MODEL": "global.anthropic.claude-opus-5",
        "AWS_REGION": "ap-northeast-2",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1:8001/credentials",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "tok",
        # 넘기면 안 되는 것들
        "AWS_ACCESS_KEY_ID": "AKIA…", "AWS_SECRET_ACCESS_KEY": "s",
        "AIPDS_S3_BUCKET": "bucket", "COGNITO_CLIENT_SECRET": "x",
        "LD_PRELOAD": "/tmp/evil.so", "PATH": "/tmp/evil",
    }
    argv, env = _run(launch, "discovery", environ=environ)
    for kept in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                 "CLAUDE_AGENT_SDK_VERSION", "ANTHROPIC_MODEL", "AWS_REGION",
                 "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN"):
        assert env[kept] == environ[kept]
    for dropped in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AIPDS_S3_BUCKET",
                    "COGNITO_CLIENT_SECRET", "LD_PRELOAD"):
        assert dropped not in env
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_env_values_never_appear_in_argv(launch):
    """argv는 `ps`에 보인다 — 자격증명 토큰이 실리면 안 된다."""
    _tree(launch, "discovery")
    argv, _ = _run(launch, "discovery",
                   environ={"AWS_CONTAINER_AUTHORIZATION_TOKEN": "sekrit-token"})
    assert "--setenv=AWS_CONTAINER_AUTHORIZATION_TOKEN" in argv
    assert not any("sekrit-token" in a for a in argv)


@pytest.mark.parametrize("uri", [
    "http://169.254.170.2/v2/credentials",
    "http://attacker.example/credentials",
    "https://127.0.0.1:8001/credentials",
    "http://127.0.0.1:8001/credentials?x=1",
])
def test_the_credential_uri_must_be_the_loopback_endpoint(launch, uri):
    _tree(launch, "discovery")
    _, env = _run(launch, "discovery",
                  environ={"AWS_CONTAINER_CREDENTIALS_FULL_URI": uri})
    assert "AWS_CONTAINER_CREDENTIALS_FULL_URI" not in env


def test_dollar_signs_survive_systemd_expansion(launch):
    """systemd는 ExecStart 인자의 `${VAR}`를 푼다(실측). `$$`로 넘겨야 `$`로 도착한다."""
    _tree(launch, "discovery")
    argv, _ = _run(launch, "discovery", args=["--system-prompt", "use ${HOME} and $x"])
    assert _command(argv)[-1] == "use $${HOME} and $$x"


def test_imds_is_blocked_only_when_configured(launch):
    _tree(launch, "discovery")
    argv, _ = _run(launch, "discovery")
    assert not any(p.startswith("IPAddressDeny=") for p in _props(argv))
    argv, _ = _run(launch, "discovery", conf={"imds_block": "1"})
    assert "IPAddressDeny=169.254.169.254/32 fd00:ec2::254/128" in _props(argv)


@pytest.mark.parametrize("kind, slug", [("discovery", None), ("build", "todo"),
                                        ("host-build", "todo"), ("proto", "todo")])
def test_the_agent_home_is_handed_back_to_the_group_when_the_unit_stops(launch, kind, slug):
    """번들 CLI는 config dir과 트랜스크립트를 0700/0600으로 명시해 만든다(버전에 따라) — 그러면
    백엔드가 그 프로젝트를 재개하지도 지우지도 못한다. 경로는 계산한 자기 agent-home뿐이다."""
    _, home = _tree(launch, kind, slug=slug)
    args = {"host-build": ["install"], "proto": ["run", "start"]}.get(kind, [])
    argv, _ = _run(launch, kind, slug=slug, args=args)
    stops = [p for p in _props(argv) if p.startswith("ExecStopPost=")]
    assert stops == [f"ExecStopPost=+/usr/bin/chmod -R g+rwX {home}"]


def test_proto_serves_only_the_prototype_subtree_with_allowed_npm_args(launch):
    work, home = _tree(launch, "proto", slug="todo")
    argv, env = _run(launch, "proto", slug="todo", args=["run", "start"],
                     environ={"PORT": "4001", "NEXT_PUBLIC_BASE_PATH": "/api/proto/p1/todo"})
    assert work == launch.PROTOS / "p1" / "todo" / "prototype"
    assert f"BindPaths={work}" in _props(argv)
    cmd = _command(argv)
    assert "--reuid=aipds-proto" in cmd
    assert cmd[-3:] == ["/usr/bin/npm", "run", "start"]
    assert env["PORT"] == "4001"
    assert "CLAUDE_CONFIG_DIR" not in env


@pytest.mark.parametrize("kind,args", [
    ("proto", ["install", "left-pad"]),
    ("proto", ["run", "build"]),
    ("proto", ["exec", "--", "sh"]),
    ("host-build", ["run", "start"]),
    ("host-build", ["install", "--prefix", "/"]),
])
def test_npm_args_outside_the_allowlist_are_refused(launch, kind, args):
    _tree(launch, kind, slug="todo")
    with pytest.raises(launch.LaunchError):
        _run(launch, kind, slug="todo", args=args)


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a b", "a:b", "a%b", "a$b",
                                 'a"b', "a\\b", "a\nb", "x" * 201])
def test_bad_segments_are_refused(launch, bad):
    with pytest.raises(launch.LaunchError):
        _run(launch, "discovery", pid=bad)


def test_hangul_project_ids_are_accepted(launch):
    _tree(launch, "discovery", pid="한글프로젝트")
    argv, _ = _run(launch, "discovery", pid="한글프로젝트")
    assert "Description=AI-PDS discovery 한글프로젝트" in _props(argv)


def test_slug_rules_per_kind(launch):
    _tree(launch, "discovery")
    with pytest.raises(launch.LaunchError):
        _run(launch, "discovery", slug="x")
    with pytest.raises(launch.LaunchError):
        _run(launch, "build")
    with pytest.raises(launch.LaunchError):
        _run(launch, "nope")


def test_a_symlinked_tree_is_refused(launch, tmp_path):
    """링크로 다른 곳을 RW bind하게 만드는 길. 트리를 만드는 것은 백엔드(aipds)다."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _, home = launch.layout("discovery", "p1", None)
    (home / "home").mkdir(parents=True)
    (home / "config").mkdir()
    launch.WORKSPACES.mkdir(parents=True)
    (launch.WORKSPACES / "p1").symlink_to(elsewhere)
    with pytest.raises(launch.LaunchError):
        _run(launch, "discovery")


def test_missing_trees_are_refused_not_created(launch):
    with pytest.raises(launch.LaunchError):
        _run(launch, "discovery")
    assert not launch.WORKSPACES.exists()


def test_unit_names(launch):
    a = launch.unit_name("proto", "p1", "todo")
    assert a == launch.unit_name("proto", "p1", "todo")  # stop이 이름으로 찾는다
    assert a.startswith("aipds-run-proto-") and a.endswith(".service")
    # 해시라서 `a-b`+`c`와 `a`+`b-c`가 부딪히지 않는다.
    assert launch.unit_name("proto", "a-b", "c") != launch.unit_name("proto", "a", "b-c")
    # 에이전트는 겹쳐 뜰 수 있으므로 매번 다르다.
    assert launch.unit_name("discovery", "p1", None) != launch.unit_name("discovery", "p1", None)


def test_stop_is_only_for_hosting_kinds(launch):
    assert launch.build_stop("proto", "p1", "todo")[:2] == ["/usr/bin/systemctl", "stop"]
    with pytest.raises(launch.LaunchError):
        launch.build_stop("discovery", "p1", "todo")
    with pytest.raises(launch.LaunchError):
        launch.build_stop("proto", "..", "todo")


def test_conf_parsing(launch, tmp_path):
    conf = tmp_path / "launch.conf"
    conf.write_text("# comment\nimds_block = 1\n\nother=x\n", encoding="utf-8")
    assert launch.read_conf(conf) == {"imds_block": "1", "other": "x"}
    assert launch.read_conf(tmp_path / "missing") == {}


def test_refuses_to_run_without_root(launch, monkeypatch):
    monkeypatch.setattr(launch.os, "geteuid", lambda: 1000)
    assert launch.main(["check"]) == 2


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no system python")
def test_compiles_under_the_instance_system_python(tmp_path):
    """인스턴스(AL2023)의 /usr/bin/python3는 3.9다. venv의 3.11 문법이 섞이면 root 쪽이 죽는다."""
    target = tmp_path / "aipds_launch.py"
    shutil.copyfile(SCRIPT, target)
    subprocess.run(["/usr/bin/python3", "-m", "py_compile", str(target)], check=True)
