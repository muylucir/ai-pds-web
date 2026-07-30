# backend/pathfinder/agent/session_store.py — SDK SessionStore adapter over S3
# for DISCOVERY (ClaudeDriver).
#
# Why this exists: the CLI keeps its transcript on LOCAL DISK
# (`<config_dir>/projects/<encoded cwd>/<session_id>.jsonl` --
# claude_driver._transcript_path). That is enough to resume within one box's
# lifetime, and nothing more: an EC2 replacement or redeploy takes the whole
# Discovery conversation with it, and there is no second copy anywhere.
#
# It is also why chat history broke when Discovery moved from StrandsDriver to
# ClaudeDriver. `session_history.list_history` reads
# `session_{pid}/agents/agent_default/messages/`, which ONLY strands'
# S3SessionManager ever wrote (agent/driver.py). Under the claude driver that
# prefix is empty forever, and `list_history` degrades every failure to `[]` --
# so the timeline came back blank with no error anywhere. Mirroring the
# transcript here gives history a durable source that is also the SDK's own
# documented contract, rather than a parser aimed at the CLI's private on-disk
# layout.
#
# This is proto/session_store.py's adapter with one difference: the key prefix.
# Kept as a separate module rather than parameterizing that one, because the two
# prefixes are owned by different delete paths -- `prototypes/{slug}/` is wiped
# by the prototype reset route (proto/session.py's purge_session_state), while
# this lives under the project's own prefix and goes away with the project. A
# shared class would invite a future edit that moves one of them and silently
# breaks the other's cleanup.
#
# Everything else about the mechanism is carried across verbatim, including the
# reason the sequence counter is SEEDED FROM S3 rather than starting at 0: a
# resume constructs a brand-new store instance, so an instance-local counter
# would reuse `00000001.jsonl` on its first append and overwrite the first
# session's opening batches. Read proto/session_store.py's header for the full
# argument -- it is the same one, and it was learned there.
from __future__ import annotations

import json

from pathfinder.s3store import S3StoreLike


def transcript_prefix() -> str:
    """Discovery's transcript root, RELATIVE to the per-project store.

    `session_s3_factory` is already scoped to `sessions/`, and history is read
    through that same factory, so both sides see the same keys. Deliberately
    NOT under `session_{pid}/agents/...`: that shape belongs to strands'
    S3SessionManager and reusing it would make two different formats share one
    prefix, which is exactly the confusion that produced this bug.
    """
    return "discovery/transcript/"


def _session_prefix(key: dict) -> str:
    base = f"{transcript_prefix()}{key['session_id']}/"
    subpath = key.get("subpath")
    # `subpath` is opaque to adapters -- a storage key suffix only. "main/"
    # keeps the main transcript from sharing a prefix with a subagent whose
    # subpath could otherwise start with the same characters.
    return f"{base}sub/{subpath}/" if subpath else f"{base}main/"


class DiscoverySessionStore:
    """SDK SessionStore over S3, scoped to one project's session store.

    `s3` must already be namespaced per project (app.session_s3_factory gives a
    `sessions/` store, and the driver passes the project's own sub-store), so
    this class never spells the project id itself.
    """

    def __init__(self, s3: S3StoreLike):
        self._s3 = s3
        self._seq: dict[str, int] = {}  # session-prefix -> highest seq written

    async def _next_seq(self, prefix: str) -> int:
        if prefix not in self._seq:
            # First append() under this prefix on THIS instance -- seed from
            # whatever is already in S3 (0 if nothing) rather than assuming we
            # are the first writer ever.
            self._seq[prefix] = await self._max_existing_seq(prefix)
        self._seq[prefix] += 1
        return self._seq[prefix]

    async def _max_existing_seq(self, prefix: str) -> int:
        highest = 0
        for k in await self._s3.list(prefix):
            stem = k[len(prefix):].split(".", 1)[0]
            try:
                n = int(stem)
            except ValueError:
                continue  # not one of our NNNNNNNN.jsonl objects -- ignore
            highest = max(highest, n)
        return highest

    async def append(self, key: dict, entries: list[dict]) -> None:
        if not entries:
            return
        prefix = _session_prefix(key)
        seq = await self._next_seq(prefix)
        blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
        await self._s3.put(f"{prefix}{seq:08d}.jsonl", blob)

    async def load(self, key: dict) -> list[dict] | None:
        prefix = _session_prefix(key)
        keys = await self._s3.list(prefix)
        if not keys:
            return None  # never written (or emptied -- the SDK treats both alike)
        entries: list[dict] = []
        for k in sorted(keys):
            body = await self._s3.get(k)
            entries.extend(json.loads(line) for line in body.splitlines() if line)
        return entries

    async def list_subkeys(self, key: dict) -> list[str]:
        base = f"{transcript_prefix()}{key['session_id']}/sub/"
        found: list[str] = []
        for k in await self._s3.list(base):
            subpath = k[len(base):].rsplit("/", 1)[0]
            if subpath and subpath not in found:
                found.append(subpath)
        return sorted(found)


async def load_transcript(s3: S3StoreLike, session_id: str) -> list[dict]:
    """Every mirrored transcript line for one session, in write order.

    The read side of `append`, used by session_history. Ordering comes from the
    zero-padded sequence in the key, so a plain `sorted()` is chronological.
    Failure is the caller's to degrade -- this raises.
    """
    prefix = _session_prefix({"session_id": session_id})
    entries: list[dict] = []
    for k in sorted(await s3.list(prefix)):
        body = await s3.get(k)
        for line in body.splitlines():
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # One malformed line must not cost the whole transcript. The
                # SDK's only stated invariant is json round-tripping, so a
                # failure here means the object was truncated mid-write.
                continue
    return entries
