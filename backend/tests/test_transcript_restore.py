# backend/tests/test_transcript_restore.py — restoring the resume transcript locally.
#
# `restore` borrows the SDK's private materialize_resume_session. This file pins that contract with the
# **real SDK** — if the restore path silently drops out when the SDK is upgraded, it shows up here
# (the SDK lower-bound comment in pyproject: run the tests after upgrading).
from __future__ import annotations

import json
import uuid

from claude_agent_sdk import InMemorySessionStore, project_key_for_directory

from aipds.agent.claude_driver import _transcript_path
from aipds.transcript_restore import MirrorOnlyStore, restore


async def _seed(store, cwd, session_id, entries):
    await store.append({"project_key": project_key_for_directory(cwd),
                        "session_id": session_id}, entries)


def _entries(n=2):
    return [{"type": "user", "uuid": str(uuid.uuid4()), "message": {"content": f"m{i}"}}
            for i in range(n)]


async def test_restore_writes_where_the_cli_and_the_driver_look(tmp_path):
    cwd = tmp_path / "ws"
    cwd.mkdir()
    config = tmp_path / "config"
    sid = str(uuid.uuid4())
    store = InMemorySessionStore()
    await _seed(store, str(cwd), sid, _entries(3))

    assert await restore(store, config, str(cwd), sid) is True

    path = _transcript_path(str(config), str(cwd), sid)
    assert path.is_file()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["message"]["content"] for line in lines] == ["m0", "m1", "m2"]


async def test_restore_reports_a_missing_session(tmp_path):
    assert await restore(InMemorySessionStore(), tmp_path / "config",
                         str(tmp_path), str(uuid.uuid4())) is False


async def test_restore_never_overwrites_a_local_copy(tmp_path):
    """Local may be newer than the mirror — mirror writes go out batched at turn boundaries."""
    cwd = tmp_path / "ws"
    cwd.mkdir()
    config = tmp_path / "config"
    sid = str(uuid.uuid4())
    store = InMemorySessionStore()
    await _seed(store, str(cwd), sid, _entries(1))
    path = _transcript_path(str(config), str(cwd), sid)
    path.parent.mkdir(parents=True)
    path.write_text('{"local": true}\n', encoding="utf-8")

    await restore(store, config, str(cwd), sid)

    assert path.read_text(encoding="utf-8") == '{"local": true}\n'


async def test_mirror_only_store_writes_through_and_reads_nothing():
    inner = InMemorySessionStore()
    store = MirrorOnlyStore(inner)
    key = {"project_key": "k", "session_id": str(uuid.uuid4())}
    await store.append(key, _entries(1))
    assert await store.load(key) is None
    assert await inner.load(key)


async def test_the_sdk_does_not_materialize_through_a_mirror_only_store(tmp_path):
    """That the SDK does not run the CLI with a temporary config dir (no CLAUDE.md or skills) on resume."""
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.session_resume import materialize_resume_session

    inner = InMemorySessionStore()
    sid = str(uuid.uuid4())
    await _seed(inner, str(tmp_path), sid, _entries(1))
    options = ClaudeAgentOptions(cwd=str(tmp_path), resume=sid,
                                 session_store=MirrorOnlyStore(inner))
    assert await materialize_resume_session(options) is None


async def test_restore_does_not_write_through_a_planted_link(tmp_path):
    """The config dir is written by the agent. If `projects` were swapped for a link pointing outside,
    the transcript would be written beyond it with the backend's uid."""
    cwd = tmp_path / "ws"
    cwd.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (config / "projects").symlink_to(outside, target_is_directory=True)
    sid = str(uuid.uuid4())
    store = InMemorySessionStore()
    await _seed(store, str(cwd), sid, _entries(1))

    assert await restore(store, config, str(cwd), sid) is True

    assert list(outside.iterdir()) == []
    assert _transcript_path(str(config), str(cwd), sid).is_file()
