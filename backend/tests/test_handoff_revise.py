# backend/tests/test_handoff_revise.py — changing a settled answer from the handoff tab (routes/handoff.revise_answer)
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store
from test_handoff_readiness import D, QUESTIONS

client = TestClient(app)
STRATEGY = D + "product-strategy/strategy-questions.md"


def _seed(monkeypatch, pid):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()
    monkeypatch.setattr(app_module, "s3_store_factory", lambda _pid: s3)

    async def make(_pid):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    asyncio.run(ws.runner.write_file(STRATEGY, QUESTIONS))
    asyncio.run(ws.runner.write_file(D + "discovery-document.md", "# 문서\n"))
    return ws


def _revise(pid, **body):
    return client.post(f"/projects/{pid}/handoff/revise", json={"file": STRATEGY, "number": 1, **body})


def test_the_web_writes_the_new_answer_and_hands_the_change_to_the_agent(monkeypatch):
    """Observed (ws-o, ws-g): asked in chat to change an answer, one agent kept "the web fills [Answer]" and left the old one —
    the ledger then contradicted the document it had rewritten — and the other broke the rule. The web writes it now."""
    ws = _seed(monkeypatch, "rev-1")
    before = client.get("/projects/rev-1/handoff/supplement").json()
    item = next(c for c in before["confirmations"] if c["number"] == 1)
    # The tab gets the options, so it can ask the question again.
    assert [o["letter"] for o in item["options"]] == ["A", "B", "X"] and item["multi_select"] is False

    r = _revise("rev-1", answer="B")
    assert r.status_code == 200 and r.json()["turn_id"]
    assert "[Answer]: B" in asyncio.run(ws.runner.read_file(STRATEGY))
    # The turn names the file, says the tag is already written, and points at the upstream change procedure.
    text = ws.runner.sent[-1]
    assert STRATEGY in text and "다시 쓰지 마세요" in text and "common/workflow-changes.md" in text
    assert "이전 답:" in text and "Subscription" in text and "One-time purchase" in text
    # The audit log keeps what the PM changed, not the instructions.
    record = ws.runner.records[-1]
    assert (record.source, record.detail) == ("revision", "strategy-questions.md")
    assert record.text in text and "workflow-changes" not in record.text
    # The workspace attaches to this turn after the tab moves there: the turn carries the bubble it draws first.
    assert client.get("/projects/rev-1/turn").json()["turn"]["input"] == record.text
    # The decision is the PM's now: it leaves the AI-suggestion confirmations.
    after = client.get("/projects/rev-1/handoff/supplement").json()
    assert 1 not in [c["number"] for c in after["confirmations"]]


def test_only_a_different_answer_to_an_answered_discovery_question_is_accepted(monkeypatch):
    _seed(monkeypatch, "rev-2")
    assert _revise("rev-2", answer="A").status_code == 400                    # unchanged
    assert _revise("rev-2", answer="  ").status_code == 400                   # empty
    assert client.post("/projects/rev-2/handoff/revise",
                       json={"file": "aiplc-docs/audit.md", "number": 1, "answer": "B"}).status_code == 400
    assert client.post("/projects/rev-2/handoff/revise",
                       json={"file": D + "envision/none-questions.md", "number": 1,
                             "answer": "B"}).status_code == 404
    # Question 6 of the fixture has no answer: an open round is the question form's to answer, not this endpoint's.
    assert _revise("rev-2", number=6, answer="A").status_code == 409
    assert registry.get("rev-2").runner.sent == []
