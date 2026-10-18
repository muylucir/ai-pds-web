# backend/aipds/audit_log.py — the web is the side that records user input verbatim in audit.md.
#
# **Why the web (measured 2026-10-07).** The upstream rules say to record "every user input verbatim, with
# a timestamp" in audit.md (core-workflow.md Audit Logging). While the agent was doing that, three things
# went wrong.
# - **It made up times.** ws-b wrote "12:50:00" and "13:05:00" on entries actually written at 12:46 —
#   in the future, and round values on the hour. The agent has no clock.
# - **It summarized.** ws-l's user input was left as "검증 설문 결과 제공… 5명 응답. Q1:5.0/5…".
#   That is what the rules forbid.
# - **It was slow.** Every turn, the model retyped the user's original text. A single audit Edit was 0.9–3.2K
#   characters and 5–31 seconds (ws-c, ws-b).
# To begin with, the agent never sees the original text — form answers are assembled by the web as
# "질문에 답했습니다: …" and passed on. Having the side that holds the original text write it is closer to
# what the rules require. The agent appends only its own responses, decisions, and context (the
# Required artifacts section of discovery-config/CLAUDE.md).
#
# **The original text goes in a quote block (`> `).** Users paste markdown — if a heading or a code fence
# lands at the start of a line as is, the audit log's entry boundaries break (parsers/audit.py splits entries
# on `##` and fences on ```). A line with a quote marker is neither a heading, a fence, nor a label, and
# the parser strips that marker and returns the original text.
from __future__ import annotations

import errno
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aipds.parsers.redaction import redact_credentials
from aipds.pathsafe import read_text_beneath, write_beneath

AUDIT_KEY = "aiplc-docs/audit.md"

#: Heading prefix of entries the web writes. The parser uses it to recognize the web's entries and pair them
#: with the agent entries that follow (parsers/audit.py). The document follows the project language, so there are two variants.
WEB_INPUT_HEADINGS = {"ko": "사용자 입력 (AI-PDS Web 기록)",
                      "en": "User Input (recorded by AI-PDS Web)"}

#: Title when the file does not exist yet. On the first turn, the web writes before the agent.
_TITLE = "# AI-PLC Audit Log"

_SOURCES = {
    "ko": {"chat": "채팅", "answers": "질문 답변", "approval": "문서 승인"},
    "en": {"chat": "chat", "answers": "question answers", "approval": "document approval"},
}


@dataclass(frozen=True)
class UserInput:
    """The user input that opened the turn. The route builds it — only the route knows what the user's words are
    (the text of a question-answer turn appends instructions for the agent after the answers)."""
    text: str
    source: str              # "chat" | "answers" | "approval"
    language: str = "ko"
    detail: str | None = None   # question file name, for an answer turn


def _lang(language: str) -> str:
    return "en" if language == "en" else "ko"


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote(text: str) -> str:
    """Original text as a quote block, line by line. Empty lines are kept as `>` too so the quote does not break."""
    lines = text.strip("\n").splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def entry(record: UserInput, now: datetime | None = None) -> str:
    """One entry to append to audit.md (including the blank lines before and after)."""
    lang = _lang(record.language)
    source = _SOURCES[lang].get(record.source, record.source)
    if record.detail:
        source = f"{source} · {record.detail}"
    return "\n".join([
        "",
        f"## {WEB_INPUT_HEADINGS[lang]} — {source}",
        f"**Timestamp**: {_stamp(now)}",
        "**User Input**:",
        _quote(redact_credentials(record.text)),
        "",
    ])


def append(local_root: Path, record: UserInput, now: datetime | None = None) -> str:
    """Append an entry to the workspace's audit.md and return its key. Creates the file if absent.

    Links are not followed (`pathsafe`) — it is a tree the agent writes to. If it was a link, we do not
    read past it and start a new file instead.
    """
    try:
        existing = read_text_beneath(Path(local_root), AUDIT_KEY)
    except FileNotFoundError:
        existing = f"{_TITLE}\n"
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        existing = f"{_TITLE}\n"
    write_beneath(Path(local_root), AUDIT_KEY,
                  existing.rstrip("\n") + "\n" + entry(record, now))
    return AUDIT_KEY
