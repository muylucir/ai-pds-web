from pathlib import Path
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner


async def test_list_artifacts_finds_nested_and_top_level_files(tmp_path: Path):
    r = FakeRunner()
    await r.write_file("aiplc-docs/audit.md", "a")
    await r.write_file("aiplc-docs/aiplc-state.md", "b")
    await r.write_file("aiplc-docs/discovery/discovery-document.md", "c")
    await r.write_file("aiplc-docs/discovery/prototype/prototype-spec.md", "d")
    ws = Workspace(r)
    found = sorted(await ws.list_artifacts())
    assert found == [
        "aiplc-docs/aiplc-state.md",
        "aiplc-docs/audit.md",
        "aiplc-docs/discovery/discovery-document.md",
        "aiplc-docs/discovery/prototype/prototype-spec.md",
    ]


async def test_list_artifacts_includes_non_markdown_files(tmp_path: Path):
    # "artifact" = every file under aiplc-docs/, not just *.md — a later stage
    # may write non-markdown output (e.g. exported JSON); the lister must not
    # silently drop it.
    r = FakeRunner()
    await r.write_file("aiplc-docs/discovery/prototype/preview-snapshot.json", "{}")
    ws = Workspace(r)
    found = await ws.list_artifacts()
    assert "aiplc-docs/discovery/prototype/preview-snapshot.json" in found


async def test_list_artifacts_empty_workspace_returns_empty_list(tmp_path: Path):
    ws = Workspace(FakeRunner())
    assert await ws.list_artifacts() == []


# ---- The artifact list is newest first (2026-08-21) ----
#
# **Why it is needed.** When there is no `activeDoc`, the workspace document viewer falls back to
# the first item in the list (WorkspaceDocPanel). In alphabetical order that first item is almost
# always `aiplc-docs/audit.md` — the audit log opens instead of the document the conversation just
# created.
#
# The measured symptom was the stage before that: the dropdown was full of items, but the body
# said "아직 문서가 없습니다" (no documents yet) (there was no fallback at all). To add a fallback, the
# order has to mean something.

async def test_list_artifacts_returns_newest_first(tmp_path: Path):
    ws = Workspace(FakeRunner())
    await ws.runner.write_file("aiplc-docs/audit.md", "감사")
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ")

    found = await ws.list_artifacts()

    # What was written later comes first. In alphabetical order audit.md would come first, so
    # this assertion tells them apart.
    assert found[0] == "aiplc-docs/discovery/prfaq.md", found


async def test_a_rewrite_moves_a_file_back_to_the_front(tmp_path: Path):
    """When a document is rewritten, it is "the document currently under discussion" — the order
    must reflect that for the fallback to be useful."""
    ws = Workspace(FakeRunner())
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ")
    await ws.runner.write_file("aiplc-docs/audit.md", "감사")
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ 고침")

    found = await ws.list_artifacts()

    assert found[0] == "aiplc-docs/discovery/prfaq.md", found
