# backend/aipds/parsers/audit.py
from __future__ import annotations
import re
from aipds.models import AuditEntry
from aipds.parsers.redaction import redact_credentials

# Any level-2 heading starts a candidate entry. The rules tell the agent to log
# under a SEMANTIC heading — "## Session Start", "## 최종 승인",
# "## Discovery Mode Selection" (core-workflow.md "Audit Logging" plus the
# per-stage examples) — and NEVER "## Entry N:". Matching only the latter made
# every real audit.md parse to zero entries, which left the review page's
# "AI 검증 요약"/"승인 게이트 이력" panels empty and made the approval button
# look like it did nothing. `## Entry N:` is still honored for pilot logs, and
# its explicit number is preserved.
_ENTRY = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LEGACY_ENTRY_NAME = re.compile(r"^Entry\s+(\d+)\s*:")

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

# Real agent-written logs also use a SUB-HEADING + fenced block for the raw
# input instead of the `**User Input**:` marker, e.g.
#     ### 사용자 입력
#     ```
#     승인
#     ```
# and then narrate the AI's side as the prose/bullets that follow. Both shapes
# appear in the same file, so both are supported: without this, entries in that
# style parsed with empty user_input/ai_response and the review panels showed
# blank rows.
_SUBHEAD_INPUT = re.compile(
    r"^###\s+(?:사용자\s*(?:입력|답변)|User\s+Raw\s+Input)[^\n]*\n+"
    r"(?:```[^\n]*\n(?P<fenced>.*?)```|(?P<plain>(?:(?!^#)[^\n]*\n?)*))",
    re.MULTILINE | re.DOTALL,
)


def _strip_sub_headings(text: str) -> str:
    """Drop `### ...` sub-sections and fenced blocks from a narrated body so
    what remains reads as the AI's response summary."""
    without_fences = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL)
    lines = [ln for ln in without_fences.splitlines()
             if not ln.startswith("#") and ln.strip() not in ("", "---")]
    return "\n".join(lines).strip()

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s

def parse_audit_file(markdown: str) -> list[AuditEntry]:
    matches = list(_ENTRY.finditer(markdown))
    entries: list[AuditEntry] = []
    position = 0
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        block = markdown[start:end]

        marker_matches = list(_MARKER.finditer(block))
        sub_input = _SUBHEAD_INPUT.search(block)
        if not marker_matches and sub_input is None:
            # A heading with no audit markers is prose (a findings section, the
            # doc title's sibling sections, etc.), not a logged interaction.
            # Counting it would put blank rows in the review panels.
            continue

        fields: dict[str, str] = {}
        for i, mm in enumerate(marker_matches):
            key = _KEY_MAP[mm.group(1)]
            value_start = mm.end()
            value_end = marker_matches[i + 1].start() if i + 1 < len(marker_matches) else len(block)
            value = _strip_quotes(block[value_start:value_end])
            # First occurrence of a marker wins, matching prior behavior.
            fields.setdefault(key, value)

        # Sub-heading style fills whatever the markers didn't: the fenced block
        # is the raw input, and the surrounding prose is the AI's narration.
        if sub_input is not None:
            raw = sub_input.group("fenced") or sub_input.group("plain") or ""
            fields.setdefault("user_input", raw.strip())
            if not fields.get("ai_response"):
                body = block[sub_input.end():]
                fields["ai_response"] = _strip_sub_headings(body)
        # Heading text is the best available context for these entries.
        if not fields.get("context"):
            fields["context"] = m.group(1).strip()

        # Legacy "## Entry N:" logs carry their own number; semantic headings
        # get a 1-based sequence so the UI can sort newest-first.
        legacy = _LEGACY_ENTRY_NAME.match(m.group(1))
        position += 1
        index = int(legacy.group(1)) if legacy else position

        entries.append(AuditEntry(
            index=index,
            timestamp=fields.get("timestamp", ""),
            user_input=redact_credentials(fields.get("user_input", "")),
            ai_response=redact_credentials(fields.get("ai_response", "")),
            context=redact_credentials(fields.get("context", "")) or None,
        ))
    return entries
