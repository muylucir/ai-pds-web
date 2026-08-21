# backend/aipds/workspace_sync.py -- the rules for publishing a workspace file to the record
# of truth (S3).
#
# **Why this module exists (2026-08-18).** The screen said "the document has been written" while
# it was absent from the document tab's dropdown; it appeared in the list but selecting it
# showed no content; it appeared briefly and vanished; and it was visible in the "document
# review" screen. Four symptoms, one cause:
#
#     `file_changed` announces immediately on a **local** write, while publication to the record
#     of truth was deferred to the **end of the turn**. Every read path in the UI is S3
#     (runner.read_file / list_files).
#
# The measurement showed it plainly: the S3 timestamps of one project's 16 `aiplc-docs` files
# were all within the same second -- nothing during the turn, everything uploaded in a burst at
# the end. So in a turn running longer than 90 seconds the user saw only the words "written" and
# could not see the document.
#
# The frontend was already straining against this (WorkspaceDocPanel unions the current path
# into the list and distinguishes a 404 as "not yet synced"). But when there is nothing in the
# record of truth to read, there is nothing the frontend can do -- hence the four symptoms
# above.
#
# The contract is the one already applied to question files (a2b9623's "put the file in S3
# before advertising the card"): **publish before advertising.**
#
# **Why this module owns the rules.** There are now two places that upload -- the end-of-turn
# batch sync (`runner._sync_workspace_to_s3`) and the publish immediately after a write (the
# PostToolUse hook in `claude_driver`). If the `audit.md` redaction and the target globs were
# copied into both, only one would get fixed and a path would appear by which **audit.md is
# published to the record of truth without redaction**. That class of divergence raises no
# error.
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from aipds.globmatch import matches_glob
from aipds.parsers.redaction import redact_credentials
from aipds.pathsafe import reject_unsafe
from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.workspace")

#: The subtrees published to the record of truth. `AgentRunner._SYNC_GLOBS` uses this value --
#: with two copies, the immediate publish and the end-of-turn batch would upload different sets,
#: and that divergence looks like "a document that was there and then was not".
SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")

#: The keys redacted on store. `audit.md` carries tool output verbatim and is exposed even to
#: someone reading S3 directly, so credentials are stripped on upload. Artifact documents go up
#: unchanged.
_REDACTED_KEYS = frozenset({"aiplc-docs/audit.md"})


def is_synced_key(key: str) -> bool:
    """Whether this key belongs to the set published to the record of truth."""
    return any(matches_glob(key, glob) for glob in SYNC_GLOBS)


def content_for_s3(key: str, text: str) -> str:
    """The content to publish. Only `audit.md` is redacted."""
    return redact_credentials(text) if key in _REDACTED_KEYS else text


async def publish_file(
    s3: S3StoreLike,
    local_root: Path,
    key: str,
    on_published: Callable[[str, str, str | None], None] | None = None,
) -> bool:
    """Publish one workspace file to the record of truth. True if it was published.

    **It does not raise.** This function is called from the hook immediately after a write --
    publishing is incidental, and a file already deleted or a momentary S3 failure must not kill
    the turn. The end-of-turn batch sync is still the backstop (runner's done/error paths and
    its `finally`).

    An unsafe key is refused (fail-closed). The batch sync stops the whole sync on meeting one,
    while here that one file is refused -- refusing is right rather than stopping the turn from
    inside a hook.
    """
    try:
        reject_unsafe(key)
    except Exception:
        _log.warning("refusing to publish an unsafe key: %r", key)
        return False
    if not is_synced_key(key):
        return False
    path = local_root / key
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Already deleted, or a directory -- the batch sync uploads the final state.
        return False
    canonical = content_for_s3(key, text)
    try:
        etag = await s3.put(key, canonical)
    except Exception:
        _log.exception("publishing %s to S3 failed — the turn-end sync will "
                       "retry", key)
        return False
    if on_published is not None:
        on_published(key, canonical, etag)
    return True
