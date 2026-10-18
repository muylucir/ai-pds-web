# backend/tests/test_routes_user_input_record.py — the routes that open a turn pass along the user input.
#
# User input in the audit log is recorded by the web app (aipds/audit_log). Only the route knows what the user actually
# said — question-form answers and document approvals are covered by their own test files; this file covers chat and SDK
# question answers.
from __future__ import annotations

from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

client = TestClient(app)


def _seed(monkeypatch, pid, language="ko"):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")

    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid, "language": language})
    return registry.get(pid).runner


def test_a_chat_message_is_recorded_verbatim_in_the_project_language(monkeypatch):
    runner = _seed(monkeypatch, "ui-chat", language="en")
    text = "Let's start AI-PLC.\n\n## Our company\nWe run 320 stores."
    assert client.post("/projects/ui-chat/turns", json={"text": text}).status_code == 200
    record = runner.records[-1]
    assert (record.text, record.source, record.language) == (text, "chat", "en")


def test_sdk_question_answers_are_recorded_as_question_and_answer(monkeypatch):
    runner = _seed(monkeypatch, "ui-sdk")
    r = client.post("/projects/ui-sdk/answers", json={"answers": {"누구?": "PM"}})
    assert r.status_code == 200
    record = runner.records[-1]
    assert (record.text, record.source) == ("누구? → PM", "answers")
