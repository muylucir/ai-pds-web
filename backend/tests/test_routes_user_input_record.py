# backend/tests/test_routes_user_input_record.py — 턴을 여는 라우트가 사용자 입력을 넘긴다.
#
# 감사 로그의 사용자 입력은 웹이 남긴다(aipds/audit_log). 무엇이 사용자의 말인지는 라우트만
# 안다 — 질문 폼 답변·문서 승인은 각자의 테스트 파일이 덮고, 여기서는 채팅과 SDK 질문 답변을
# 덮는다.
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
