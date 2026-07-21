from pathlib import Path
from pathfinder.workspace import Workspace
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
