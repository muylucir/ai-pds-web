# Strands persists pending interrupts together with the session, but the Claude Agent SDK's
# session store is a transcript mirror, so a pending question is an in-memory Future.
# GET /pending (restoring the question form after a refresh) uses that capability, so we persist it separately.
#
# Why sdk_questions (the raw SDK form) is saved too: it is needed to translate answers back
# to SDK labels (builder._answer_to_sdk), and after a restart there is no in-memory copy.
import json

import pytest

from aipds.agent.pending_store import (
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
    # Answer submission and an interrupt can overlap and call this twice — if the second
    # call blows up, the turn dies.
    await clear_pending(FakeS3Store())


@pytest.mark.asyncio
async def test_save_replaces_an_earlier_pending():
    # A project has only one pending question (fixed key). If the earlier one remained,
    # a refresh would show an old form that can no longer be answered.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="old", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    await save_pending(s3, interrupt_id="new", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    got = await load_pending(s3)
    assert got is not None and got["interrupt_id"] == "new"


@pytest.mark.asyncio
async def test_load_degrades_to_none_on_corrupt_json():
    # Don't return 500 for a corrupt payload — pending is a restore convenience, and without it
    # the user can start the turn again.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = "{not json"
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_a_required_field_is_missing():
    # Half-restoring a payload whose contract has drifted makes answer submission fail silently.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = '{"interrupt_id": "i-1"}'
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_hangul_survives_the_round_trip():
    # Must be saved with ensure_ascii=False so \\uXXXX doesn't show on screen.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    assert "누구?" in s3.blobs[PENDING_KEY]


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_sdk_questions_has_the_wrong_type():
    # If sdk_questions is not a list, Task 6's answer back-translation (builder._answer_to_sdk)
    # blows up while indexing — passing it just because the field "exists" would die here.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "i-1", "questions": QUESTIONS,
        "sdk_questions": "not-a-list", "session_id": "s-1",
    })
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_questions_has_the_wrong_type():
    # If questions is not a dict, GET /pending dies while rendering the form —
    # an existence check alone can't catch this drift.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "i-1", "questions": ["not", "a", "dict"],
        "sdk_questions": SDK_QUESTIONS, "session_id": "s-1",
    })
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_interrupt_id_is_empty():
    # An empty interrupt_id makes it impossible to know which interrupt to resume on
    # answer submission — it passes the "field exists" check but is effectively the same
    # as having no value.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = json.dumps({
        "interrupt_id": "", "questions": QUESTIONS,
        "sdk_questions": SDK_QUESTIONS, "session_id": "s-1",
    })
    assert await load_pending(s3) is None
