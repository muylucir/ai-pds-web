# backend/tests/test_workspace.py
from pathlib import Path
from aipds.workspace import Workspace, ProjectRegistry
from fakes.in_memory_s3 import FakeS3Store

FIX = Path(__file__).parent / "fixtures"


class FakeRunner:
    """Workspace가 의존하는 파일 계약 ops만 가진 최소 러너 (S3 backed)."""
    def __init__(self, s3=None):
        self._s3 = s3 or FakeS3Store()
        self.input_holder = None

    async def read_file(self, rel):
        return await self._s3.get(rel)

    async def write_file(self, rel, content):
        await self._s3.put(rel, content)

    async def list_files(self, glob):
        from aipds.globmatch import matches_glob
        keys = await self._s3.list("")
        return sorted(k for k in keys if matches_glob(k, glob))


async def _seeded():
    r = FakeRunner()
    await r.write_file("aiplc-docs/strategy-questions.md",
                       (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    await r.write_file("aiplc-docs/aiplc-state.md",
                       (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    return Workspace(r)

async def test_get_questions_and_put_answers():
    ws = await _seeded()
    qf = await ws.get_questions("aiplc-docs/strategy-questions.md")
    assert len(qf.questions) == 13
    updated = await ws.put_answers("aiplc-docs/strategy-questions.md", {1: "B"})
    assert next(q for q in updated.questions if q.number == 1).answer == "B"

async def test_get_state():
    ws = await _seeded()
    st = await ws.get_state()
    assert st.project_type == "Greenfield"

async def test_missing_document_returns_empty():
    ws = await _seeded()
    assert await ws.get_document() == ""

async def test_registry_create_and_get():
    reg = ProjectRegistry()
    r = FakeRunner()
    reg.register("p1")
    ws = reg.attach("p1", Workspace(r))
    assert reg.get("p1") is ws

async def test_list_question_files_finds_top_level_and_nested():
    r = FakeRunner()
    # top-level (pilot1: discovery-mode-selection-questions.md sits directly under aiplc-docs/)
    await r.write_file("aiplc-docs/discovery-mode-selection-questions.md", "x")
    # nested (pilot1: discovery/product-strategy/strategy-questions.md)
    await r.write_file("aiplc-docs/discovery/product-strategy/strategy-questions.md", "y")
    # a non-question file that must NOT be listed
    await r.write_file("aiplc-docs/audit.md", "z")
    ws = Workspace(r)
    found = sorted(await ws.list_question_files())
    assert found == [
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]


def test_registry_list_ids_preserves_insertion_order():
    reg = ProjectRegistry()
    for pid in ("p1", "p2", "p3"):
        reg.register(pid)
        reg.attach(pid, Workspace(FakeRunner()))
    assert reg.list_ids() == ["p1", "p2", "p3"]


def test_registry_create_without_name_defaults_to_none():
    # Backward-compat: existing Phase 1 call sites pass no `name` at all.
    reg = ProjectRegistry()
    reg.register("p-noname")
    reg.attach("p-noname", Workspace(FakeRunner()))
    assert reg.get_name("p-noname") is None


def test_registry_create_with_name_stores_it():
    reg = ProjectRegistry()
    reg.register("p-named", name="기획전 AI 어시스턴트")
    reg.attach("p-named", Workspace(FakeRunner()))
    assert reg.get_name("p-named") == "기획전 AI 어시스턴트"


def test_registry_get_name_unknown_project_raises_keyerror():
    reg = ProjectRegistry()
    try:
        reg.get_name("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
