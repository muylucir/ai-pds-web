# backend/tests/test_answer_store.py
import json

import pytest

from pathfinder.agent.answer_store import load_answers, save_answers
from tests.fakes.in_memory_s3 import FakeS3Store

_QFILE = {"name": "discovery-questions", "questions": [{"number": 1}]}


async def test_round_trips_keyed_by_tool_use_id():
    """tool_use_id가 키다 — 트랜스크립트의 tool_result가 같은 id를 들고 있어
    복원 조인이 순서·타임스탬프 추측 없이 정확해진다."""
    s3 = FakeS3Store()
    await save_answers(s3, tool_use_id="toolu_1", interrupt_id="iid-1",
                       questions=_QFILE, answers={"1": "A", "2": "B,C"})

    got = await load_answers(s3)

    assert set(got) == {"toolu_1"}
    assert got["toolu_1"]["answers"] == {"1": "A", "2": "B,C"}
    assert got["toolu_1"]["questions"] == _QFILE


async def test_resubmission_overwrites_the_same_round():
    s3 = FakeS3Store()
    await save_answers(s3, tool_use_id="toolu_1", interrupt_id="iid-1",
                       questions=_QFILE, answers={"1": "A"})
    await save_answers(s3, tool_use_id="toolu_1", interrupt_id="iid-1",
                       questions=_QFILE, answers={"1": "B"})

    got = await load_answers(s3)

    assert len(got) == 1 and got["toolu_1"]["answers"] == {"1": "B"}


@pytest.mark.parametrize("body", [
    "not json at all",
    json.dumps([1, 2, 3]),                                  # dict가 아님
    json.dumps({"tool_use_id": "", "questions": {}, "answers": {}}),
    json.dumps({"tool_use_id": "x", "questions": "문자열", "answers": {}}),
    json.dumps({"tool_use_id": "x", "questions": {}, "answers": {"1": 5}}),
])
async def test_one_corrupt_record_does_not_hide_the_others(body):
    """히스토리는 보조 데이터다 — 손상된 한 건이 나머지 라운드의 복원을 막으면
    안 된다(list_history의 강등과 같은 원칙). answers 값 타입까지 보는 이유는
    프론트 계약이 Record<string, string>이고, 숫자가 섞이면 answerSummary가
    letter로 해석할 수 없는 값을 받게 되기 때문이다."""
    s3 = FakeS3Store()
    await save_answers(s3, tool_use_id="good", interrupt_id="iid",
                       questions=_QFILE, answers={"1": "A"})
    s3.blobs["answers/bad.json"] = body

    got = await load_answers(s3)

    assert set(got) == {"good"}


async def test_missing_prefix_is_not_an_error():
    """레코드가 없는 세션(이 기능 이전)은 빈 dict — 호출부가 CLI 문장 폴백으로
    떨어진다."""
    assert await load_answers(FakeS3Store()) == {}


async def test_listing_failure_degrades_to_empty():
    class _Boom(FakeS3Store):
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    assert await load_answers(_Boom()) == {}
