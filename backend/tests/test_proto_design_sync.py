# backend/tests/test_proto_design_sync.py
#
# 워크스페이스 반영만 시험한다 — S3는 여기 없다(프로필 객체를 직접 만든다).
from __future__ import annotations

from pathlib import Path

import pytest

from pathfinder.design_profile import DesignProfile
from pathfinder.proto.design_sync import (
    DESIGN_FILENAME, THEME_FILENAME, stub_css, sync_design, theme_copies,
    theme_css, theme_imported, theme_required,
)


def profile(tokens=None, prose="## 톤\n여백을 넉넉히.") -> DesignProfile:
    return DesignProfile(filename="acme.md", uploaded_at="2026-08-15T00:00:00+00:00",
                         uploaded_by="admin@x", markdown="(원문)",
                         tokens=tokens if tokens is not None
                         else {"primary": "#5b2ea6", "radius": "0.75rem"},
                         prose=prose)


def test_theme_css_maps_keys_to_css_variables():
    css = theme_css({"primary": "#5b2ea6", "primary_foreground": "#ffffff",
                     "radius": "0.75rem", "font_sans": "Pretendard"})
    assert "--primary: #5b2ea6;" in css
    assert "--primary-foreground: #ffffff;" in css
    assert "--radius: 0.75rem;" in css
    # 서체는 폴백 체인을 붙인다 — 사내 폰트가 없는 브라우저가 깨지지 않아야 한다.
    assert "--font-sans: Pretendard, " in css
    assert ":root {" in css


def test_theme_css_omits_absent_keys():
    css = theme_css({"primary": "#5b2ea6"})
    assert "--ring" not in css


def theme_required_of(css: str, root: Path) -> bool:
    # theme_required는 디스크를 보므로 임시 디렉토리로 감싸 확인한다.
    root.mkdir()
    (root / THEME_FILENAME).write_text(css, encoding="utf-8")
    return theme_required(root)


def test_stub_and_theme_are_distinguishable(tmp_path):
    assert theme_required_of(stub_css(), tmp_path / "stub") is False
    assert theme_required_of(theme_css({}), tmp_path / "empty") is True


def test_sync_writes_theme_and_design_and_claude_section(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    assert "--primary: #5b2ea6;" in (tmp_path / THEME_FILENAME).read_text()
    assert "여백을 넉넉히" in (tmp_path / DESIGN_FILENAME).read_text()
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert "pathfinder:design:start" in claude
    assert "pathfinder-theme.css" in claude


def test_sync_is_idempotent_and_does_not_duplicate_the_section(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, profile(), "ko")
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.count("pathfinder:design:start") == 1


def test_sync_refreshes_changed_tokens(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, profile({"primary": "#111111"}), "ko")
    css = (tmp_path / THEME_FILENAME).read_text()
    assert "#111111" in css and "#5b2ea6" not in css


def test_prose_only_profile_writes_an_honest_stub_not_an_empty_theme(tmp_path):
    sync_design(tmp_path, profile(tokens={}), "ko")
    # 배선은 그대로 깐다 — admin이 나중에 토큰을 넣으면 재호스팅만으로 색이 온다.
    assert (tmp_path / THEME_FILENAME).is_file()
    # 그러나 "브랜드 프로필에서 생성됨"이라고 적힌 **변수 0개** 파일을 두지
    # 않는다. 2026-08-19 실측: ship의 빌드 에이전트가 그 파일을 열고 "비어 있으니
    # 덮을 것이 없다"고 판단해 shadcn 기본값을 그대로 뒀다. 같은 프로필에서
    # test1111은 DESIGN.md 산문을 읽어 팔레트를 옮겼다 — 강제 채널이 비면
    # 브랜드가 에이전트 자율에 맡겨진다.
    assert (tmp_path / THEME_FILENAME).read_text() == stub_css()
    assert theme_required(tmp_path) is False


def test_prose_only_profile_tells_the_agent_to_move_the_values_itself(tmp_path):
    sync_design(tmp_path, profile(tokens={}), "ko")
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert "globals.css" in claude
    assert "옮겨라" in claude
    # 값의 출처는 그 문서다 — 산문은 그대로 실린다.
    assert "여백을 넉넉히" in (tmp_path / DESIGN_FILENAME).read_text()


def test_tokens_arriving_later_rebrand_the_copies_laid_by_the_stub_run(tmp_path):
    # "재호스팅만으로 리브랜딩"이 0토큰 경로를 지나서도 성립해야 한다: 스텁으로
    # 한 번 돈 뒤 사본이 놓였고, 그 다음 토큰이 들어오면 사본까지 갈린다.
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    sync_design(tmp_path, profile(tokens={}), "ko")
    (app / THEME_FILENAME).write_text(stub_css(), encoding="utf-8")

    sync_design(tmp_path, profile(), "ko")

    assert "--primary: #5b2ea6;" in (app / THEME_FILENAME).read_text()
    assert theme_required(tmp_path) is True


def test_token_only_profile_removes_the_design_md(tmp_path):
    sync_design(tmp_path, profile(prose=""), "ko")
    assert not (tmp_path / DESIGN_FILENAME).exists()
    assert theme_required(tmp_path) is True


def test_copies_under_prototype_are_synced(tmp_path):
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    (app / THEME_FILENAME).write_text("/* 낡은 사본 */", encoding="utf-8")
    sync_design(tmp_path, profile({"primary": "#111111"}), "ko")
    assert "#111111" in (app / THEME_FILENAME).read_text()
    assert theme_copies(tmp_path) == [app / THEME_FILENAME]


def test_node_modules_and_next_are_not_searched(tmp_path):
    for junk in ("node_modules", ".next"):
        d = tmp_path / "prototype" / junk
        d.mkdir(parents=True)
        (d / THEME_FILENAME).write_text("/* 남의 것 */", encoding="utf-8")
    sync_design(tmp_path, profile(), "ko")
    assert theme_copies(tmp_path) == []


def test_removed_profile_stubs_instead_of_deleting(tmp_path):
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    sync_design(tmp_path, profile(), "ko")
    (app / THEME_FILENAME).write_text(
        (tmp_path / THEME_FILENAME).read_text(), encoding="utf-8")

    sync_design(tmp_path, None, "ko")

    # 삭제하면 import 대상이 사라져 npm run build가 깨진다.
    assert (tmp_path / THEME_FILENAME).is_file()
    assert (app / THEME_FILENAME).is_file()
    assert "no brand profile" in (app / THEME_FILENAME).read_text()
    assert not (tmp_path / DESIGN_FILENAME).exists()
    assert theme_required(tmp_path) is False


def test_removed_profile_drops_the_claude_section(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# 다른 규칙\n유지되어야 한다.\n",
                                        encoding="utf-8")
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, None, "ko")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "pathfinder:design" not in text
    # 남의 내용은 살아 있어야 한다 — 파일을 통째로 지우지 않는 이유다.
    assert "다른 규칙" in text


def test_claude_md_is_deleted_when_only_our_section_was_there(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, None, "ko")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_no_profile_and_no_prior_file_writes_nothing(tmp_path):
    sync_design(tmp_path, None, "ko")
    assert list(tmp_path.iterdir()) == []


def test_theme_imported_requires_a_reference(tmp_path):
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    sync_design(tmp_path, profile(), "ko")
    (app / THEME_FILENAME).write_text("/* 사본 */", encoding="utf-8")
    assert theme_imported(tmp_path) is False
    (app / "globals.css").write_text('@import "./pathfinder-theme.css";',
                                     encoding="utf-8")
    assert theme_imported(tmp_path) is True


def test_english_project_gets_english_claude_section(tmp_path):
    sync_design(tmp_path, profile(), "en")
    assert "Brand design profile" in (tmp_path / "CLAUDE.md").read_text()


# ---- 최종 리뷰 M1: 심볼릭 링크는 사본 탐색을 무한 루프로 만들 수 있다 ----

def test_theme_copies_terminates_through_a_directory_symlink_cycle(tmp_path):
    """prototype/ 아래에 자기 자신을 가리키는 디렉토리 심볼릭 링크가 있어도
    끝나야 한다. 순환은 예외가 아니라 무한 루프라서, 고치기 전에는
    session.start()·build_complete 도구·POST /host 어느 쪽의 try/except도
    잡지 못하고 그대로 매달렸다."""
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    (app / "loop").symlink_to(app)  # app/loop -> app 자신(순환)

    assert theme_copies(tmp_path) == []  # 끝나기만 해도 이 테스트의 요점이다


def test_theme_copies_excludes_a_symlink_named_like_the_theme_file(tmp_path):
    """이름이 pathfinder-theme.css인 심볼릭 링크는 사본으로 인정하지 않는다
    -- 인정하면 _write_theme_everywhere가 그 링크가 가리키는 임의의 경로에
    write_text하게 된다."""
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    target = tmp_path / "elsewhere.css"
    target.write_text("/* 다른 파일 */", encoding="utf-8")
    (app / THEME_FILENAME).symlink_to(target)

    assert theme_copies(tmp_path) == []
    sync_design(tmp_path, profile({"primary": "#111111"}), "ko")
    # 심볼릭 링크를 통해 타깃에 쓰지 않았어야 한다.
    assert target.read_text() == "/* 다른 파일 */"


# ---- 최종 리뷰 M2: 프로필이 한 번도 없었으면 DESIGN.md를 건드리지 않는다 ----

def test_sync_none_never_touches_an_agent_authored_design_md_when_we_planted_nothing(tmp_path):
    """프로필이 한 번도 없었던 프로젝트에서도 sync_design(None, ...)이 매
    호스팅마다 불린다(routes/prototypes.py의 start_host). 빌드 에이전트가
    자기 메모로 루트에 DESIGN.md를 만들어 뒀다면, 우리가 흔적
    (pathfinder-theme.css)을 심은 적이 없는 한 그 파일을 건드리면 안 된다 --
    _remove_section이 남의 CLAUDE.md 내용을 보존하는 것과 같은 원칙이다."""
    (tmp_path / DESIGN_FILENAME).write_text("에이전트 메모", encoding="utf-8")

    sync_design(tmp_path, None, "ko")

    assert (tmp_path / DESIGN_FILENAME).read_text() == "에이전트 메모"
