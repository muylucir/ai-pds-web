# backend/tests/test_audit_log.py — the web records user input verbatim in audit.md.
#
# aipds/audit_log writes and parsers/audit reads. Pin both sides together: what was written must come back as
# one "input → response" line on screen.
from __future__ import annotations

from datetime import datetime, timezone

from aipds import audit_log
from aipds.audit_log import UserInput
from aipds.parsers.audit import parse_audit_file

NOW = datetime(2026, 10, 8, 1, 2, 3, tzinfo=timezone.utc)


def test_an_entry_carries_the_real_time_and_the_input_verbatim():
    md = audit_log.entry(UserInput(text="Path A로 시작할게요", source="chat"), NOW)
    assert "## 사용자 입력 (AI-PDS Web 기록) — 채팅" in md
    assert "**Timestamp**: 2026-10-08T01:02:03Z" in md
    assert "> Path A로 시작할게요" in md


def test_the_heading_follows_the_project_language_and_names_the_question_file():
    md = audit_log.entry(UserInput(text="Q1 → A", source="answers", language="en",
                                   detail="business-context-questions.md"), NOW)
    assert "## User Input (recorded by AI-PDS Web) — question answers · business-context-questions.md" in md


def test_pasted_markdown_cannot_break_the_log_structure():
    """Headings, fences, or labels the user pasted at the start of a line would break entry boundaries."""
    pasted = "## 우리 회사\n```\ncode\n```\n**Context**: 가짜 라벨\n\n끝"
    md = audit_log.entry(UserInput(text=pasted, source="chat"), NOW)
    body = md.split("**User Input**:\n", 1)[1]
    assert all(line.startswith(">") for line in body.strip().splitlines())


def test_credentials_never_reach_the_log():
    """Upstream rule: credentials are never recorded in audit.md. Don't rely on redaction during the source-of-truth upload."""
    md = audit_log.entry(UserInput(text="키는 AKIAIOSFODNN7EXAMPLE 입니다", source="chat"), NOW)
    assert "AKIAIOSFODNN7EXAMPLE" not in md


def test_append_creates_the_file_on_the_first_turn_and_appends_after(tmp_path):
    key = audit_log.append(tmp_path, UserInput(text="첫 요청", source="chat"), NOW)
    path = tmp_path / key
    assert key == "aiplc-docs/audit.md"
    assert path.read_text(encoding="utf-8").startswith("# AI-PLC Audit Log\n")
    audit_log.append(tmp_path, UserInput(text="두 번째", source="chat"), NOW)
    text = path.read_text(encoding="utf-8")
    assert text.index("> 첫 요청") < text.index("> 두 번째")


# ---- parser: the web's input entry + the agent's response entry = one interaction ----

AGENT_ENTRY = """
## Envision — Business Context
**Timestamp**: 2026-10-08T01:02:40Z
**AI Response**: 비즈니스 맥락을 정리하고 후속 질문을 만들었다.
**Context**: Envision Step 0
"""


def _log(*parts: str) -> str:
    return "# AI-PLC Audit Log\n" + "".join(parts)


def test_a_web_input_and_the_following_agent_entry_read_as_one_interaction():
    pasted = "## 우리 회사\n치킨 프랜차이즈 320개\n```\n표\n```"
    md = _log(audit_log.entry(UserInput(text=pasted, source="chat"), NOW), AGENT_ENTRY)
    [entry] = parse_audit_file(md)
    assert entry.user_input == pasted
    assert entry.ai_response == "비즈니스 맥락을 정리하고 후속 질문을 만들었다."
    assert entry.context == "Envision Step 0"
    assert entry.timestamp == "2026-10-08T01:02:03Z"     # the web's real time


def test_the_web_input_wins_when_the_agent_copies_the_input_again():
    """Even if the agent habitually writes the input again, the original is the web's — the agent's may be a summary."""
    agent = AGENT_ENTRY.replace("**AI Response**", "**User Input**: 요약된 입력\n**AI Response**")
    md = _log(audit_log.entry(UserInput(text="원문 그대로의 긴 입력", source="chat"), NOW), agent)
    [entry] = parse_audit_file(md)
    assert entry.user_input == "원문 그대로의 긴 입력"


def test_an_interrupted_turn_leaves_the_input_on_its_own():
    md = _log(AGENT_ENTRY, audit_log.entry(UserInput(text="중단된 요청", source="chat"), NOW))
    entries = parse_audit_file(md)
    assert [e.user_input for e in entries] == ["", "중단된 요청"]
    assert entries[-1].ai_response == ""


def test_old_entries_and_web_entries_coexist_and_are_numbered_in_order():
    """In an existing project's audit.md, new entries follow the old format (input and response in one entry)."""
    old = """
## Workspace Detection
**Timestamp**: 2026-09-29T04:45:13Z
**User Input**: "AI-PLC를 시작해줘"
**AI Response**: 새 워크스페이스
**Context**: Session start
"""
    md = _log(old, audit_log.entry(UserInput(text="새 답변", source="chat"), NOW), AGENT_ENTRY)
    entries = parse_audit_file(md)
    assert [(e.index, e.user_input) for e in entries] == [(1, "AI-PLC를 시작해줘"), (2, "새 답변")]
