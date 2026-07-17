# backend/tests/test_workspace.py
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.workspace import Workspace, ProjectRegistry

FIX = Path(__file__).parent / "fixtures"

async def _seeded(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/strategy-questions.md",
                        (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    await sb.write_file("aiplc-docs/aiplc-state.md",
                        (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    return Workspace(sb)

async def test_get_questions_and_put_answers(tmp_path):
    ws = await _seeded(tmp_path)
    qf = await ws.get_questions("aiplc-docs/strategy-questions.md")
    assert len(qf.questions) == 13
    updated = await ws.put_answers("aiplc-docs/strategy-questions.md", {1: "B"})
    assert next(q for q in updated.questions if q.number == 1).answer == "B"

async def test_get_state(tmp_path):
    ws = await _seeded(tmp_path)
    st = await ws.get_state()
    assert st.project_type == "Greenfield"

async def test_missing_document_returns_empty(tmp_path):
    ws = await _seeded(tmp_path)
    assert await ws.get_document() == ""

async def test_registry_create_and_get(tmp_path):
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path); await sb.start()
    ws = reg.create("p1", sb)
    assert reg.get("p1") is ws
