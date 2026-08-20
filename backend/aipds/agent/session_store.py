# backend/aipds/agent/session_store.py — SDK SessionStore adapter over S3
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

import asyncio
import json
import logging

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.agent")


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
        bodies = await asyncio.gather(
            *(self._s3.get(k) for k in sorted(keys)))
        entries: list[dict] = []
        for body in bodies:
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

    `session_id` is accepted in the caller's terms -- a PROJECT ID -- and
    translated here, because the two sides of the mirror do not agree on it by
    default. The writer never sees the project id: the CLI rejects a non-UUID
    `--session-id` outright, so the driver derives a stable uuid5 from it
    (`_sdk_session_id`) and mirrors under THAT. A reader that takes the project
    id literally therefore lists a prefix nothing was ever written to and
    reports an empty history -- with no error, because `list_history` degrades
    every failure to `[]`. Measured: transcript present in S3, chat timeline
    blank. Same shape as the prefix mismatch this module's header describes,
    one layer further in.

    Translating rather than making the caller do it: the key layout is owned
    here, and the caller (`session_history.list_history`) already receives a
    project id from the route. A value that is already a UUID passes through,
    so a caller that hands over a real session id is not overridden.
    """
    from aipds.agent.claude_driver import _sdk_session_id

    resolved, _ = _sdk_session_id({"session_id": session_id})
    prefix = _session_prefix({"session_id": resolved})
    keys = sorted(await s3.list(prefix))
    # **병렬 GET.** 순차로 읽으면 배치 수 × S3 왕복이 그대로 화면 로딩을 막는다 —
    # 실측(2026-08-17, 배포 인스턴스): 왕복 1회 30ms, 32배치 순차 0.98초 vs 병렬
    # 0.11초(8.6배). 세션 길이에 선형이므로 워크숍 하나가 200배치면 순차는 6초다.
    # `gather`는 입력 순서대로 결과를 돌려주므로 위의 키 정렬이 그대로 순서를
    # 보장한다(project_store.load_manifest가 같은 패턴을 쓴다).
    #
    # 한 배치의 실패가 나머지를 못 삼키게 return_exceptions를 쓴다. 트랜스크립트는
    # 히스토리 복원용 보조 데이터이고, 한 객체가 손상됐을 때 대화 전체가 빈 목록이
    # 되는 것이 더 나쁘다(list_history의 강등과 같은 원칙).
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    entries: list[dict] = []
    for k, body in zip(keys, bodies):
        if isinstance(body, BaseException):
            _log.warning("unreadable transcript batch skipped: %s", k)
            continue
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
