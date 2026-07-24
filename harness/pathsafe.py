from __future__ import annotations
from pathlib import PurePosixPath

def reject_unsafe(path: str) -> None:
    """Raise ValueError if `path` could escape the workspace root.

    Reject any path that is absolute (leading "/") or contains a ".." path
    segment. Wildcards ("*", "**", "?") are ordinary, non-".." segments and
    pass, so legitimate globs like "aiplc-docs/*-questions.md" are accepted.
    Used by AgentRunner before it forwards any path/glob to S3 or the local
    workspace.
    """
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ValueError(f"unsafe path: {path}")
