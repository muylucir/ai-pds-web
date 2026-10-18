# backend/tests/test_agent_home.py — per-project CLI config dir and HOME (aipds/agent_home.py).
from __future__ import annotations

import pytest

from aipds import agent_home


def _shared(tmp_path):
    shared = tmp_path / "discovery-config"
    (shared / "skills" / "a").mkdir(parents=True)
    (shared / "skills" / "a" / "SKILL.md").write_text("A", encoding="utf-8")
    (shared / "CLAUDE.md").write_text("rules v1", encoding="utf-8")
    (shared / "README.md").write_text("for humans", encoding="utf-8")
    return shared


def test_each_project_gets_its_own_dirs():
    a = agent_home.discovery("p1").prepare()
    b = agent_home.discovery("p2").prepare()
    assert a.config != b.config
    assert a.config.is_dir() and a.home.is_dir()
    assert agent_home.build("p1", "s").base != agent_home.proto("p1", "s").base
    proto = agent_home.proto("p1", "s").prepare(with_config=False)
    assert proto.home.is_dir() and not proto.config.exists()


def test_copy_config_copies_content_and_keeps_runtime_state(tmp_path):
    shared = _shared(tmp_path)
    config = agent_home.discovery("p1").prepare().config
    transcript = config / "projects" / "-ws" / "sid.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")

    agent_home.copy_config(shared, config)

    assert (config / "CLAUDE.md").read_text(encoding="utf-8") == "rules v1"
    assert (config / "skills" / "a" / "SKILL.md").is_file()
    assert not (config / "README.md").exists()  # human-facing docs are not CLI content
    assert transcript.is_file()


def test_copy_config_replaces_stale_content(tmp_path):
    """A skill removed from the shared side stays enabled if it remains in the copy."""
    shared = _shared(tmp_path)
    config = agent_home.discovery("p1").prepare().config
    agent_home.copy_config(shared, config)
    (shared / "CLAUDE.md").write_text("rules v2", encoding="utf-8")
    (shared / "skills" / "a" / "SKILL.md").unlink()
    (shared / "skills" / "a").rmdir()
    # Even if the agent modifies the copy, it reverts on the next connection.
    (config / "CLAUDE.md").write_text("tampered", encoding="utf-8")

    agent_home.copy_config(shared, config)

    assert (config / "CLAUDE.md").read_text(encoding="utf-8") == "rules v2"
    assert not (config / "skills" / "a").exists()


def test_copy_config_resets_the_settings_an_agent_could_plant(tmp_path):
    """The agent can write to the config dir and the workspace. If hooks, env, or MCP servers placed there
    carried over to the next run, a single injection would persist for the whole project."""
    shared = _shared(tmp_path)
    config = agent_home.discovery("p1").prepare().config
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    planted = '{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}}'
    (config / "settings.json").write_text(planted, encoding="utf-8")
    for rel in agent_home.WORKSPACE_SETTINGS:
        (ws / rel).write_text(planted, encoding="utf-8")
    (ws / ".claude" / "agents.md").write_text("not ours to remove", encoding="utf-8")

    agent_home.copy_config(shared, config, ws)

    assert (config / "settings.json").read_text(encoding="utf-8") == "{}\n"
    assert not any((ws / rel).exists() for rel in agent_home.WORKSPACE_SETTINGS)
    assert (ws / ".claude" / "agents.md").is_file()


def test_copy_config_does_not_write_through_links(tmp_path):
    """The config dir is a tree the agent writes to. If `skills` or `settings.json` were replaced with links
    pointing outside, the copy would overwrite whatever lies beyond them."""
    shared = _shared(tmp_path)
    config = agent_home.discovery("p1").prepare().config
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other-file").write_text("untouched", encoding="utf-8")
    (config / "settings.json").symlink_to(outside / "other-file")
    (config / "skills").symlink_to(outside, target_is_directory=True)

    agent_home.copy_config(shared, config)

    assert (outside / "other-file").read_text(encoding="utf-8") == "untouched"
    assert sorted(p.name for p in outside.iterdir()) == ["other-file"]
    assert (config / "skills" / "a" / "SKILL.md").is_file()


async def test_purges(tmp_path):
    for home in (agent_home.discovery("p1"), agent_home.build("p1", "s"),
                 agent_home.proto("p1", "s"), agent_home.discovery("p2")):
        home.prepare()
    await agent_home.purge_prototype("p1", "s")
    assert not agent_home.build("p1", "s").base.exists()
    assert not agent_home.proto("p1", "s").base.exists()
    assert agent_home.discovery("p1").base.exists()
    await agent_home.purge_project("p1")
    assert not agent_home.discovery("p1").base.exists()
    assert agent_home.discovery("p2").base.exists()


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b"])
def test_unsafe_segments_are_refused(bad):
    with pytest.raises(ValueError):
        agent_home.discovery(bad)
    with pytest.raises(ValueError):
        agent_home.build("p1", bad)
