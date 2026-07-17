# backend/pathfinder/parsers/questions.py
from __future__ import annotations
import logging
import re
from pathfinder.models import Question, QuestionOption, QuestionFile

logger = logging.getLogger(__name__)

_Q_HEADER = re.compile(r"^#{2,3}\s+Question\s+(\d+)\s*$", re.MULTILINE)
_CAT_HEADER = re.compile(r"^##\s+(?!Question\b)(.+?)\s*$", re.MULTILINE)
_OPTION = re.compile(r"^([A-F]|X)\)\s+(.*)$")
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$")
_RECO = re.compile(r"\s*←\s*(추천|recommended).*$", re.IGNORECASE)

def parse_question_file(name: str, markdown: str) -> QuestionFile:
    try:
        return _parse(name, markdown)
    except Exception:
        logger.warning("parse_question_file falling back to raw markdown for %s", name, exc_info=True)
        return QuestionFile(name=name, preamble=None, questions=[],
                            parse_ok=False, raw_markdown=markdown)

def _parse(name: str, markdown: str) -> QuestionFile:
    lines = markdown.splitlines()
    questions: list[Question] = []
    current_category: str | None = None
    preamble_lines: list[str] = []
    seen_first_header = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        qm = _Q_HEADER.match(line)
        cm = _CAT_HEADER.match(line)
        # Category header (## X, but not "## Question")
        if cm and not qm and line.startswith("## "):
            current_category = cm.group(1).strip()
            seen_first_header = True
            i += 1
            continue
        if qm:
            seen_first_header = True
            number = int(qm.group(1))
            i += 1
            text_parts: list[str] = []
            options: list[QuestionOption] = []
            answer: str | None = None
            # consume until next header
            while i < n and not _Q_HEADER.match(lines[i]) and not (
                lines[i].startswith("## ") and not _Q_HEADER.match(lines[i])
            ):
                raw = lines[i].strip()
                om = _OPTION.match(raw)
                am = _ANSWER.match(raw)
                if am:
                    answer = am.group(1).strip() or None
                elif om:
                    letter, otext = om.group(1), om.group(2).strip()
                    recommended = bool(_RECO.search(otext))
                    otext = _RECO.sub("", otext).strip()
                    options.append(QuestionOption(
                        letter=letter, text=otext,
                        is_other=(letter == "X" or otext.lower().startswith("other")),
                        recommended=recommended,
                    ))
                elif raw and not options:
                    text_parts.append(raw)
                i += 1
            questions.append(Question(
                number=number, category=current_category,
                text=" ".join(text_parts).strip(), options=options, answer=answer,
            ))
            continue
        if not seen_first_header and line.strip():
            preamble_lines.append(line.rstrip())
        i += 1

    if not questions:
        raise ValueError("no questions found")
    preamble = "\n".join(preamble_lines).strip() or None
    return QuestionFile(name=name, preamble=preamble, questions=questions,
                        parse_ok=True, raw_markdown=None)

def serialize_answers(markdown: str, answers: dict[int, str]) -> str:
    present = {q.number for q in _parse("_", markdown).questions}
    missing = set(answers) - present
    if missing:
        raise KeyError(f"question numbers not in file: {sorted(missing)}")

    lines = markdown.splitlines(keepends=True)
    current_q: int | None = None
    out: list[str] = []
    for line in lines:
        # Match on the same basis _parse uses (the raw line minus its line
        # ending) so header detection can't diverge between the two passes.
        qm = _Q_HEADER.match(line.rstrip("\r\n"))
        stripped = line.strip()
        if qm:
            current_q = int(qm.group(1))
            out.append(line)
            continue
        if current_q in answers and stripped.startswith("[Answer]:"):
            m_end = re.search(r"(\r\n|\r|\n)$", line)
            ending = m_end.group(1) if m_end else ""
            out.append(f"[Answer]: {answers[current_q]}{ending}")
            continue
        out.append(line)
    return "".join(out)
