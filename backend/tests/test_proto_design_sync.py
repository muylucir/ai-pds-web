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


def test_prose_only_profile_writes_theme_without_tokens(tmp_path):
    sync_design(tmp_path, profile(tokens={}), "ko")
    # 배선을 미리 깐다 — admin이 나중에 토큰을 넣으면 재호스팅만으로 색이 온다.
    assert (tmp_path / THEME_FILENAME).is_file()
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
