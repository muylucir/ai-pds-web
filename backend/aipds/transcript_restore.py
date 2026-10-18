# backend/aipds/transcript_restore.py — restores the transcript to resume into the per-project
# config dir.
#
# **Why not use the SDK's restore.** With `session_store` + `resume`, the SDK unpacks the
# store's transcript into the backend's `/tmp/claude-resume-*` and **switches** the CLI's
# `CLAUDE_CONFIG_DIR` **to that temporary directory** (`_internal/session_resume.py`). Only
# `.credentials.json`, `.claude.json`, and `settings.json` are copied there, so the resumed
# CLI has neither CLAUDE.md nor skills — measured (2026-09-23): asking the resumed CLI for its
# skill list and the codeword in CLAUDE.md gave "Skills: None, CLAUDE.md is no longer
# present". Discovery is a resident process, so it took that path only after a reconnect, but
# prototype modification builds took it every time. It is worse under the execution wrapper
# (aipds/launcher.py): a CLI with a different uid and PrivateTmp cannot see that temporary
# directory at all.
#
# So the roles are split:
# - Local (`<config>/projects/…`) is the CLI's working copy. The CLI writes it directly and
#   resumes from it directly.
# - S3 is the mirror. The SDK is given a `MirrorOnlyStore` whose `load` is empty so the SDK
#   does not unpack into a temporary directory, and only writes (append) flow through as is.
# - Only when there is no local copy (right after instance replacement, first use of the agent
#   home, after the CLI's cleanupPeriodDays cleanup) does `restore` fetch it from S3 and put it
#   locally.
#
# `restore` borrows the SDK's **private** `materialize_resume_session` — if we reimplemented
# the store format (sub-agent transcripts, metadata sidecars, line order), it would drift from
# the SDK. If the SDK moves that function, it logs a warning and falls back to a new session
# (= the behavior after instance replacement until now). tests/test_transcript_restore.py pins
# that contract with the real SDK.
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aipds.pathsafe import stat_beneath, write_beneath

_log = logging.getLogger("aipds.agent")


class MirrorOnlyStore:
    """Writes as is, reads pretend to be empty. Keeps the SDK from creating a temporary config
    dir on resume.

    When `load` is empty, the SDK treats it as "not in the store" and passes `--resume <id>`
    to the CLI as is — the CLI resumes from the local copy. Apart from `append`, nothing is
    called by the SDK on a live session (`list_sessions` only in continue_conversation, which
    this app does not use).
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def append(self, key: Any, entries: list) -> None:
        await self.inner.append(key, entries)

    async def load(self, key: Any) -> None:
        return None


def _materializer():
    try:
        from claude_agent_sdk._internal.session_resume import materialize_resume_session
    except ImportError:
        return None
    return materialize_resume_session


async def restore(store: Any, config_dir: str | Path, cwd: str,
                  session_id: str) -> bool:
    """Write the `session_id` transcript from `store` under `config_dir/projects/`.

    True if restored. False if it is not in the store (new project) or there is no means to
    restore — the caller starts a new session. Existing local files are not overwritten (local
    may be newer than the mirror: mirror writes go out batched at turn boundaries).
    """
    materialize = _materializer()
    if materialize is None:
        _log.warning("SDK has no materialize_resume_session — cannot restore "
                     "transcript %s; starting a fresh session", session_id)
        return False
    from claude_agent_sdk import ClaudeAgentOptions
    options = ClaudeAgentOptions(cwd=cwd, resume=session_id, session_store=store,
                                 env={"CLAUDE_CONFIG_DIR": str(config_dir)})
    materialized = await materialize(options)
    if materialized is None:
        return False
    try:
        src = Path(materialized.config_dir) / "projects"
        dst = Path(config_dir) / "projects"
        await asyncio.to_thread(_merge_tree, src, dst)
    finally:
        await materialized.cleanup()
    _log.info("restored transcript %s from the mirror into %s", session_id, dst)
    return True


def _merge_tree(src: Path, dst: Path) -> None:
    """Write only the files under `src` that are missing in `dst`.

    `dst` is under the config dir and is a tree the agent writes to — links are not followed
    (`pathsafe`). Following them would write transcripts, as the backend uid, wherever the
    agent chose.
    """
    root, base = dst.parent, dst.name
    root.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = f"{base}/{path.relative_to(src).as_posix()}"
        if stat_beneath(root, rel) is None:
            # The temporary copy is 0600 (SDK). Writing it fresh makes it follow the umask —
            # the group must be able to read it for the CLI under the execution wrapper to
            # open it.
            write_beneath(root, rel, path.read_bytes())
