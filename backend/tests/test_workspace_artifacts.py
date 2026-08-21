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


# ---- 산출물 목록은 최신 순이다 (2026-08-21) ----
#
# **왜 필요한가.** 워크스페이스 문서 뷰어가 `activeDoc`이 없을 때 목록의 첫 항목으로
# 떨어진다(WorkspaceDocPanel). 알파벳 순이면 그 첫 항목이 거의 항상
# `aiplc-docs/audit.md`다 — 대화가 방금 만든 문서가 아니라 감사 로그가 열린다.
#
# 실측한 증상은 그 앞 단계였다: 드롭다운에는 항목이 가득한데 본문이 "아직 문서가
# 없습니다"였다(폴백 자체가 없었다). 폴백을 넣으려면 순서가 의미를 가져야 한다.

async def test_list_artifacts_returns_newest_first(tmp_path: Path):
    ws = Workspace(FakeRunner())
    await ws.runner.write_file("aiplc-docs/audit.md", "감사")
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ")

    found = await ws.list_artifacts()

    # 나중에 쓴 것이 앞에 온다. 알파벳 순이면 audit.md가 앞이므로 이 단정이 갈린다.
    assert found[0] == "aiplc-docs/discovery/prfaq.md", found


async def test_a_rewrite_moves_a_file_back_to_the_front(tmp_path: Path):
    """문서를 고쳐 쓰면 그것이 "지금 대화 중인 문서"다 — 순서가 그것을 반영해야
    폴백이 쓸모 있다."""
    ws = Workspace(FakeRunner())
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ")
    await ws.runner.write_file("aiplc-docs/audit.md", "감사")
    await ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "PRFAQ 고침")

    found = await ws.list_artifacts()

    assert found[0] == "aiplc-docs/discovery/prfaq.md", found
