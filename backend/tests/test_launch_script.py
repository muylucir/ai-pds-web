# backend/tests/test_launch_script.py — the decision logic of infra/scripts/aipds-launch (the root launch wrapper).
#
# The wrapper is a path to becoming root via sudo, so "what it rejects" is the spec itself.
# It does not call the real systemd-run — it inspects the argv and env that `build_run`
# builds. The effects of the sandbox itself (whether other trees are hidden, whether IMDS is
# blocked) were verified by measurement on the instance (docs plan 3·4-BC §6.1).
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
    # The entire app tree disappears under an empty tmpfs — other projects' trees, and the
    # app source and git checkout too. The only things visible again are its own tree and
    # the CLI binary (read-only).
    assert f"TemporaryFileSystem={launch.APP}" in props
    assert f"BindReadOnlyPaths={CLAUDE}" in props
    assert not [p for p in props if p.startswith("TemporaryFileSystem=")
                and p != f"TemporaryFileSystem={launch.APP}"]
    cmd = _command(argv)
    # PID namespace → uid switch → CLI. So that other units with the same uid cannot see it
    # via /proc/<pid>/root.
    assert cmd[:2] == ["/usr/bin/unshare", "--pid"]
    assert "--reuid=aipds-agent" in cmd and "--regid=aipds-work" in cmd
    assert cmd[-3:] == [str(CLAUDE), "--output-format", "stream-json"]
    assert env["CLAUDE_CONFIG_DIR"] == str(home / "config")
    assert env["HOME"] == str(home / "home")


def test_config_dir_from_the_caller_is_ignored(launch):
    """Pointing it at a shared config dir would expose other projects' transcripts."""
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
        # things that must not be passed through
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
    """argv is visible in `ps` — credential tokens must not be put on it."""
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
    """systemd expands `${VAR}` in ExecStart arguments (measured). It must be passed as `$$`
    to arrive as `$`."""
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
    """The bundled CLI explicitly creates the config dir and transcripts as 0700/0600
    (depending on the version) — the backend would then be able neither to resume nor to
    delete that project. The only path is its own computed agent-home.

    **There must be no prefix.** With `+` or similar attached, systemd-run puts it into
    ExecStopPostEx, and systemd 252 discards that on the next daemon-reload (measured in
    production) — and then this chmod never runs even once. For root inside the sandbox to
    traverse and chmod another's tree, two capabilities must be in the bounding set."""
    _, home = _tree(launch, kind, slug=slug)
    args = {"host-build": ["install"], "proto": ["run", "start"]}.get(kind, [])
    argv, _ = _run(launch, kind, slug=slug, args=args)
    props = _props(argv)
    stops = [p for p in props if p.startswith("ExecStopPost")]
    assert stops == [f"ExecStopPost=/usr/bin/chmod -R g+rwX {home}"]
    caps = next(p for p in props if p.startswith("CapabilityBoundingSet=")).split("=", 1)[1].split()
    assert {"CAP_FOWNER", "CAP_DAC_READ_SEARCH"} <= set(caps)


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


def test_npm_kinds_see_no_app_tree_and_bind_no_binary(launch):
    """npm lives in /usr/bin — nothing more needs to be bound from the app tree."""
    work, home = _tree(launch, "proto", slug="s1")
    props = _props(_run(launch, "proto", slug="s1", args=["run", "start"])[0])
    assert f"TemporaryFileSystem={launch.APP}" in props
    assert not [p for p in props if p.startswith("BindReadOnlyPaths=")]
    assert f"BindPaths={work}" in props and f"BindPaths={home}" in props


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
    """A path to making it RW-bind somewhere else via a link. The tree is created by the
    backend (aipds)."""
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
    assert a == launch.unit_name("proto", "p1", "todo")  # stop looks it up by name
    assert a.startswith("aipds-run-proto-") and a.endswith(".service")
    # Because it is a hash, `a-b`+`c` and `a`+`b-c` do not collide.
    assert launch.unit_name("proto", "a-b", "c") != launch.unit_name("proto", "a", "b-c")
    # Agents can run concurrently, so it differs every time.
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
    """The instance's (AL2023) /usr/bin/python3 is 3.9. If the venv's 3.11 syntax slips in,
    the root side dies."""
    target = tmp_path / "aipds_launch.py"
    shutil.copyfile(SCRIPT, target)
    subprocess.run(["/usr/bin/python3", "-m", "py_compile", str(target)], check=True)
