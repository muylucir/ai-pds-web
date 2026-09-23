# backend/tests/test_transcript_restore.py — 재개 트랜스크립트를 로컬로 되살리기.
#
# `restore`는 SDK의 비공개 materialize_resume_session을 빌려 쓴다. 이 파일이 **실제 SDK**로
# 그 계약을 고정한다 — SDK를 올렸을 때 복원 경로가 조용히 빠지면 여기서 드러난다
# (pyproject의 SDK 하한 주석: 올린 뒤에는 테스트를 돌린다).
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
    """로컬이 미러보다 새로울 수 있다 — 미러 쓰기는 턴 경계에서 모아서 나간다."""
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
    """SDK가 재개 때 임시 config dir(CLAUDE.md·스킬 없음)로 CLI를 돌리지 않는다는 것."""
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.session_resume import materialize_resume_session

    inner = InMemorySessionStore()
    sid = str(uuid.uuid4())
    await _seed(inner, str(tmp_path), sid, _entries(1))
    options = ClaudeAgentOptions(cwd=str(tmp_path), resume=sid,
                                 session_store=MirrorOnlyStore(inner))
    assert await materialize_resume_session(options) is None
