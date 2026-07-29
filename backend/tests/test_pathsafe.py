import pytest
from pathfinder.pathsafe import reject_unsafe, reject_unsafe_segment

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
