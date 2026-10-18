# backend/aipds/parsers/audit.py
from __future__ import annotations
import logging
import re
from aipds.audit_log import WEB_INPUT_HEADINGS
from aipds.models import AuditEntry
from aipds.parsers.redaction import redact_credentials

_log = logging.getLogger(__name__)

# Any level-2 heading starts a candidate entry. The rules tell the agent to log
# under a SEMANTIC heading — "## Session Start", "## 최종 승인",
# "## Discovery Mode Selection" (core-workflow.md "Audit Logging" plus the
# per-stage examples) — and NEVER "## Entry N:". `## Entry N:` is still honored
# for pilot logs, and its explicit number is preserved.
_ENTRY = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LEGACY_ENTRY_NAME = re.compile(r"^Entry\s+(\d+)\s*:")

# **Read by structure; do not match a list of labels.** The upstream rule's examples are
# the four `**Timestamp**:`, `**User Input**:`, `**AI Response**:`, `**Context**:`, but
# the labels the agent actually writes differ per project (observed in the drill bucket,
# 5 projects):
#
#   **User Input (COMPLETE RAW INPUT)** — 세션 앞부분에서 제공된 원본 요청:
#   **사용자 원문 입력 (raw input, 그대로 보존):**      ← colon is inside the bold
#   - **Event**: ... / - **Answer**: ...               ← list-item labels
#   **AI Action**: / **AI 해석:** / **답변 해석:**
#   ## 2026-08-19T08:00:19Z — 워크플로우 시작           ← timestamp is in the heading
#
# While only the four labels were matched exactly, 0 of 38 entries were read from a
# Korean project's audit log, and even an English project yielded 2 of 16; the review
# screen's verification summary and gate history and the dashboard activity feed were
# empty. Template labels are translated in ko projects, and even when not translated the
# model embellishes them. So here the **shape** "bold label + colon" is taken as a
# label, and the label's **meaning** is classified into broad families.
#
# A label is a `**X**` at the start of a line (list markers allowed) where the colon is
# inside the bold (`**X:**`) or outside it (`**X**:`), or a description follows and ends
# with a colon (`**X** — description:`), or it is alone on the line. Emphasis within a
# sentence (`**차별성은 P4·P5에 있다.**`) has no colon and is not a label.
_LABEL_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?\*\*(?P<label>[^*\n]{1,80}?)\*\*(?P<rest>[^\n]*)$")
_DESCRIPTOR = re.compile(r"^\s*[—–-][^\n]*:\s*$")
# Some pilot logs crammed the four standard labels onto one line ("\n" written as
# literal characters). Such a line is split at each label and read by the rules above.
_INLINE_CANONICAL = re.compile(r"\*\*(?:Timestamp|User Input|AI Response|Context)\*\*:")

# Label families. Compared in lowercase after removing parenthetical notes and the
# trailing colon. Order is priority: when there are several AI-side labels (`AI
# Response` together with `AI Action`), the earlier one is used.
_FAMILIES: list[tuple[str, re.Pattern]] = [
    ("timestamp", re.compile(r"^(timestamp|time|date|시각|시간|일시|타임스탬프)$")),
    ("context", re.compile(r"^(context|맥락|컨텍스트)$")),
    ("user_input", re.compile(
        r"^(user\s+(raw\s+)?(input|request|answers?|reply)|raw\s+input|answers?"
        r"|사용자.*(입력|답변|요청|응답|원문|선택)|요청\s*원문|원문\s*(입력|답변))$")),
    ("ai_response:0", re.compile(r"^(ai\s+response|ai\s*응답|response|응답)$")),
    ("ai_response:1", re.compile(r"^(ai\s+action|action|ai\s*조치|조치)$")),
    ("ai_response:2", re.compile(
        r"^(ai\s*해석|답변\s*해석|해석(\s*및\s*결정)?|interpretation|decision|결정)$")),
    ("ai_response:3", re.compile(r"^(event|summary|result|요약|결과)$")),
]
_PAREN_NOTE = re.compile(r"\s*[(（][^)）]*[)）]")

# Timestamp at the start of a heading: `## 2026-08-19T08:00:19Z — title`.
_HEADING_STAMP = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)\s*(?:[—–-]\s*)?(?P<title>.*)$")

# Real agent-written logs also use a SUB-HEADING + fenced block for the raw
# input instead of a label, e.g.
#     ### 사용자 입력
#     ```
#     승인
#     ```
# and then narrate the AI's side as the prose/bullets that follow.
_SUBHEAD_INPUT = re.compile(
    r"^###\s+(?:사용자\s*(?:입력|답변)|User\s+Raw\s+Input)[^\n]*\n+"
    r"(?:```[^\n]*\n(?P<fenced>.*?)```|(?P<plain>(?:(?!^#)[^\n]*\n?)*))",
    re.MULTILINE | re.DOTALL,
)
_STOP = re.compile(r"^(#{1,6}\s|---\s*$)")


def _family(label: str) -> str | None:
    norm = _PAREN_NOTE.sub("", label).strip().rstrip(":：").strip().lower()
    norm = re.sub(r"\s+", " ", norm)
    for family, pattern in _FAMILIES:
        if pattern.match(norm):
            return family
    return None


def _split_inline_markers(block: str) -> str:
    lines = []
    for line in block.splitlines():
        if len(_INLINE_CANONICAL.findall(line)) > 1:
            parts = _INLINE_CANONICAL.split(line)
            marks = _INLINE_CANONICAL.findall(line)
            if parts[0].strip():
                lines.append(parts[0].rstrip())
            lines.extend(f"{m} {p.strip()}" for m, p in zip(marks, parts[1:]))
        else:
            lines.append(line)
    return "\n".join(lines)


def _label_at(line: str) -> tuple[str, str] | None:
    """If this line is a label, (family, value on the same line). None if not a label or
    the family is unknown."""
    m = _LABEL_LINE.match(line)
    if not m:
        return None
    label, rest = m.group("label"), m.group("rest")
    if label.rstrip().endswith((":", "：")):
        value = rest
    elif rest.lstrip().startswith((":", "：")):
        value = rest.lstrip()[1:]
    elif _DESCRIPTOR.match(rest) or not rest.strip():
        value = ""
    else:
        return None
    family = _family(label)
    return (family, value.strip()) if family else ("other", value.strip())


def _clean(text: str) -> str:
    """Strip fences, quote markers, and surrounding quotes from a value, leaving only
    the human-readable original text."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    lines = [re.sub(r"^\s*>\s?", "", ln) for ln in lines]
    s = "\n".join(lines).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”":
        s = s[1:-1]
    elif len(s) >= 2 and s[0] == "“" and s[-1] == "”":
        s = s[1:-1]
    return s.strip()


def _strip_sub_headings(text: str) -> str:
    """Drop `### ...` sub-sections and fenced blocks from a narrated body so
    what remains reads as the AI's response summary."""
    without_fences = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL)
    lines = [ln for ln in without_fences.splitlines()
             if not ln.startswith("#") and ln.strip() not in ("", "---")]
    return "\n".join(lines).strip()


def _fields(block: str) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """Collect a block's label fields by family. The second value is the line ranges
    occupied by user input.

    A value is the rest of the label line plus the lines up to the next label, heading,
    or rule. If the same family appears more than once, the first is used. The AI side
    uses the highest-priority family.
    """
    lines = block.splitlines()
    # Lines inside a fence are neither labels nor terminators; they are transcribed
    # original text.
    fenced = []
    in_fence = False
    for ln in lines:
        opens = ln.lstrip().startswith("```")
        fenced.append(in_fence or opens)
        if opens:
            in_fence = not in_fence
    found: dict[str, str] = {}
    input_spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        hit = None if fenced[i] else _label_at(lines[i])
        if hit is None:
            i += 1
            continue
        family, first = hit
        j = i + 1
        while j < len(lines) and (fenced[j] or (
                _label_at(lines[j]) is None and not _STOP.match(lines[j]))):
            j += 1
        value = _clean("\n".join([first, *lines[i + 1:j]]))
        if family == "user_input":
            input_spans.append((i, j))
        if family != "other" and value:
            found.setdefault(family, value)
        i = j
    fields = {k: v for k, v in found.items() if not k.startswith("ai_response:")}
    ai = sorted(k for k in found if k.startswith("ai_response:"))
    if ai:
        fields["ai_response"] = found[ai[0]]
    return fields, input_spans


def _sections(markdown: str) -> list[tuple[str, str]]:
    """List of (heading, body) per `##` heading.

    **In logs that use timestamp headings, a `##` without a timestamp is part of the
    previous entry.** Logs exist in which the agent wrote the per-question answers
    within an entry as `## Question N` instead of `### Question N` (observed: one entry
    was split into `## Question 1`–`8` and the answers vanished from that entry). If a
    majority of entry headings carry a timestamp, that is this file's entry marker, and
    a `##` without one is read as a subsection and attached to the previous entry.

    A `##` inside a code fence is not a heading; there are entries that copied the
    question file the user answered verbatim into a fence.
    """
    raw: list[tuple[str, str]] = []
    body: list[str] = []
    heading: str | None = None
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _ENTRY.match(line)
        if m:
            if heading is not None:
                raw.append((heading, "\n".join(body)))
            heading, body = m.group(1).strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        raw.append((heading, "\n".join(body)))
    stamped = sum(1 for h, _ in raw if _HEADING_STAMP.match(h))
    if not raw or stamped * 2 < len(raw):
        return raw
    merged: list[tuple[str, str]] = []
    for heading, body in raw:
        if merged and not _HEADING_STAMP.match(heading):
            prev_h, prev_b = merged[-1]
            merged[-1] = (prev_h, f"{prev_b}\n### {heading}\n{body}")
        else:
            merged.append((heading, body))
    return merged


def _is_web_input(heading: str) -> bool:
    """Is this a user-input entry written by the web app (aipds/audit_log)? Recognized
    by the start of the heading."""
    return any(heading.startswith(h) for h in WEB_INPUT_HEADINGS.values())


def _pair_web_inputs(rows: list[tuple[AuditEntry, bool]]) -> list[AuditEntry]:
    """Merge a web user-input entry and the agent entry after it into one interaction.

    In the upstream rule, an entry holds both input and response for one interaction.
    Once the web app started writing the input, that split into two entries, while the
    screens (verification summary, gate history, activity feed) still expect a single
    "input → response" line. The original text always comes from the web app's entry;
    even if the agent habitually re-wrote the input, its version may be a summary
    (observed, see the audit_log header). The time also comes from the web app. If no
    agent entry follows (the turn was cut off), it remains an input-only entry.
    """
    out: list[AuditEntry] = []
    i = 0
    while i < len(rows):
        entry, web = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if web and nxt is not None and not nxt[1]:
            agent = nxt[0]
            out.append(agent.model_copy(update={
                "timestamp": entry.timestamp or agent.timestamp,
                "user_input": entry.user_input}))
            i += 2
            continue
        out.append(entry)
        i += 1
    return out


def parse_audit_file(markdown: str) -> list[AuditEntry]:
    sections = _sections(markdown)
    rows: list[tuple[AuditEntry, bool]] = []
    position = 0
    for heading, body in sections:
        block = _split_inline_markers(body)
        stamp = _HEADING_STAMP.match(heading)
        title = stamp.group("title").strip() if stamp else heading

        fields, input_spans = _fields(block)
        sub_input = _SUBHEAD_INPUT.search(block)
        if not fields and sub_input is None and stamp is None:
            # A heading with no fields and no timestamp is prose (a findings
            # section, the doc title's sibling sections, etc.), not a logged
            # interaction. Counting it would put blank rows in the review panels.
            continue

        if sub_input is not None:
            raw = sub_input.group("fenced") or sub_input.group("plain") or ""
            fields.setdefault("user_input", raw.strip())
            if not fields.get("ai_response"):
                fields["ai_response"] = _strip_sub_headings(block[sub_input.end():])
        web = _is_web_input(heading)
        if web:
            # The web app's entry holds only the input; the remaining lines (time label)
            # are not read as a response.
            fields["ai_response"] = ""
        elif not fields.get("ai_response"):
            # Without a labeled AI-side field, the section's narrative is itself the
            # AI's record; read it with the user-input lines excluded.
            lines = block.splitlines()
            keep = [ln for k, ln in enumerate(lines)
                    if not any(a <= k < b for a, b in input_spans)]
            fields["ai_response"] = _strip_sub_headings("\n".join(keep))
        if not fields.get("timestamp") and stamp is not None:
            fields["timestamp"] = stamp.group("ts")
        # Heading text is the best available context for these entries.
        if not fields.get("context"):
            fields["context"] = title

        # Legacy "## Entry N:" logs carry their own number; semantic headings
        # get a 1-based sequence so the UI can sort newest-first.
        legacy = _LEGACY_ENTRY_NAME.match(heading)
        position += 1
        index = int(legacy.group(1)) if legacy else position

        rows.append((AuditEntry(
            index=index,
            timestamp=fields.get("timestamp", ""),
            user_input=redact_credentials(fields.get("user_input", "")),
            ai_response=redact_credentials(fields.get("ai_response", "")),
            context=redact_credentials(fields.get("context", "")) or None,
        ), web))
    entries = _pair_web_inputs(rows)
    if any(web for _, web in rows):
        # Pairing leaves gaps in the numbering, so renumber. Legacy `## Entry N:` logs
        # have no web entries.
        entries = [e.model_copy(update={"index": n})
                   for n, e in enumerate(entries, start=1)]
    if not entries and len(sections) > 1:
        # Multiple headings but none could be read means a shape the parser does not
        # know. An empty panel is indistinguishable from "no records", so surface it
        # here.
        _log.warning("audit.md has %d sections but none parsed as an entry",
                     len(sections))
    return entries
