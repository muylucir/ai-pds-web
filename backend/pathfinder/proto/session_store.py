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
# in-instance counter (not a timestamp) does that -- timestamps would collide
# at the SDK's ~100ms batch cadence.
from __future__ import annotations

import json

from pathfinder.s3store import S3StoreLike


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
        self._seq = 0

    async def append(self, key: dict, entries: list[dict]) -> None:
        if not entries:
            return
        self._seq += 1
        blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
        await self._s3.put(
            f"{_session_prefix(self._slug, key)}{self._seq:08d}.jsonl", blob)

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
