# backend/tests/test_s3_parallelism.py — S3 왕복을 순차로 도는 회귀를 막는다.
#
# **왜 이 파일이 있는가(2026-08-17 실측).** 배포 인스턴스에서 잰 값:
#
#     S3 왕복 1회        0.030초
#     순차 GET 32개      0.984초
#     병렬 GET 32개      0.114초   ← 8.6배
#
# 이 격차가 그대로 사용자 지연이 된다. 트랜스크립트는 세션 길이에, 워크스페이스
# 복원은 산출물 수에, 카드 목록은 카드 수에 각각 **선형**이다. 그리고 순차로
# 되돌리는 변경은 **기능 테스트를 전부 통과한다** — 결과가 같고 느려질 뿐이다.
# 그래서 동시성 자체를 단정한다.
#
# 이 리포에는 이미 선례가 있다: project_store.load_manifest가 `asyncio.gather`를
# 쓰고, 삭제된 strands 히스토리 경로에도 "순차(개수 × S3 왕복 ≈ 80개면 ~5초)로
# 읽으면 /history가 화면 로딩을 수 초씩 막는다"는 주석과 함께 gather가 있었다.
# 정작 살아 있는 경로에는 그 최적화가 없었다.
from __future__ import annotations

import asyncio
import json

import pytest

from fakes.in_memory_s3 import FakeS3Store


class ConcurrencyProbe(FakeS3Store):
    """동시에 떠 있는 `get` 수의 최고치를 기록한다.

    순차 구현이면 최고치가 1을 넘지 않는다 — 그것이 이 파일의 단정이다.
    `sleep(0)`으로 이벤트 루프에 한 번 양보해, 병렬 호출이 실제로 겹칠 기회를
    만든다(양보가 없으면 gather라도 동기적으로 하나씩 끝나 최고치가 1이 된다).
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
    """가장 큰 단일 손실이었다 — 실측 0.877초(32배치). 세션 길이에 선형이다."""
    from aipds.agent.claude_driver import _sdk_session_id
    from aipds.agent.session_store import load_transcript

    s3 = ConcurrencyProbe()
    resolved, _ = _sdk_session_id({"session_id": "p1"})
    _seed_transcript(s3, resolved, 12)

    entries = await load_transcript(s3, "p1")

    assert len(entries) == 12, "병렬화가 내용을 잃으면 안 된다"
    assert s3.peak > 1, f"순차로 읽고 있다(peak={s3.peak})"


async def test_load_transcript_keeps_batch_order():
    """`gather`는 입력 순서대로 돌려주므로 키 정렬이 곧 대화 순서다. 이것이
    깨지면 채팅이 뒤섞여 복원된다 — 조용한 오작동이다."""
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
    """`return_exceptions=True`의 근거. 손상된 객체 하나가 대화 전체를 빈 목록으로
    만드는 것이 더 나쁘다(list_history의 강등과 같은 원칙)."""
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
    """라운드 수에 선형이다."""
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
    """**매 턴 시작**에 도는 경로다 — 순차 왕복이 그대로 답변 지연이 된다.
    실측 0.247초(산출물 6개), 산출물 수에 선형이므로 50개면 ~3초다."""
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
    """복원 실패는 삼키지 않는다 — 워크스페이스가 불완전한 채로 턴이 돌면
    에이전트가 없는 파일을 못 찾고, 그건 조용한 오작동이다."""
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
    """동시에 떠 있는 `list` 수의 최고치. 카드 목록은 슬러그마다 `list`를 돈다."""

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
    """카드 N개에 2N번 순차 왕복이었다(bundle 조회 + 설문 응답 수). 실측 왕복
    30ms이므로 카드 10개면 0.6초가 목록 조회에 그대로 붙었다."""
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
