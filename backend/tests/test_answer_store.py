# backend/tests/test_answer_store.py
import json

import pytest

from aipds.agent.answer_store import load_answers, save_answers
from tests.fakes.in_memory_s3 import FakeS3Store

_QFILE = {"name": "discovery-questions", "questions": [{"number": 1}]}


async def test_round_trips_keyed_by_tool_use_id():
    """tool_use_id is the key — the transcript's tool_result carries the same id, so the
    restore join is exact without guessing by order or timestamp."""
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
    json.dumps([1, 2, 3]),                                  # not a dict
    json.dumps({"tool_use_id": "", "questions": {}, "answers": {}}),
    json.dumps({"tool_use_id": "x", "questions": "문자열", "answers": {}}),
    json.dumps({"tool_use_id": "x", "questions": {}, "answers": {"1": 5}}),
])
async def test_one_corrupt_record_does_not_hide_the_others(body):
    """History is auxiliary data — one corrupted entry must not block restoring the remaining rounds
    (same principle as list_history's downgrade). The answers value types are checked too because
    the frontend contract is Record<string, string>, and if numbers were mixed in, answerSummary
    would receive values it cannot interpret as letters."""
    s3 = FakeS3Store()
    await save_answers(s3, tool_use_id="good", interrupt_id="iid",
                       questions=_QFILE, answers={"1": "A"})
    s3.blobs["answers/bad.json"] = body

    got = await load_answers(s3)

    assert set(got) == {"good"}


async def test_missing_prefix_is_not_an_error():
    """Sessions without records (before this feature) give an empty dict — callers fall back
    to the CLI sentence."""
    assert await load_answers(FakeS3Store()) == {}


async def test_listing_failure_degrades_to_empty():
    class _Boom(FakeS3Store):
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    assert await load_answers(_Boom()) == {}
