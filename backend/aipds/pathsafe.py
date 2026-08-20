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


def workspace_relative(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; return None on escape.

    The two functions above *raise* because their callers are our own routes,
    where an unsafe path is a bug. This one *returns None* because its callers
    are tool/hook callbacks reacting to a path the **model** chose — an escape
    there is ordinary model behavior to be reported, not an exception to
    propagate through the SDK's callback machinery.

    `relative_to` also raises ValueError when `path` is absolute but shares no
    prefix with `workspace` at all (e.g. "/etc/passwd" vs workspace
    "/workspace") -- not just for genuinely relative inputs. A naive fallback
    (`path.lstrip("/")`) would treat both cases as "already relative", letting
    an unrelated absolute path escape undetected. Only fall back to the lstrip
    path when `path` was not absolute to begin with; an absolute path that
    isn't under the workspace is always an escape.

    Lives here rather than in either caller because it had already been copied
    twice (VM-era claude_driver -> proto/builder -> agent/claude_driver), and
    agent/discovery_guard needed a third. Three copies of an escape check is
    three chances for one of them to drift into a hole.
    """
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        if path.startswith("/"):
            return None
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


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
