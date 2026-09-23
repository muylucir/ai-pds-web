# backend/tests/test_agent_home.py — 프로젝트별 CLI config dir과 HOME(aipds/agent_home.py).
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
    assert not (config / "README.md").exists()  # 사람용 문서는 CLI 내용이 아니다
    assert transcript.is_file()


def test_copy_config_replaces_stale_content(tmp_path):
    """공유 쪽에서 빠진 스킬이 사본에 남으면 계속 켜진다."""
    shared = _shared(tmp_path)
    config = agent_home.discovery("p1").prepare().config
    agent_home.copy_config(shared, config)
    (shared / "CLAUDE.md").write_text("rules v2", encoding="utf-8")
    (shared / "skills" / "a" / "SKILL.md").unlink()
    (shared / "skills" / "a").rmdir()
    # 에이전트가 사본을 고쳐도 다음 연결에서 원래대로 돌아온다.
    (config / "CLAUDE.md").write_text("tampered", encoding="utf-8")

    agent_home.copy_config(shared, config)

    assert (config / "CLAUDE.md").read_text(encoding="utf-8") == "rules v2"
    assert not (config / "skills" / "a").exists()


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
