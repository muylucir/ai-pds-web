# backend/aipds/answer_summary.py — submitted answers as human-readable chat text.
#
# **Why the backend (2026-08-21).** This classification used to live only in
# `frontend/lib/answerSummary.ts`, and that was the root of the defect: the bubble
# the user saw on screen was built only inside the browser and never reached the
# server. The server sent the model a different sentence of its own, and that was
# recorded in the transcript as the user's utterance (routes/answers.py). The live
# screen showed the real answers while the restored screen showed machine text --
# measured: 13 of 16 user utterances in one project were
# "질문에 답했습니다. 답변은 …의 [Answer]: 태그에 들어 있으니…".
#
# Two renderings drift apart. So the rendering lives here, once, and the frontend
# uses the server-built string as-is. We do not take the direction of the frontend
# building it and sending it to the server -- the model reads this text too, so it
# must follow the project language, and having the frontend manage two languages
# is exactly the shape of the 2026-08-04 defect (the same judgment in
# routes/answers.py).
#
# One more thing got fixed by the move: while it lived in the frontend, the
# empty-submission text followed the **UI language**. Chat bubbles must be in the
# project language (agent/prompts.py header; lib/approvalMarker.ts records the
# same judgment).
#
# **The contract to unwind.** QuestionCard packs four shapes into one string
# (components/questions/QuestionCard.tsx):
#
#     "A"                  bare letter
#     "A: 부연"             letter + free-form elaboration
#     "A,C"                comma join of a multiple choice
#     "Broker: 큐를 …"      free text that starts like a letter
#
# Only the first three can be expanded, and **telling them apart is this module's
# entire job** -- splitting free text on `": "` would damage the very answer we
# meant to preserve.
from __future__ import annotations

from aipds.models import QuestionFile, QuestionOption

#: The bubble for an empty submission. An empty string would make an empty bubble, so keep one line.
#: Same wording as the frontend's `chat.answersSubmitted` -- which now receives this value.
_SUBMITTED = {"ko": "답변 제출", "en": "Answers submitted"}


def _letter_text(options: list[QuestionOption], letter: str) -> str | None:
    """Text of the **non-Other** option with that letter. None if there is none.

    Excluding is_other is intentional: that letter is an internal notation and its
    `text` is a placeholder, not the user's answer.
    """
    for o in options:
        if not o.is_other and o.letter == letter:
            return o.text
    return None


def _expand_letter_list(options: list[QuestionOption],
                        value: str) -> str | None:
    """`"A,C"` → `"A. 자동 생성, C. 이력 관리"`. None if it is not a list.

    If even one token is not the letter of a non-Other option, **the whole thing is
    treated as free text** -- all-or-nothing, so a sentence containing commas is not
    mistaken for a letter list.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 2:
        return None
    expanded: list[str] = []
    for p in parts:
        text = _letter_text(options, p)
        if text is None:
            return None
        expanded.append(f"{p}. {text}")
    return ", ".join(expanded)


def _render_answer(options: list[QuestionOption], value: str) -> str:
    """The form the reader should see: the option text if it points to an option, the original if free text."""
    multi = _expand_letter_list(options, value)
    if multi is not None:
        return multi

    whole = _letter_text(options, value)
    if whole is not None:
        return f"{value}. {whole}"

    # "A: 부연" (letter: elaboration) -- reaches this branch **only when the head is an
    # existing non-Other letter**. Everything else (including "Broker: …") is free
    # text and left untouched.
    idx = value.find(": ")
    if idx > 0:
        head = value[:idx]
        text = _letter_text(options, head)
        if text is not None:
            return f"{head}. {text} — {value[idx + 2:]}"

    return value


def answer_summary(qfile: QuestionFile, answers: dict[int, str],
                   language: str) -> str:
    """Chat text for a batch of submitted answers.

    Follows **the order of the question list** -- the key order of `answers` is the
    order of the JSON the frontend sent, and when the user skips around between
    questions, that order diverges from the screen.

    Keys not in the question list are appended at the end rather than dropped: with a
    stale form or renumbering, the reader losing a question line is better than
    losing the answer itself.
    """
    blocks: list[str] = []
    seen: set[int] = set()

    for q in qfile.questions:
        seen.add(q.number)
        value = answers.get(q.number)
        if value is None or not value.strip():
            continue
        blocks.append(f"Q{q.number}. {q.text}\n→ {_render_answer(q.options, value)}")

    for number, value in answers.items():
        if number in seen or not value.strip():
            continue
        blocks.append(f"Q{number}.\n→ {value}")

    if blocks:
        return "\n\n".join(blocks)
    return _SUBMITTED.get(language, _SUBMITTED["ko"])
