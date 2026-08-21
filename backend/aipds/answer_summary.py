# backend/aipds/answer_summary.py -- submitted answers as the chat text a human reads.
#
# **Why the backend (2026-08-21).** This logic lived only in
# `frontend/lib/answerSummary.ts`, and that was the root of the defect: the bubble the user
# saw on screen was built inside the browser and never went to the server. The server sent
# a different sentence of its own making to the model, and that was recorded in the
# transcript as the user's utterance (routes/answers.py). The live screen showed the real
# answers and the restored screen showed a machine phrase -- measured: 13 of one project's
# 16 user utterances were "질문에 답했습니다. 답변은 …의 [Answer]: 태그에 들어 있으니…".
#
# Two representations diverge. So the rendering lives here in one place, and the frontend
# uses the string the server built. The opposite direction -- the frontend building it and
# sending it up -- is not taken: this text is read by the model too, so it has to follow the
# project language, and having the frontend manage two languages is the shape of the
# 2026-08-04 defect (the same judgement in routes/answers.py).
#
# One more thing was fixed by the move: while it lived in the frontend, the empty-submission
# wording followed the **UI language**. A chat bubble has to be in the project language (the
# agent/prompts.py header; lib/approvalMarker.ts records the same judgement).
#
# **The contract to invert.** QuestionCard packs four shapes into one string
# (components/questions/QuestionCard.tsx):
#
#     "A"                  a bare letter
#     "A: 부연"             a letter plus free-prose elaboration
#     "A,C"                a multi-select comma join
#     "Broker: 큐를 …"      free text that happens to start like a letter
#
# Only the first three can be expanded, and **telling them apart is this module's entire
# job** -- splitting free text on `": "` corrupts the very answer it set out to preserve.
from __future__ import annotations

from aipds.models import QuestionFile, QuestionOption

#: The bubble for an empty submission. An empty string would be an empty bubble, so one
#: line is left. The same wording as the frontend's `chat.answersSubmitted` -- which now
#: receives this value instead.
_SUBMITTED = {"ko": "답변 제출", "en": "Answers submitted"}


def _letter_text(options: list[QuestionOption], letter: str) -> str | None:
    """The text of the **non-Other** option carrying that letter. None when there is none.

    Excluding is_other is deliberate: that letter is an internal notation and its `text` is
    a placeholder, not the user's answer.
    """
    for o in options:
        if not o.is_other and o.letter == letter:
            return o.text
    return None


def _expand_letter_list(options: list[QuestionOption],
                        value: str) -> str | None:
    """`"A,C"` -> `"A. 자동 생성, C. 이력 관리"`. None when it is not a list.

    If even one token is not a non-Other option's letter, **the whole thing is treated as
    free text** -- an all-or-nothing rule that keeps a sentence containing a comma from
    being mistaken for a list of letters.
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
    """The form the reader should see: the option's text when it points at an option, the\n    original when it is free text."""
    multi = _expand_letter_list(options, value)
    if multi is not None:
        return multi

    whole = _letter_text(options, value)
    if whole is not None:
        return f"{value}. {whole}"

    # "A: 부연" -- this branch is taken **only when the head is a real non-Other letter**.
    # Everything else ("Broker: …" included) is free text and is left alone.
    idx = value.find(": ")
    if idx > 0:
        head = value[:idx]
        text = _letter_text(options, head)
        if text is not None:
            return f"{head}. {text} — {value[idx + 2:]}"

    return value


def answer_summary(qfile: QuestionFile, answers: dict[int, str],
                   language: str) -> str:
    """The chat text for a submitted bundle of answers.

    It follows **the question list's order** -- the key order of `answers` is the JSON order
    the frontend sent, and if the user answered questions out of order that order disagrees
    with the screen.

    A key not present among the questions is appended rather than dropped: with a stale form
    or a renumbering, the reader losing one line of question is better than losing the answer
    itself.
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
