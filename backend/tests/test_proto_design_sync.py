# backend/tests/test_proto_design_sync.py
#
# Tests only the workspace side — S3 is not involved here (profile objects are built directly).
from __future__ import annotations

from pathlib import Path

import pytest

from aipds.design_profile import DesignProfile
from aipds.proto.design_sync import (
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
    # Font families get a fallback chain — browsers without the in-house font must not break.
    assert "--font-sans: Pretendard, " in css
    assert ":root {" in css


def test_theme_css_omits_absent_keys():
    css = theme_css({"primary": "#5b2ea6"})
    assert "--ring" not in css


def theme_required_of(css: str, root: Path) -> bool:
    # theme_required looks at disk, so wrap it in a temporary directory to check.
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
    assert "aipds:design:start" in claude
    assert "aipds-theme.css" in claude


def test_sync_is_idempotent_and_does_not_duplicate_the_section(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, profile(), "ko")
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.count("aipds:design:start") == 1


def test_sync_refreshes_changed_tokens(tmp_path):
    sync_design(tmp_path, profile(), "ko")
    sync_design(tmp_path, profile({"primary": "#111111"}), "ko")
    css = (tmp_path / THEME_FILENAME).read_text()
    assert "#111111" in css and "#5b2ea6" not in css


def test_prose_only_profile_writes_an_honest_stub_not_an_empty_theme(tmp_path):
    sync_design(tmp_path, profile(tokens={}), "ko")
    # The wiring is still laid down — if an admin adds tokens later, the colors arrive through re-hosting alone.
    assert (tmp_path / THEME_FILENAME).is_file()
    # But we do not leave a file with **zero variables** that says "generated from
    # the brand profile". Observed 2026-08-19: ship's build agent opened that file,
    # concluded "it is empty, so there is nothing to override", and left the shadcn
    # defaults in place. With the same profile, test1111 read the DESIGN.md prose and
    # carried over the palette — when the enforcing channel is empty, the brand is left
    # to the agent's discretion.
    assert (tmp_path / THEME_FILENAME).read_text() == stub_css()
    assert theme_required(tmp_path) is False


def test_prose_only_profile_tells_the_agent_to_move_the_values_itself(tmp_path):
    sync_design(tmp_path, profile(tokens={}), "ko")
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert "globals.css" in claude
    assert "옮겨라" in claude
    # The values come from that document — the prose is carried over as is.
    assert "여백을 넉넉히" in (tmp_path / DESIGN_FILENAME).read_text()


def test_tokens_arriving_later_rebrand_the_copies_laid_by_the_stub_run(tmp_path):
    # "Rebranding through re-hosting alone" must hold even after passing through the
    # zero-token path: after one stub run the copies were laid down, and when tokens
    # arrive next, the copies are replaced too.
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

    # Deleting would remove the import target and break npm run build.
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
    assert "aipds:design" not in text
    # Other content must survive — this is why we do not delete the whole file.
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
    (app / "globals.css").write_text('@import "./aipds-theme.css";',
                                     encoding="utf-8")
    assert theme_imported(tmp_path) is True


def test_english_project_gets_english_claude_section(tmp_path):
    sync_design(tmp_path, profile(), "en")
    assert "Brand design profile" in (tmp_path / "CLAUDE.md").read_text()


# ---- Final review M1: symbolic links can turn the copy search into an infinite loop ----

def test_theme_copies_terminates_through_a_directory_symlink_cycle(tmp_path):
    """It must terminate even if prototype/ contains a directory symlink pointing to
    itself. A cycle is an infinite loop, not an exception, so before the fix none of
    the try/except blocks in session.start(), the build_complete tool, or POST /host
    caught it, and it simply hung."""
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    (app / "loop").symlink_to(app)  # app/loop -> app itself (cycle)

    assert theme_copies(tmp_path) == []  # merely terminating is the point of this test


def test_theme_copies_excludes_a_symlink_named_like_the_theme_file(tmp_path):
    """A symlink named aipds-theme.css is not accepted as a copy
    -- accepting it would make _write_theme_everywhere call write_text on whatever
    arbitrary path the link points to."""
    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    target = tmp_path / "elsewhere.css"
    target.write_text("/* 다른 파일 */", encoding="utf-8")
    (app / THEME_FILENAME).symlink_to(target)

    assert theme_copies(tmp_path) == []
    sync_design(tmp_path, profile({"primary": "#111111"}), "ko")
    # It must not have written to the target through the symlink.
    assert target.read_text() == "/* 다른 파일 */"


# ---- Final review M2: if there was never a profile, DESIGN.md is not touched ----

def test_sync_none_never_touches_an_agent_authored_design_md_when_we_planted_nothing(tmp_path):
    """Even in a project that never had a profile, sync_design(None, ...) is called on
    every hosting (start_host in routes/prototypes.py). If the build agent created a
    DESIGN.md at the root as its own notes, we must not touch that file unless we have
    planted our marker (aipds-theme.css) -- the same principle as _remove_section
    preserving other people's CLAUDE.md content."""
    (tmp_path / DESIGN_FILENAME).write_text("에이전트 메모", encoding="utf-8")

    sync_design(tmp_path, None, "ko")

    assert (tmp_path / DESIGN_FILENAME).read_text() == "에이전트 메모"
