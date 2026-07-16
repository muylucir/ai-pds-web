# backend/tests/test_local_sandbox.py
import pytest
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

async def _collect(aiter):
    return [e async for e in aiter]

async def test_read_write_roundtrip(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "hello")
    assert await sb.read_file("aiplc-docs/audit.md") == "hello"

async def test_path_escape_rejected(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")

async def test_default_script_echoes(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    events = await _collect(sb.send_message("승인"))
    assert events[0].kind == "message" and "승인" in events[0].text
    assert events[-1].kind == "done"

async def test_custom_script_can_write_files_and_emit(tmp_path: Path):
    def script(text, sb):
        return [AgentEvent(kind="file_changed", path="aiplc-docs/x.md"),
                AgentEvent(kind="done")]
    sb = LocalSandbox(root=tmp_path, script=script)
    await sb.start()
    events = await _collect(sb.send_message("go"))
    assert events[0].kind == "file_changed"
    assert events[0].path == "aiplc-docs/x.md"

async def test_list_files_glob(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]
