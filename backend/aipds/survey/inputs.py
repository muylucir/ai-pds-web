# backend/aipds/survey/inputs.py — Envision artifacts that survey question generation pulls in
# from outside the prototype spec.
#
# **Why the spec alone is not enough.** The spec's `Problem Statement` and `Business Value` are
# one- or two-line summaries, and the evidence behind them (severity, frequency, and current
# workarounds per pain point; priorities and their reasons; industry and current work practices)
# exists only in the Envision artifacts. The survey validates that evidence, so generating questions
# from the summary alone produces questions that do not know what they are validating.
#
# **Why `discovery-document.md` is not included (observed 2026-08-20).** At the time the survey is
# generated (Current Stage in `aiplc-state.md` is Prototype & Validation), that document contained
# only `# Part 1: Envision` — even in projects where Solution Analysis was marked `[x]` done,
# Part 2 had not been written — and its `## 페인 포인트 분석 요약` is a lossy restatement of
# `pain-point-analysis.md` below. So its only unique contribution is the PR/FAQ prose, yet that
# document's internal FAQ has more than 20 questions on pricing, TAM, unit economics, and time to
# profitability (observed: 21 in ship, 24 in test1111). Those are exactly the axes the
# survey/builder.py prompt **explicitly says not to ask about**. It pulls in more than it
# contributes.
#
# **Every lookup is fail-soft.** Supporting documents are an enhancement to the survey, not a
# prerequisite. If an exception leaked here, it would fall into the 502 in routes/surveys.py, and the
# survey could not be generated at all because of one nice-to-have document.
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aipds.agent.question_file_answers import looks_like_question_file

_log = logging.getLogger(__name__)

#: The directory where Envision artifacts live. It is per project, not per prototype —
#: so this module owns it rather than proto/layout.py (that module is the sole owner of the
#: prototype artifact layout).
ENVISION_PREFIX = "aiplc-docs/discovery/envision/"

#: The fixed key the rules declare (envision.md:190). Present in 3/3 of the 3 projects observed.
PAIN_POINTS_KEY = ENVISION_PREFIX + "pain-point-analysis.md"

#: The canonical name for the business context — **the rules do not declare it.** The agent makes up
#: the name, so `_business_context_keys` below widens candidates by prefix. If this name is present,
#: it is the synthesized version and beats the other variants.
BUSINESS_CONTEXT_KEY = ENVISION_PREFIX + "business-context.md"

_BUSINESS_CONTEXT_STEM = "business-context"

#: Maximum characters allowed per document. The observed maximum was 19,472 bytes (a Korean
#: pain point analysis), so only pathological documents reach this cap. The cap exists not so
#: much for token cost as to keep **one document from dominating the prompt**.
MAX_CHARS = 40_000


@dataclass(frozen=True)
class DiscoveryContext:
    """Envision evidence to include in survey generation. Either may be absent."""

    pain_points: str | None = None
    business_context: str | None = None


def _clip(text: str, key: str) -> str:
    """Truncates at the cap. **Says so if truncated** — silent truncation reads as "everything was included"."""
    if len(text) <= MAX_CHARS:
        return text
    _log.warning("truncated %s to %d chars for the survey prompt (was %d)",
                 key, MAX_CHARS, len(text))
    return text[:MAX_CHARS]


#: Option lines in a questionnaire. Same shape as `_OPTION` in `parsers/questions.py`.
_OPTION_LINE = re.compile(r"^([A-J]|X)\)\s")

#: The answer tag. The `^` anchor is required for the same reason as `_ANSWER_SLOT` in
#: `looks_like_question_file` — audit documents **quote** this tag inside sentences.
_ANSWER_LINE = re.compile(r"^\[Answer\]:[ \t]*(.*)$")


def _scrub(text: str) -> tuple[str, int, int]:
    """Body with the questionnaire skeleton stripped, number of filled answers, total number of answer slots.

    **The point is stripping line by line without reconstructing paragraphs.** An answer can span
    several paragraphs (observed: `ship`'s Question 1 answer was 4 paragraphs), and the later
    paragraphs describe current work practices and bottlenecks — exactly the context we want.
    Extracting answers with `parse_question_file` loses those paragraphs: that parser's
    `[Answer]:` regex captures only one line (its purpose is the AskUserQuestion answer round trip),
    and the remaining paragraphs are absorbed into the next question's body. Observed: 1,153
    characters became 263, and what was cut was the body.

    Only two things are stripped — option lines and the `[Answer]:` tag. The tag does not delete the
    line; **only the prefix** is removed so the answer body after it survives.
    """
    kept: list[str] = []
    slots = filled = 0
    for line in text.splitlines():
        if _OPTION_LINE.match(line):
            continue
        answer = _ANSWER_LINE.match(line)
        if answer is None:
            kept.append(line)
            continue
        slots += 1
        body = answer.group(1).strip()
        if body:
            filled += 1
            kept.append(body)
    return "\n".join(kept), filled, slots


def _distill(key: str, text: str) -> str | None:
    """Strips the skeleton from questionnaire-shaped documents. Prose passes through unchanged.

    **Why not decide by name (observed 2026-08-20).** `ship`'s
    `business-context-freeform.md` has no `question` in its name and passes the name filter,
    yet its body was an AIPLC questionnaire with `## Question 1~5` + `[Answer]:` tags
    (only 1 of 5 questions answered). If unanswered tags and `A)`/`X) Other` options were
    included in the prompt, the model would copy someone else's question format to build survey questions.

    **Why strip only the skeleton rather than discard it.** Discarding it entirely would make `ship` lose
    its business context completely — the answered Question 1 is the real context, covering
    industry, scale, and current work practices.

    **The decision is made solely by `looks_like_question_file`.** The reason that module decided so
    applies here as well: two versions of the check would give two answers to "what is a question
    file", producing documents caught by one and not the other.

    There are three branches, and each is a different event:

      not a questionnaire                    -> original text as is
      questionnaire + at least one answer    -> body with the skeleton stripped
      questionnaire + slots present, all empty -> None (nothing to salvage)

    Why the last branch is needed: what remains in a questionnaire with no answers is only the
    question sentences, and including those as "business context" would make the model carry
    someone else's questions over as its own.

    Prose that happens to quote `[Answer]:` is counted as having filled `slots`, so its body
    survives as in the first branch — a sound document does not vanish because of one tag line.
    """
    if not looks_like_question_file(key, text):
        return text
    scrubbed, filled, slots = _scrub(text)
    if slots and not filled:
        _log.info("%s is an unanswered question file; nothing to carry into the "
                  "survey prompt", key)
        return None
    _log.info("%s is a question file; scrubbed its scaffolding "
              "(%d of %d answer slots filled)", key, filled, slots)
    return scrubbed


async def _get(s3, key: str) -> str | None:
    try:
        text = await s3.get(key)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 — a failure to fetch a supporting document does not block the survey
        _log.exception("could not read %s for the survey prompt", key)
        return None
    if not text.strip():
        return None
    distilled = _distill(key, text)
    if distilled is None or not distilled.strip():
        return None
    return _clip(distilled, key)


def _business_context_keys(keys: list[str]) -> list[str]:
    """Candidates among `business-context*.md` excluding questionnaires. Only the canonical name if it exists.

    Excluding names containing `question` is the point. `business-context-questions.md` is a
    **questionnaire** the rules declare (envision.md:52), and its body is options and `[Answer]:`
    tags — including it as context makes the model copy someone else's question format. The observed
    bucket also had `-clarification-questions.md` and `-followup-questions.md`.
    """
    candidates = []
    for key in keys:
        name = key[len(ENVISION_PREFIX):]
        if "/" in name or not name.endswith(".md"):
            continue
        if not name.startswith(_BUSINESS_CONTEXT_STEM):
            continue
        if "question" in name:
            continue
        candidates.append(key)
    if BUSINESS_CONTEXT_KEY in candidates:
        # The synthesized version beats the original input (`-input.md`) — this is test1111's actual state.
        return [BUSINESS_CONTEXT_KEY]
    return sorted(candidates)


async def _business_context(s3) -> str | None:
    try:
        keys = await s3.list(ENVISION_PREFIX)
    except Exception:  # noqa: BLE001 — downgraded for the same reason as above
        _log.exception("could not list %s for the survey prompt", ENVISION_PREFIX)
        return None

    found = []
    for key in _business_context_keys(keys):
        text = await _get(s3, key)
        if text:
            found.append((key, text))
    if not found:
        return None
    if len(found) == 1:
        return found[0][1]
    # Attach the source only when there are several variants. The original input and the synthesized
    # version can both be picked up, so this keeps the model from reading two versions of the same
    # fact as separate facts. Attaching it when there is only one just leaks a meaningless S3 key into
    # the prompt — the section title is already added by the survey/builder.py prompt.
    joined = "\n\n".join(f"[{key}]\n{text}" for key, text in found)
    return _clip(joined, ENVISION_PREFIX + "business-context*.md")


async def gather_context(s3) -> DiscoveryContext:
    """Gathers this project's Envision evidence. Missing pieces stay None."""
    return DiscoveryContext(
        pain_points=await _get(s3, PAIN_POINTS_KEY),
        business_context=await _business_context(s3),
    )
