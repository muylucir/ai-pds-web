import json
import pytest
from events import AgentEvent
from aiplc_tools import build_tools, QUESTIONS_SCHEMA_HINT


class FakeToolContext:
    """Duck-typed ToolContext: interrupt() returns a canned response (resume
    semantics) or raises to emulate the first-pass suspension."""
    def __init__(self, response=None, raise_first=False):
        self._response = response
        self._raise = raise_first
        self.calls = []

    def interrupt(self, name, reason=None):
        self.calls.append((name, reason))
        if self._raise:
            raise RuntimeError("suspended")  # stands in for InterruptException
        return self._response


def _tool_by_name(tools, name):
    return next(t for t in tools if getattr(t, "tool_name", getattr(t, "__name__", "")) == name)


def test_report_stage_emits_stage_event_and_acks(tmp_path):
    emitted: list[AgentEvent] = []
    tools = build_tools(str(tmp_path), emitted.append)
    report_stage = _tool_by_name(tools, "report_stage")
    out = report_stage(stage="Envision", status="in_progress", summary="PR/FAQ 작성 중")
    assert emitted[0].kind == "stage"
    assert json.loads(emitted[0].payload) == {
        "stage": "Envision", "status": "in_progress", "summary": "PR/FAQ 작성 중"}
    assert "Envision" in out

def test_report_stage_rejects_invalid_status_without_emitting(tmp_path):
    emitted: list[AgentEvent] = []
    tools = build_tools(str(tmp_path), emitted.append)
    report_stage = _tool_by_name(tools, "report_stage")
    out = report_stage(stage="Envision", status="bogus", summary="x")
    assert emitted == []  # no stage event emitted for an invalid status
    assert "invalid status" in out
    assert "bogus" in out

def test_questions_schema_hint_includes_parse_ok_and_raw_markdown():
    assert '"parse_ok": true' in QUESTIONS_SCHEMA_HINT
    assert '"raw_markdown": null' in QUESTIONS_SCHEMA_HINT

def test_schema_hint_mentions_multi_select():
    assert "multi_select" in QUESTIONS_SCHEMA_HINT
    assert "false" in QUESTIONS_SCHEMA_HINT  # 기본값 안내

def test_submit_document_emits_document_event(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    submit_document = _tool_by_name(tools, "submit_document")
    submit_document(path="aiplc-docs/discovery/discovery-document.md",
                    version="v2", summary="솔루션 분석 반영")
    assert emitted[0].kind == "document"
    assert json.loads(emitted[0].payload)["version"] == "v2"

def test_ask_questions_interrupts_with_payload_and_returns_answers(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    ask = _tool_by_name(tools, "ask_questions")
    payload = {"name": "pain-point-questions", "preamble": None, "questions": [
        {"number": 1, "category": None, "text": "주요 고객은?", "answer": None,
         "options": [{"letter": "A", "text": "사내 PM", "is_other": False, "recommended": True}]}]}
    ctx = FakeToolContext(response={"1": "A"})
    result = ask(questions_file=payload, tool_context=ctx)
    name, reason = ctx.calls[0]
    assert name == "ask_questions"
    assert reason["questions_payload"] == payload
    assert "1" in result  # answers are returned to the model as the tool result

def test_file_write_confined_and_emits_file_changed(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    fw = _tool_by_name(tools, "file_write")
    fw(path="aiplc-docs/audit.md", content="# audit")
    assert (tmp_path / "aiplc-docs" / "audit.md").read_text() == "# audit"
    assert emitted[0].kind == "file_changed" and emitted[0].path == "aiplc-docs/audit.md"
    with pytest.raises(ValueError):
        fw(path="../etc/passwd", content="x")

def test_file_read_confined(tmp_path):
    (tmp_path / "aiplc-rules").mkdir()
    (tmp_path / "aiplc-rules" / "r.md").write_text("rule")
    tools = build_tools(str(tmp_path), lambda e: None)
    fr = _tool_by_name(tools, "file_read")
    assert fr(path="aiplc-rules/r.md") == "rule"
    with pytest.raises(ValueError):
        fr(path="/etc/passwd")
