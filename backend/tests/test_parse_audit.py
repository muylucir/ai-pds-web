# backend/tests/test_parse_audit.py
import re
from pathlib import Path
from aipds.parsers.audit import parse_audit_file

FIX = Path(__file__).parent / "fixtures"

def test_parses_pilot1_audit_entries():
    entries = parse_audit_file((FIX / "audit.md").read_text(encoding="utf-8"))
    assert entries[0].index == 1
    assert entries[0].user_input == "ai-plc를 시작하고 싶어"
    assert entries[0].context == "Session start"
    # entries are in order and cover the full pilot run
    assert [e.index for e in entries] == list(range(1, len(entries) + 1))

def test_redacts_credentials_in_entries():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: my key is AKIAIOSFODNN7EXAMPLE ok\n"
        "**AI Response**: noted\n"
        "**Context**: test\n"
    )
    e = parse_audit_file(md)[0]
    assert "AKIA" not in e.user_input
    assert "[CREDENTIAL REDACTED]" in e.user_input

def test_redacts_credentials_in_ai_response():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: hello\n"
        "**AI Response**: token is AKIAIOSFODNN7EXAMPLE right\n"
        "**Context**: test\n"
    )
    e = parse_audit_file(md)[0]
    assert "AKIA" not in e.ai_response
    assert "[CREDENTIAL REDACTED]" in e.ai_response

def test_redacts_credentials_in_context():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: hi\n"
        "**AI Response**: ok\n"
        "**Context**: leaked sk-abc123def456ghi789 here\n"
    )
    e = parse_audit_file(md)[0]
    assert "[CREDENTIAL REDACTED]" in e.context

def test_no_unredacted_credentials_anywhere_in_real_fixture():
    entries = parse_audit_file((FIX / "audit.md").read_text(encoding="utf-8"))
    # AWS_BEARER_TOKEN is checked as an assignment (KEY=value), matching what
    # redact_credentials actually targets. A bare mention of the env var name
    # in prose (e.g. "AWS_BEARER_TOKEN_BEDROCK env var present") is not a
    # leaked secret and is expected to remain, by design (Task 2).
    for e in entries:
        for field in (e.user_input, e.ai_response, e.context or ""):
            for marker in ("AKIA", "sk-", "bedrock-api-key-", "goog_"):
                assert marker not in field, f"unredacted {marker} in entry {e.index}"
            assert not re.search(r"AWS_BEARER_TOKEN[A-Z_]*=\S+", field), (
                f"unredacted AWS_BEARER_TOKEN assignment in entry {e.index}"
            )

def test_squashed_single_line_entry_splits_fields():
    md = (
        "## Entry 1: Squashed\n"
        '**Timestamp**: 2026-07-04T00:00:00Z **User Input**: "big blob with \\n escapes and more" **AI Response**: the real answer **Context**: Some Context Label\n'
    )
    e = parse_audit_file(md)[0]
    assert e.ai_response == "the real answer"           # not glued to context
    assert e.context == "Some Context Label"
    assert "big blob" in e.user_input

def test_parses_rule_format_semantic_headings():
    """The rules tell the agent to log entries under a SEMANTIC `## <name>`
    heading (see core-workflow.md 'Audit Logging' and the per-stage examples),
    never `## Entry N:`. Parsing only the latter made every real audit.md
    return zero entries — the review page's 'AI 검증 요약'/'승인 게이트 이력'
    panels rendered empty and the approval button looked dead."""
    md = """# AI-PLC Discovery Audit Log

## Session Start
**Timestamp**: 2025-01-15T00:00:00Z
**User Input**: "NOTAM 대시보드를 고도화 하고싶어"
**AI Response**: "Workspace 신규로 판단, Discovery 시작"
**Context**: Workspace Detection

## 최종 승인
**Timestamp**: 2025-01-16T02:00:00Z
**User Input**: "승인"
**AI Response**: "승인 완료 — Discovery 단계를 종료합니다."
**Context**: Approval gate
"""
    entries = parse_audit_file(md)
    assert len(entries) == 2
    assert entries[0].timestamp == "2025-01-15T00:00:00Z"
    assert entries[0].user_input == "NOTAM 대시보드를 고도화 하고싶어"
    assert entries[1].user_input == "승인"
    assert "승인 완료" in entries[1].ai_response
    assert entries[1].context == "Approval gate"
    # Indices must be stable and ordered so the UI can sort newest-first.
    assert [e.index for e in entries] == [1, 2]


def test_ignores_sections_without_audit_markers():
    """A heading with no Timestamp/User Input/AI Response markers is prose
    (e.g. a findings section), not an audit entry — counting it would put
    empty rows in the review panels."""
    md = """# Audit

## Session Start
**Timestamp**: 2025-01-15T00:00:00Z
**User Input**: "hello"
**AI Response**: "hi"

## Directory Structure
Some explanatory prose with no markers at all.

## 해석
- 페인포인트 정리 메모
"""
    entries = parse_audit_file(md)
    assert len(entries) == 1
    assert entries[0].user_input == "hello"


def test_still_parses_legacy_entry_n_headings():
    """Pilot logs that used `## Entry N:` must keep working, and their
    explicit numbers must be preserved."""
    md = """## Entry 7: something
**Timestamp**: 2025-01-15T00:00:00Z
**User Input**: "legacy"
**AI Response**: "ok"
"""
    entries = parse_audit_file(md)
    assert len(entries) == 1
    assert entries[0].index == 7
    assert entries[0].user_input == "legacy"


def test_parses_the_real_project_audit_log():
    """Regression pin against a real agent-written log shape: the heading text
    is arbitrary Korean, entries are separated by `---`, and some sections
    carry extra sub-headings."""
    md = """# AI-PLC Discovery Audit Log

## Session Start
**Timestamp**: 2025-01-15T00:00:00Z
**User Input**: "NOTAM 대시보드를 고도화 하고싶어"
**AI Response**: "신규 Discovery 시작"
**Context**: Workspace Detection

### Workspace Detection Findings
- 기존 aiplc-state.md: 없음

---
## Envision - 페인포인트 질문 응답
**Timestamp**: 2025-01-15T00:10:00Z
**User Input**: "Q1: A) 운항관리사/디스패처"
**AI Response**: "페인포인트 확인"
**Context**: Envision

---
## 검증 분석 결과 승인
**Timestamp**: 2025-01-16T05:00:00Z
**User Input**: "승인"
**AI Response**: "승인 완료 — 검증 분석 결과가 확정되었습니다."
**Context**: Approval gate
"""
    entries = parse_audit_file(md)
    assert len(entries) == 3
    assert entries[-1].user_input == "승인"
    # The findings sub-heading must not leak into the first entry's AI response.
    assert "Workspace Detection Findings" not in entries[0].ai_response


def test_parses_subheading_and_fenced_input_shape():
    """The other shape real agent logs use: a `### 사용자 입력` sub-heading with
    a fenced raw input, and the AI side narrated as the following prose. Before
    this was supported these entries parsed with empty user_input/ai_response,
    so the review panels showed blank rows for real approvals."""
    md = """# Audit

## 검증 분석 결과 승인
**Timestamp**: 2025-01-15T02:30:00Z
### 사용자 입력
```
승인
```
- validation-results.md 분석 및 판단 최종 승인.
- 확정: Build Decision = Iterate.

---
"""
    entries = parse_audit_file(md)
    assert len(entries) == 1
    e = entries[0]
    assert e.user_input == "승인"
    assert "최종 승인" in e.ai_response
    # The heading becomes the context so the gate filter can match it.
    assert e.context == "검증 분석 결과 승인"
    # Fences and sub-headings must not leak into the response text.
    assert "```" not in e.ai_response and "###" not in e.ai_response


def test_subheading_shape_feeds_the_gate_history_filter():
    """End-to-end intent: an approval logged in the sub-heading shape must be
    matchable by the review page's gate filter (context/ai_response contains
    승인/gate/approval), otherwise '승인 게이트 이력' stays empty after the
    user clicks 승인하고 다음 단계로."""
    md = """## 최종 승인
**Timestamp**: 2025-01-16T00:00:00Z
### 사용자 입력
```
승인
```
- Discovery 단계 완료 처리.
"""
    entries = parse_audit_file(md)
    assert len(entries) == 1
    haystack = f"{entries[0].context or ''} {entries[0].ai_response}"
    assert re.search(r"gate|approv|승인|게이트", haystack, re.I)
