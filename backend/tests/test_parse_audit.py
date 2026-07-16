# backend/tests/test_parse_audit.py
import re
from pathlib import Path
from pathfinder.parsers.audit import parse_audit_file

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
