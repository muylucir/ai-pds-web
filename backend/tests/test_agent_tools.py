from pathlib import Path
import pytest
from pathfinder.models import AgentEvent
from pathfinder.agent.tools import build_tools, QUESTIONS_SCHEMA_HINT


def _tool_by_name(tools, name):
    # strands @tool 객체는 .tool_name을 노출하고, 객체 자체가 호출 가능하다
    # (도구 본체 직접 호출) — 검증된 harness/tests/test_aiplc_tools.py 패턴.
    return next(t for t in tools if getattr(t, "tool_name", getattr(t, "__name__", "")) == name)


def _tools(workspace, rules_dir):
    emitted = []
    tools = build_tools(str(workspace), str(rules_dir), emitted.append)
    return {name: _tool_by_name(tools, name)
            for name in ("ask_questions", "report_stage", "submit_document",
                         "file_read", "file_write", "file_append")}, emitted


def test_file_read_routes_aiplc_rules_prefix_to_rules_dir(tmp_path):
    # rules_dir IS the aiplc-rules root (mirrors the real on-disk layout at
    # <repo>/rule/aiplc-rules, which directly contains aws-aiplc-rules/ and
    # aws-aiplc-rule-details/) -- not a parent directory containing an
    # "aiplc-rules/" subdir. The agent always calls file_read with the
    # "aiplc-rules/"-prefixed path per core-workflow.md; that prefix must be
    # stripped before joining onto rules_dir, or the lookup double-nests.
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text("RULE BODY", encoding="utf-8")
    tools, _ = _tools(ws, rules)
    out = tools["file_read"](path="aiplc-rules/aws-aiplc-rules/core-workflow.md")
    assert "RULE BODY" in out


def test_file_read_reaches_real_rules_layout():
    # Integration-style pin against the REAL on-disk rules directory so a
    # regression in the aiplc-rules/ prefix-stripping can't hide behind a
    # fixture that encodes the wrong layout (as the unit test above once did).
    import pathlib
    repo_rules = pathlib.Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        pytest.skip("real aiplc-rules not present")
    tools, _ = _tools(repo_rules / "does-not-matter-ws", repo_rules)
    # exactly the prefixed path core-workflow tells the agent to read for a rule detail:
    out = tools["file_read"](path="aiplc-rules/aws-aiplc-rules/core-workflow.md")
    assert len(out) > 0


def test_file_read_routes_non_rules_path_to_workspace(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; rules.mkdir()
    (ws / "aiplc-docs").mkdir()
    (ws / "aiplc-docs" / "audit.md").write_text("WS BODY", encoding="utf-8")
    tools, _ = _tools(ws, rules)
    out = tools["file_read"](path="aiplc-docs/audit.md")
    assert "WS BODY" in out


def test_file_write_confined_to_workspace_emits_file_changed(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tools, emitted = _tools(ws, tmp_path / "rules")
    tools["file_write"](path="aiplc-docs/x.md", content="hi")
    assert (ws / "aiplc-docs" / "x.md").read_text(encoding="utf-8") == "hi"
    assert any(e.kind == "file_changed" and e.path == "aiplc-docs/x.md" for e in emitted)


def test_file_read_rejects_escape_from_rules_dir(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; rules.mkdir()
    tools, _ = _tools(ws, rules)
    with pytest.raises(ValueError):
        tools["file_read"](path="aiplc-rules/../../etc/passwd")


def test_report_stage_rejects_invalid_status(tmp_path):
    tools, _ = _tools(tmp_path / "ws", tmp_path / "rules")
    out = tools["report_stage"](stage="Envision", status="bogus")
    assert "invalid status" in out


def test_schema_hint_mentions_parse_ok_and_multi_select():
    assert "parse_ok" in QUESTIONS_SCHEMA_HINT
    assert "multi_select" in QUESTIONS_SCHEMA_HINT


def test_schema_hint_mentions_letter_note_answer_form():
    # 스펙(option-annotation): 일반 보기 답변은 "B" 또는 "B: 부연설명" 형태로
    # 돌아온다 — 에이전트가 부연을 놓치지 않도록 힌트에 명시되어야 한다.
    assert "부연" in QUESTIONS_SCHEMA_HINT
    assert "'B: " in QUESTIONS_SCHEMA_HINT or '"B: ' in QUESTIONS_SCHEMA_HINT


def test_report_stage_writes_state_file(tmp_path):
    from pathfinder.parsers.state import parse_state_file
    ws = tmp_path / "ws"; ws.mkdir()
    tools, _ = _tools(ws, tmp_path / "rules")
    tools["report_stage"](stage="Envision", status="in_progress", summary="시작")
    state_file = ws / "aiplc-docs" / "aiplc-state.md"
    assert state_file.is_file()
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.current_stage == "Envision"
    tools["report_stage"](stage="Envision", status="completed", summary="끝")
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.stages[0].status == "completed"


def test_report_stage_survives_state_write_failure(tmp_path, monkeypatch):
    # fail-soft: 상태 파일 upsert가 터져도 이벤트/반환은 정상.
    ws = tmp_path / "ws"; ws.mkdir()
    emitted = []
    from pathfinder.agent import tools as tools_mod
    monkeypatch.setattr(tools_mod, "upsert_stage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tools = {t.tool_name: t for t in tools_mod.build_tools(str(ws), str(tmp_path / "rules"), emitted.append)}
    out = tools["report_stage"](stage="Envision", status="in_progress")
    assert "stage recorded" in out
    assert emitted and emitted[0].kind == "stage"


# ---- submit_document must not declare a document that isn't on disk ----

def _ws_and_tools(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; rules.mkdir()
    tools, emitted = _tools(ws, rules)
    return ws, tools, emitted


def test_submit_document_emits_when_the_file_exists(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "discovery" / "discovery-document.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# 내용", encoding="utf-8")

    result = tools["submit_document"]("aiplc-docs/discovery/discovery-document.md", "v1", "요약")

    assert "submitted" in result
    docs = [e for e in emitted if e.kind == "document"]
    assert len(docs) == 1


def test_submit_document_refuses_a_path_that_was_never_written(tmp_path):
    """The decoupling that made a real bug invisible: this tool only emitted an
    event, so an agent that called it without a preceding file_write produced a
    chat message saying the document was created, a dropdown entry for it, and
    no document. The event is the UI's source of truth for "a document is
    ready", so it must not fire for a file that does not exist."""
    ws, tools, emitted = _ws_and_tools(tmp_path)

    result = tools["submit_document"]("aiplc-docs/discovery/discovery-document.md", "v1")

    assert "document" not in [e.kind for e in emitted]
    assert "file_write" in result  # tells the agent what to do instead


def test_submit_document_refuses_an_empty_file(tmp_path):
    """A zero-byte or whitespace-only file is the same failure wearing a
    different hat -- the panel would render "문서 내용이 아직 비어 있습니다"
    while the chat claimed success."""
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "empty.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("   \n", encoding="utf-8")

    result = tools["submit_document"]("aiplc-docs/empty.md", "v1")

    assert "document" not in [e.kind for e in emitted]
    assert "비어" in result or "empty" in result.lower()


def test_submit_document_rejects_a_path_escaping_the_workspace(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")

    result = tools["submit_document"]("../secret.md", "v1")

    assert "document" not in [e.kind for e in emitted]
    assert "escape" in result.lower() or "경로" in result
