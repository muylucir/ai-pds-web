# backend/tests/test_local_sandbox.py
import json
import pytest
import tempfile
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent
from pathfinder.models import QuestionFile

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

async def test_default_script_first_turn_emits_stage_and_questions():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    evs = [e async for e in sb.send_message("시작")]
    kinds = [e.kind for e in evs]
    assert "stage" in kinds and "questions" in kinds and kinds[-1] == "done"
    q = next(e for e in evs if e.kind == "questions")
    body = json.loads(q.payload)
    assert body["interrupt_id"] == "local-i-1"
    assert len(body["questions"]["questions"]) == 2
    # questions pending until answered
    assert await sb.pending() == q.payload
    # must round-trip through the real QuestionFile model (parse_ok required)
    QuestionFile.model_validate(body["questions"])

async def test_answers_complete_stage_and_emit_document():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    [e async for e in sb.send_message("시작")]
    evs = [e async for e in sb.send_answers({"1": "A", "2": "B"})]
    kinds = [e.kind for e in evs]
    assert "message" in kinds and "stage" in kinds and "document" in kinds
    assert kinds[-1] == "done"
    assert await sb.pending() is None

async def test_answers_without_pending_errors():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert evs[0].kind == "error"

async def test_custom_script_questions_event_arms_pending_and_answers(tmp_path: Path):
    payload = json.dumps({"interrupt_id": "custom-i-1", "questions": {
        "name": "custom", "preamble": None, "parse_ok": True, "raw_markdown": None,
        "questions": [{"number": 1, "category": None, "text": "q?", "answer": None,
                       "options": [{"letter": "A", "text": "yes", "is_other": False,
                                    "recommended": True}]}]}}, ensure_ascii=False)

    def script(text, sb):
        return [AgentEvent(kind="questions", payload=payload), AgentEvent(kind="done")]

    sb = LocalSandbox(root=tmp_path, script=script)
    await sb.start()
    await _collect(sb.send_message("go"))
    assert await sb.pending() == payload

    evs = await _collect(sb.send_answers({"1": "A"}))
    kinds = [e.kind for e in evs]
    assert "error" not in kinds
    assert kinds[-1] == "done"
    assert await sb.pending() is None

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

async def test_list_files_rejects_traversal_glob(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.list_files("../*")

async def test_read_file_rejects_traversal(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.read_file("../secret.md")

async def test_rejects_absolute_path(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("/etc/evil.md", "x")
