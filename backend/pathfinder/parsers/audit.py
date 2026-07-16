# backend/pathfinder/parsers/audit.py
from __future__ import annotations
import re
from pathfinder.models import AuditEntry
from pathfinder.parsers.redaction import redact_credentials

_ENTRY = re.compile(r"^##\s+Entry\s+(\d+):", re.MULTILINE)
_FIELD_PATS = {
    "timestamp": re.compile(r"\*\*Timestamp\*\*:\s*(.*)"),
    "user_input": re.compile(r"\*\*User Input\*\*:\s*(.*)"),
    "ai_response": re.compile(r"\*\*AI Response\*\*:\s*(.*)"),
    "context": re.compile(r"\*\*Context\*\*:\s*(.*)"),
}

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s

def parse_audit_file(markdown: str) -> list[AuditEntry]:
    matches = list(_ENTRY.finditer(markdown))
    entries: list[AuditEntry] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        block = markdown[start:end]
        fields: dict[str, str] = {}
        for key, pat in _FIELD_PATS.items():
            fm = pat.search(block)
            fields[key] = _strip_quotes(fm.group(1)) if fm else ""
        entries.append(AuditEntry(
            index=int(m.group(1)),
            timestamp=fields["timestamp"],
            user_input=redact_credentials(fields["user_input"]),
            ai_response=redact_credentials(fields["ai_response"]),
            context=fields["context"] or None,
        ))
    return entries
