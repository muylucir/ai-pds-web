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


def reject_unsafe_segment(value: str) -> None:
    """Raise ValueError unless `value` is exactly ONE ordinary path segment.

    Strictly stronger than `reject_unsafe`, and the difference is load-bearing
    wherever a single URL path parameter becomes one directory name. `""` and
    `"."` both PASS reject_unsafe -- `PurePosixPath` reduces them to no parts
    at all -- yet `root / pid / slug` then collapses onto `root / pid`, so a
    caller that deletes "the slug's directory" deletes every SIBLING slug
    instead. `".."` climbs one further and takes every project with it.
    Multi-segment values ("a/b") are rejected for the same reason: one
    parameter must not silently address a nested path.

    Ordinary names with a ".." substring inside them ("..foo") are fine --
    same boundary reject_unsafe draws, since they are one real segment.
    """
    reject_unsafe(value)
    if PurePosixPath(value).parts != (value,):
        raise ValueError(f"unsafe path segment: {value!r}")
