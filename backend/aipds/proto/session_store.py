# backend/pathfinder/proto/session_store.py — SDK SessionStore adapter over S3.
#
# This is what makes prototype build context outlive a session: the SDK mirrors
# every transcript line here, and on resume it loads them back and materializes
# a temp JSONL for the subprocess. Without it the ClaudeSDKClient dies with the
# session and a follow-up "change that button" starts from zero.
#
# Only append/load/list_subkeys are implemented. The Protocol's other methods
# raise NotImplementedError by default, and our call path never reaches them:
# we always pass an explicit `resume` (never continue_conversation), which is
# what would otherwise force list_sessions(); and deletion is handled by the
# project-delete path wiping the whole S3 prefix, so a WORM-style no-op here is
# correct.
#
# Batch ordering: each append() writes ONE object whose key sorts after every
# earlier one, so load() can restore order by sorting keys. A monotonic
# counter (not a timestamp) does that -- timestamps would collide at the
# SDK's ~100ms batch cadence.
#
# That counter is seeded from S3, not hardcoded to start at 0: a resume always
# builds a brand-new S3SessionStore (PrototypeBuilder constructs one per
# session), so an instance-wide counter starting at 0 would reuse the first
# instance's key names on the very first append and silently overwrite its
# early batches. Seeding from the highest NNNNNNNN already on S3 for that
# session makes append() safe across separate instances of this class talking
# to the same session, not just within one instance's lifetime. It is tracked
# per session-prefix (a dict), not as one instance-wide integer, because a
# single instance also appends under the main transcript and any number of
# subagent subpaths -- each of those needs its own seeded starting point.
from __future__ import annotations

import json

from aipds.s3store import S3StoreLike


def transcript_prefix(slug: str) -> str:
    return f"prototypes/{slug}/transcript/"


def _session_prefix(slug: str, key: dict) -> str:
    base = f"{transcript_prefix(slug)}{key['session_id']}/"
    subpath = key.get("subpath")
    # `subpath` is opaque to adapters -- use it as a storage key suffix only.
    # "main/" keeps the main transcript from sharing a prefix with a subagent
    # whose subpath could otherwise start with the same characters.
    return f"{base}sub/{subpath}/" if subpath else f"{base}main/"


class S3SessionStore:
    def __init__(self, s3: S3StoreLike, slug: str):
        self._s3 = s3
        self._slug = slug
        self._seq: dict[str, int] = {}  # session-prefix -> highest seq written

    async def _next_seq(self, prefix: str) -> int:
        if prefix not in self._seq:
            # First append() under this prefix on THIS instance -- seed from
            # whatever's already in S3 (0 if nothing) instead of assuming
            # we're the first writer ever.
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
        prefix = _session_prefix(self._slug, key)
        seq = await self._next_seq(prefix)
        blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
        await self._s3.put(f"{prefix}{seq:08d}.jsonl", blob)

    async def load(self, key: dict) -> list[dict] | None:
        prefix = _session_prefix(self._slug, key)
        keys = await self._s3.list(prefix)
        if not keys:
            return None  # never written (or emptied -- the SDK treats both the same)
        entries: list[dict] = []
        for k in sorted(keys):
            body = await self._s3.get(k)
            entries.extend(json.loads(line) for line in body.splitlines() if line)
        return entries

    async def list_subkeys(self, key: dict) -> list[str]:
        base = f"{transcript_prefix(self._slug)}{key['session_id']}/sub/"
        found: list[str] = []
        for k in await self._s3.list(base):
            subpath = k[len(base):].rsplit("/", 1)[0]
            if subpath and subpath not in found:
                found.append(subpath)
        return sorted(found)
