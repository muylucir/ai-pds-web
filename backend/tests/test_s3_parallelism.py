# backend/tests/test_s3_parallelism.py — guards against regressions that run S3 round trips sequentially.
#
# **Why this file exists (observed 2026-08-17).** Values measured on the deployed instance:
#
#     1 S3 round trip        0.030 s
#     32 sequential GETs     0.984 s
#     32 parallel GETs       0.114 s   ← 8.6x
#
# That gap becomes user latency as is. The transcript is **linear** in session length, workspace
# restore in the number of artifacts, and the card list in the number of cards. And a change that reverts
# to sequential **passes every functional test** — the results are the same, it is just slower.
# So the concurrency itself is asserted.
#
# This repo already has precedent: project_store.load_manifest uses `asyncio.gather`,
# and the deleted strands history path also had gather, with the comment "reading sequentially (count × S3 round trip ≈ ~5 s for 80)
# blocks screen loading on /history for seconds at a time".
# Yet the live paths lacked that optimization.
from __future__ import annotations

import asyncio
import json

import pytest

from fakes.in_memory_s3 import FakeS3Store


class ConcurrencyProbe(FakeS3Store):
    """Records the peak number of `get` calls in flight at once.

    With a sequential implementation the peak never exceeds 1 — that is this file's assertion.
    `sleep(0)` yields to the event loop once, giving parallel calls a real chance to overlap
    (without the yield, even with gather each finishes synchronously one at a time and the peak is 1).
    """

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0

    async def get(self, key: str) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0)
            return await super().get(key)
        finally:
            self.in_flight -= 1


def _seed_transcript(s3: FakeS3Store, session_uuid: str, batches: int) -> None:
    for n in range(1, batches + 1):
        key = (f"discovery/transcript/{session_uuid}/main/{n:08d}.jsonl")
        s3.blobs[key] = json.dumps(
            {"type": "user", "message": {"role": "user", "content": f"turn {n}"}})


async def test_load_transcript_reads_batches_in_parallel():
    """This was the largest single loss — observed 0.877 s (32 batches). Linear in session length."""
    from aipds.agent.claude_driver import _sdk_session_id
    from aipds.agent.session_store import load_transcript

    s3 = ConcurrencyProbe()
    resolved, _ = _sdk_session_id({"session_id": "p1"})
    _seed_transcript(s3, resolved, 12)

    entries = await load_transcript(s3, "p1")

    assert len(entries) == 12, "병렬화가 내용을 잃으면 안 된다"
    assert s3.peak > 1, f"순차로 읽고 있다(peak={s3.peak})"


async def test_load_transcript_keeps_batch_order():
    """`gather` returns in input order, so key order is conversation order. If this
    breaks, the chat is restored scrambled — a silent malfunction."""
    from aipds.agent.claude_driver import _sdk_session_id
    from aipds.agent.session_store import load_transcript

    s3 = ConcurrencyProbe()
    resolved, _ = _sdk_session_id({"session_id": "p1"})
    _seed_transcript(s3, resolved, 12)

    entries = await load_transcript(s3, "p1")

    assert [e["message"]["content"] for e in entries] == \
        [f"turn {n}" for n in range(1, 13)]


async def test_sdk_session_store_load_reads_batches_in_parallel():
    from aipds.agent.session_store import DiscoverySessionStore

    s3 = ConcurrencyProbe()
    for n in range(1, 9):
        s3.blobs[f"discovery/transcript/session/main/{n:08d}.jsonl"] = (
            json.dumps({"n": n}))

    entries = await DiscoverySessionStore(s3).load(
        {"session_id": "session"})

    assert [entry["n"] for entry in entries] == list(range(1, 9))
    assert s3.peak > 1, f"순차로 읽고 있다(peak={s3.peak})"


async def test_one_unreadable_batch_does_not_lose_the_rest():
    """The rationale for `return_exceptions=True`. One corrupted object turning the whole conversation into an empty list
    is worse (the same principle as list_history's degradation)."""
    from aipds.agent.claude_driver import _sdk_session_id
    from aipds.agent.session_store import load_transcript

    class OneBoom(ConcurrencyProbe):
        async def get(self, key: str) -> str:
            if key.endswith("00000002.jsonl"):
                raise RuntimeError("s3 hiccup")
            return await super().get(key)

    s3 = OneBoom()
    resolved, _ = _sdk_session_id({"session_id": "p1"})
    _seed_transcript(s3, resolved, 5)

    entries = await load_transcript(s3, "p1")

    assert len(entries) == 4, [e["message"]["content"] for e in entries]


async def test_load_answers_reads_records_in_parallel():
    """Linear in the number of rounds."""
    from aipds.agent.answer_store import load_answers

    s3 = ConcurrencyProbe()
    for n in range(10):
        s3.blobs[f"answers/toolu_{n}.json"] = json.dumps({
            "tool_use_id": f"toolu_{n}", "interrupt_id": "i",
            "questions": {"questions": []}, "answers": {"1": "A"}})

    got = await load_answers(s3)

    assert len(got) == 10
    assert s3.peak > 1, f"순차로 읽고 있다(peak={s3.peak})"


async def test_load_answers_skips_a_broken_record_without_losing_the_rest():
    from aipds.agent.answer_store import load_answers

    class OneBoom(ConcurrencyProbe):
        async def get(self, key: str) -> str:
            if key.endswith("toolu_3.json"):
                raise RuntimeError("s3 hiccup")
            return await super().get(key)

    s3 = OneBoom()
    for n in range(5):
        s3.blobs[f"answers/toolu_{n}.json"] = json.dumps({
            "tool_use_id": f"toolu_{n}", "interrupt_id": "i",
            "questions": {"questions": []}, "answers": {"1": "A"}})

    got = await load_answers(s3)

    assert len(got) == 4 and "toolu_3" not in got


async def test_workspace_restore_reads_files_in_parallel(tmp_path):
    """A path that runs **at the start of every turn** — sequential round trips become reply latency as is.
    Observed 0.247 s (6 artifacts); linear in the number of artifacts, so ~3 s for 50."""
    from aipds.runner import AgentRunner

    s3 = ConcurrencyProbe()
    for n in range(10):
        s3.blobs[f"aiplc-docs/doc-{n}.md"] = f"# {n}"
    runner = AgentRunner(project_id="p1", driver=None, s3=s3,
                         local_root=str(tmp_path), session={})

    await runner._restore_workspace_from_s3()

    assert (tmp_path / "aiplc-docs" / "doc-9.md").read_text(encoding="utf-8") == "# 9"
    assert s3.peak > 1, f"순차로 읽고 있다(peak={s3.peak})"


async def test_workspace_restore_still_fails_the_turn_on_error(tmp_path):
    """A restore failure is not swallowed — if a turn runs with an incomplete workspace, the agent
    cannot find files that are missing, and that is a silent malfunction."""
    from aipds.runner import AgentRunner

    class Boom(ConcurrencyProbe):
        async def get(self, key: str) -> str:
            raise RuntimeError("s3 down")

    s3 = Boom()
    s3.blobs["aiplc-docs/a.md"] = "x"
    runner = AgentRunner(project_id="p1", driver=None, s3=s3,
                         local_root=str(tmp_path), session={})

    with pytest.raises(RuntimeError):
        await runner._restore_workspace_from_s3()


async def test_workspace_sync_uses_bounded_parallel_uploads(tmp_path):
    from aipds.runner import AgentRunner

    class PutProbe(FakeS3Store):
        def __init__(self):
            super().__init__()
            self.in_flight = 0
            self.peak = 0

        async def put(self, key, content):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                await asyncio.sleep(0)
                return await super().put(key, content)
            finally:
                self.in_flight -= 1

    s3 = PutProbe()
    for n in range(20):
        path = tmp_path / "aiplc-docs" / f"doc-{n}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {n}")
    runner = AgentRunner(project_id="p1", driver=None, s3=s3,
                         local_root=str(tmp_path), session={})

    await runner._sync_workspace_to_s3()

    assert 1 < s3.peak <= 8


class ListConcurrencyProbe(FakeS3Store):
    """Peak number of `list` calls in flight at once. The card list runs a `list` per slug."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0

    async def list(self, prefix: str) -> list[str]:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0)
            return await super().list(prefix)
        finally:
            self.in_flight -= 1


async def test_prototype_list_does_not_round_trip_per_card(monkeypatch, tmp_path):
    """N cards meant 2N sequential round trips (bundle lookup + survey response count). With an observed round trip
    of 30 ms, 10 cards added 0.6 s straight onto the list query."""
    import aipds.app as app_module
    from aipds.routes.prototypes import list_prototypes

    s3 = ListConcurrencyProbe()
    for n in range(6):
        slug = f"proto-{n}"
        s3.blobs[f"aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md"] = "# s"
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    monkeypatch.setattr(app_module.registry, "is_registered", lambda pid: True)

    body = await list_prototypes("p1")

    assert len(body["prototypes"]) == 6
    assert s3.peak > 1, f"카드마다 순차로 왕복하고 있다(peak={s3.peak})"
