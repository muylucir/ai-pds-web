# harness/globmatch.py
# vendored from backend/pathfinder/sandbox/globmatch.py — keep in sync.
# The harness is a standalone deployable (runs inside the MicroVM, no access
# to the backend package), so this module is copied rather than imported.
from __future__ import annotations
import fnmatch


def matches_glob(path: str, glob: str) -> bool:
    """fnmatch-based glob matching that implements pathlib.Path.glob '**'
    semantics (single '**' supported): '**' matches ZERO or more path
    segments, so 'dir/**/*' matches both a direct child ('dir/audit.md') and
    a nested file ('dir/sub/audit.md'). Plain fnmatch.fnmatch requires '**'
    to consume at least one literal '/', so it silently misses top-level
    files under a '**'-globbed directory -- a real bug relative to
    pathlib.Path.glob, whose '**' already matches top-level files. Globs
    without '**' fall back to plain fnmatch, unchanged, so single-level glob
    behavior (e.g. 'aiplc-docs/*-questions.md') is preserved exactly.
    """
    if "**" not in glob:
        return fnmatch.fnmatch(path, glob)
    prefix, _, suffix = glob.partition("**")
    suffix = suffix.lstrip("/") or "*"
    return path.startswith(prefix) and fnmatch.fnmatch(path[len(prefix):], suffix)
