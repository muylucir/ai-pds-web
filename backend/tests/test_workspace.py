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

async def test_list_question_files_finds_top_level_and_nested(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    # top-level (pilot1: discovery-mode-selection-questions.md sits directly under aiplc-docs/)
    await sb.write_file("aiplc-docs/discovery-mode-selection-questions.md", "x")
    # nested (pilot1: discovery/product-strategy/strategy-questions.md)
    await sb.write_file("aiplc-docs/discovery/product-strategy/strategy-questions.md", "y")
    # a non-question file that must NOT be listed
    await sb.write_file("aiplc-docs/audit.md", "z")
    ws = Workspace(sb)
    found = sorted(await ws.list_question_files())
    assert found == [
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]


def test_registry_list_ids_preserves_insertion_order(tmp_path):
    reg = ProjectRegistry()
    for pid in ("p1", "p2", "p3"):
        sb = LocalSandbox(root=tmp_path / pid)
        import asyncio
        asyncio.run(sb.start())
        reg.create(pid, sb)
    assert reg.list_ids() == ["p1", "p2", "p3"]


def test_registry_create_without_name_defaults_to_none(tmp_path):
    # Backward-compat: existing Phase 1 call sites pass no `name` at all.
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path)
    import asyncio
    asyncio.run(sb.start())
    reg.create("p-noname", sb)
    assert reg.get_name("p-noname") is None


def test_registry_create_with_name_stores_it(tmp_path):
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path)
    import asyncio
    asyncio.run(sb.start())
    reg.create("p-named", sb, name="기획전 AI 어시스턴트")
    assert reg.get_name("p-named") == "기획전 AI 어시스턴트"


def test_registry_get_name_unknown_project_raises_keyerror():
    reg = ProjectRegistry()
    try:
        reg.get_name("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
