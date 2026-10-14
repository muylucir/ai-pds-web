import os

import pytest
from aipds.pathsafe import (read_text_beneath, reject_unsafe, reject_unsafe_segment,
                            stat_beneath, unlink_beneath, write_beneath)

def test_accepts_normal_relative_paths():
    for ok in ("aiplc-docs/audit.md", "aiplc-docs/discovery/x.md", "a-questions.md",
               "aiplc-docs/*-questions.md", "aiplc-docs/**/*.md"):
        reject_unsafe(ok)  # must not raise

def test_rejects_absolute_paths():
    with pytest.raises(ValueError):
        reject_unsafe("/etc/passwd")

def test_rejects_parent_traversal_segment():
    for bad in ("../evil.md", "aiplc-docs/../../evil.md", "../*"):
        with pytest.raises(ValueError):
            reject_unsafe(bad)

def test_dotdot_only_as_whole_segment():
    # A literal ".." substring inside a filename is NOT a traversal
    # (reject_unsafe checks Path(path).parts, so "..foo" is a safe name).
    reject_unsafe("aiplc-docs/..foo.md")  # must not raise


# ---- reject_unsafe_segment: one path segment, for {pid}/{slug} ----

def test_segment_accepts_an_ordinary_name():
    for ok in ("todo-app", "한글-앱", "demo", "a", "..foo", "v1.2"):
        reject_unsafe_segment(ok)  # must not raise


def test_segment_rejects_what_collapses_a_path():
    """The gap this exists to close: "" and "." both PASS reject_unsafe --
    PurePosixPath reduces them to zero parts -- yet `root / pid / slug` then
    resolves to `root / pid`, so "delete the slug's directory" deletes every
    SIBLING slug. ".." climbs one further and takes every project with it."""
    for bad in ("", ".", "..", "./x", "x/.."):
        with pytest.raises(ValueError):
            reject_unsafe_segment(bad)


def test_segment_rejects_a_nested_path():
    """One URL parameter must not silently address a nested path -- these are
    safe for reject_unsafe (no traversal) but are not ONE segment."""
    for bad in ("a/b", "a/", "aiplc-docs/x.md"):
        with pytest.raises(ValueError):
            reject_unsafe_segment(bad)


def test_segment_still_rejects_absolute_and_traversal():
    for bad in ("/etc/passwd", "/", "../evil"):
        with pytest.raises(ValueError):
            reject_unsafe_segment(bad)


# ---- 링크를 따라가지 않는 읽기·쓰기 (에이전트가 쓰는 트리) ----

def test_write_beneath_creates_parents_and_writes(tmp_path):
    write_beneath(tmp_path, "a/b/c.txt", "hi")
    assert (tmp_path / "a/b/c.txt").read_text() == "hi"
    write_beneath(tmp_path, "a/b/c.txt", " there", append=True)
    assert read_text_beneath(tmp_path, "a/b/c.txt") == "hi there"


def test_write_beneath_replaces_links_instead_of_writing_through_them(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("untouched")
    root = tmp_path / "root"
    root.mkdir()
    (root / "d").symlink_to(outside, target_is_directory=True)
    (root / "f.txt").symlink_to(outside / "f.txt")

    write_beneath(root, "d/f.txt", "new")
    write_beneath(root, "f.txt", "new")

    assert (outside / "f.txt").read_text() == "untouched"
    assert not (root / "d").is_symlink() and not (root / "f.txt").is_symlink()
    assert (root / "d/f.txt").read_text() == "new"


def test_reads_refuse_links_and_non_regular_files(tmp_path):
    outside = tmp_path / "outside-file"
    outside.write_text("outside")
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "link").symlink_to(outside)
    (root / "dlink").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(root / "d" / "fifo")

    for rel in ("link", "dlink/outside-file", "d/fifo"):
        with pytest.raises(OSError):
            read_text_beneath(root, rel)
        assert stat_beneath(root, rel) is None


def test_unlink_beneath_removes_the_link_not_its_target(tmp_path):
    outside = tmp_path / "keep"
    outside.write_text("keep")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    (root / "dlink").symlink_to(tmp_path, target_is_directory=True)

    assert unlink_beneath(root, "link") is True
    assert not (root / "link").exists() and outside.exists()
    # 상위 조각이 링크면 그 너머를 지우지 않는다.
    assert unlink_beneath(root, "dlink/keep") is False
    assert outside.exists()


def test_beneath_helpers_reject_escaping_paths(tmp_path):
    for rel in ("../x", "/etc/passwd"):
        with pytest.raises(ValueError):
            write_beneath(tmp_path, rel, "x")
        with pytest.raises(ValueError):
            read_text_beneath(tmp_path, rel)
