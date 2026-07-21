import pytest
from pathfinder.pathsafe import reject_unsafe

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
    # A literal ".." substring inside a filename is NOT a traversal (matches
    # LocalSandbox: it checks Path(path).parts, so "..foo" is a safe name).
    reject_unsafe("aiplc-docs/..foo.md")  # must not raise
