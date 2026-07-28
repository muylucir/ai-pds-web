# Strands는 세션에 pending interrupt를 함께 영속하지만 Claude Agent SDK의
# session store는 트랜스크립트 미러여서 pending 질문이 인메모리 Future다.
# GET /pending(새로고침 후 질문 폼 복원)이 그 기능을 쓰므로 별도로 영속한다.
#
# sdk_questions(SDK 원형)를 함께 저장하는 이유: 답변을 SDK 라벨로 되번역할 때
# 필요하고(builder._answer_to_sdk), 재시작 후에는 인메모리 사본이 없다.
import json

import pytest

from pathfinder.agent.pending_store import (
    PENDING_KEY, clear_pending, load_pending, save_pending,
)
from tests.fakes.in_memory_s3 import FakeS3Store

QUESTIONS = {"name": "envision", "preamble": None, "parse_ok": True,
             "raw_markdown": None,
             "questions": [{"number": 1, "category": None, "text": "누구?",
                            "answer": None, "multi_select": False,
                            "options": [{"letter": "A", "text": "PM",
                                         "is_other": False,
                                         "recommended": False}]}]}
SDK_QUESTIONS = [{"question": "누구?", "header": "Audience",
                  "multiSelect": False,
                  "options": [{"label": "PM", "description": "제품 관리자"}]}]


@pytest.mark.asyncio
async def test_round_trips_every_field():
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    got = await load_pending(s3)
    assert got == {"interrupt_id": "i-1", "questions": QUESTIONS,
                   "sdk_questions": SDK_QUESTIONS, "session_id": "s-1"}


@pytest.mark.asyncio
async def test_load_returns_none_when_nothing_is_pending():
    assert await load_pending(FakeS3Store()) is None


@pytest.mark.asyncio
async def test_clear_removes_it():
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    await clear_pending(s3)
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_clear_is_idempotent():
    # 답변 제출과 인터럽트가 겹쳐 두 번 호출될 수 있다 — 두 번째가 터지면
    # 턴이 죽는다.
    await clear_pending(FakeS3Store())


@pytest.mark.asyncio
async def test_save_replaces_an_earlier_pending():
    # 한 프로젝트에 pending 질문은 하나뿐이다(고정 키). 이전 것이 남으면
    # 새로고침 시 답변 불가한 옛 폼이 뜬다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="old", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    await save_pending(s3, interrupt_id="new", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    got = await load_pending(s3)
    assert got is not None and got["interrupt_id"] == "new"


@pytest.mark.asyncio
async def test_load_degrades_to_none_on_corrupt_json():
    # 손상된 payload로 500을 내지 않는다 — pending은 복원 편의이고, 없으면
    # 사용자가 턴을 다시 시작할 수 있다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = "{not json"
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_a_required_field_is_missing():
    # 계약이 드리프트한 payload를 반쯤 복원하면 답변 제출이 조용히 실패한다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = '{"interrupt_id": "i-1"}'
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_hangul_survives_the_round_trip():
    # ensure_ascii=False로 저장해야 화면에 \\uXXXX가 뜨지 않는다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    assert "누구?" in s3.blobs[PENDING_KEY]


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_sdk_questions_has_the_wrong_type():
    # sdk_questions가 리스트가 아니면 Task 6의 답변 되번역(builder._answer_to_sdk)이
    # 인덱싱 도중 터진다 — 필드가 "있기만" 하면 통과시키면 여기서 죽는다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "i-1", "questions": QUESTIONS,
        "sdk_questions": "not-a-list", "session_id": "s-1",
    })
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_questions_has_the_wrong_type():
    # questions가 dict가 아니면 GET /pending이 폼을 렌더링하다가 죽는다 —
    # 존재 검사만으론 이 드리프트를 못 잡는다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "i-1", "questions": ["not", "a", "dict"],
        "sdk_questions": SDK_QUESTIONS, "session_id": "s-1",
    })
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_interrupt_id_is_empty():
    # 빈 interrupt_id는 답변 제출 시 어떤 인터럽트를 재개할지 알 수 없게
    # 만든다 — "필드가 있다"는 검사를 통과하지만 실질적으로 값이 없는 것과
    # 같다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "", "questions": QUESTIONS,
        "sdk_questions": SDK_QUESTIONS, "session_id": "s-1",
    })
    assert await load_pending(s3) is None
