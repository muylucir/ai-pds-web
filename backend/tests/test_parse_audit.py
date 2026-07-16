# backend/tests/test_parse_audit.py
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
