from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.workspace import Workspace


async def test_list_artifacts_finds_nested_and_top_level_files(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "a")
    await sb.write_file("aiplc-docs/aiplc-state.md", "b")
    await sb.write_file("aiplc-docs/discovery/discovery-document.md", "c")
    await sb.write_file("aiplc-docs/discovery/prototype/prototype-spec.md", "d")
    ws = Workspace(sb)
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
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/discovery/prototype/preview-snapshot.json", "{}")
    ws = Workspace(sb)
    found = await ws.list_artifacts()
    assert "aiplc-docs/discovery/prototype/preview-snapshot.json" in found


async def test_list_artifacts_empty_workspace_returns_empty_list(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    ws = Workspace(sb)
    assert await ws.list_artifacts() == []
