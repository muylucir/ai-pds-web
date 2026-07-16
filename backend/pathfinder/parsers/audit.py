# backend/pathfinder/parsers/audit.py
from __future__ import annotations
import re
from pathfinder.models import AuditEntry
from pathfinder.parsers.redaction import redact_credentials

_ENTRY = re.compile(r"^##\s+Entry\s+(\d+):", re.MULTILINE)

# Matches any of the four field markers, in whatever order they appear in the
# block. Some pilot logs squash an entire entry onto one physical line (using
# literal "\n" text rather than real newlines), so field values must be
# extracted marker-to-next-marker rather than end-of-line.
_MARKER = re.compile(r"\*\*(Timestamp|User Input|AI Response|Context)\*\*:\s*")

_KEY_MAP = {
    "Timestamp": "timestamp",
    "User Input": "user_input",
    "AI Response": "ai_response",
    "Context": "context",
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

        marker_matches = list(_MARKER.finditer(block))
        fields: dict[str, str] = {}
        for i, mm in enumerate(marker_matches):
            key = _KEY_MAP[mm.group(1)]
            value_start = mm.end()
            value_end = marker_matches[i + 1].start() if i + 1 < len(marker_matches) else len(block)
            value = _strip_quotes(block[value_start:value_end])
            # First occurrence of a marker wins, matching prior behavior.
            fields.setdefault(key, value)

        entries.append(AuditEntry(
            index=int(m.group(1)),
            timestamp=fields.get("timestamp", ""),
            user_input=redact_credentials(fields.get("user_input", "")),
            ai_response=redact_credentials(fields.get("ai_response", "")),
            context=redact_credentials(fields.get("context", "")) or None,
        ))
    return entries
