# backend/aipds/workspace_sync.py — rules for publishing workspace files to the source of truth (S3).
#
# **Why this module exists (2026-08-18).** The screen said "the document has been
# written", yet it was missing from the Documents tab dropdown; when it did appear in the
# list and was selected, it had no content; it appeared briefly and then vanished; and it
# was visible on the "document review" screen. All four symptoms had one cause:
#
#     `file_changed` announces a **local** write immediately, but publishing to the
#     source of truth was deferred until **turn end**. Every UI read path goes to S3
#     (runner.read_file / list_files).
#
# The measurements showed it plainly: the S3 timestamps of all 16 files in one project's
# `aiplc-docs` fell within the same single second — nothing during the turn, everything
# uploaded in a batch at the end. So in a turn running longer than 90 seconds, the user
# saw only the claim "written" and could not see the document.
#
# The frontend was already working hard to prevent this (WorkspaceDocPanel unions the
# current path into the list and distinguishes a 404 as "not yet synced"). But if what it
# needs to read is not in the source of truth, there is nothing the frontend can do — hence
# the four remaining symptoms above.
#
# The contract is the same as the one already applied to question files ("upload the file
# to S3 before advertising the card" in a2b9623): **publish before advertising.**
#
# **Why this module owns the rules.** There are now two upload paths — the turn-end batch
# sync (`runner._sync_workspace_to_s3`) and publish-right-after-write (`claude_driver`'s
# PostToolUse hook). If the `audit.md` redaction and the target globs were copied into both
# places, fixing only one would open a path where **audit.md reaches the source of truth
# unredacted**. That kind of divergence raises no error.
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from aipds.globmatch import matches_glob
from aipds.parsers.redaction import redact_credentials
from aipds.pathsafe import read_text_beneath, reject_unsafe
from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.workspace")

#: Subtrees published to the source of truth. `AgentRunner._SYNC_GLOBS` uses this value —
#: with two copies, publish-right-after-write and the turn-end batch would upload different
#: sets, and that mismatch shows up as "documents that exist and then disappear".
#:
#: `uploads/` is not included. The upload route writes directly to the source of truth, and
#: it is input the agent only reads. The publish path is text (UTF-8), so including it would
#: overwrite original images and PDFs every turn with copies corrupted into U+FFFD.
SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*")

#: Keys redacted on save. `audit.md` contains raw tool output and is also exposed to anyone
#: reading S3 directly, so credentials are scrubbed on upload. Artifact documents are uploaded as is.
_REDACTED_KEYS = frozenset({"aiplc-docs/audit.md"})


def is_synced_key(key: str) -> bool:
    """Whether this key belongs to the set published to the source of truth."""
    return any(matches_glob(key, glob) for glob in SYNC_GLOBS)


def content_for_s3(key: str, text: str) -> str:
    """Content to publish to the source of truth. Only `audit.md` is redacted."""
    return redact_credentials(text) if key in _REDACTED_KEYS else text


async def publish_file(
    s3: S3StoreLike,
    local_root: Path,
    key: str,
    on_published: Callable[[str, str, str | None], None] | None = None,
) -> bool:
    """Publish a single workspace file to the source of truth. True if published.

    **Never raises.** This function is called from the post-write hook — publishing is a
    side effect, and the turn must not die because the file was already deleted or S3
    briefly failed. The turn-end batch sync remains the backstop (the runner's done/error
    paths and `finally`).

    Unsafe keys are rejected (fail-closed). The batch sync halts the entire sync when it
    meets such a key; here we reject that one file — rejecting is more appropriate than
    halting the turn from a hook.
    """
    try:
        reject_unsafe(key)
    except Exception:
        _log.warning("refusing to publish an unsafe key: %r", key)
        return False
    if not is_synced_key(key):
        return False
    try:
        text = read_text_beneath(local_root, key, errors="replace")
    except OSError:
        # Already deleted, a directory, or a link — the batch sync uploads the final state
        # (links are skipped there too).
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
