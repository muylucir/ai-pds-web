# backend/aipds/survey/inputs.py -- the Envision artifacts survey question generation pulls
# in from outside the prototype spec.
#
# **Why the spec alone is not enough.** The spec's `Problem Statement` and `Business Value`
# are one- or two-line summaries, and the evidence behind those summaries (each pain point's
# severity, frequency and current workaround; the priority and its reasoning; the industry and
# how work is done today) exists only in the Envision artifacts. A survey validates that
# evidence, so building questions from the summary alone produces questions that do not know
# what they are validating.
#
# **Why `discovery-document.md` is not included (measured 2026-08-20).** At the moment a
# survey is built (Current Stage in `aiplc-state.md` being Prototype & Validation) that
# document held only `# Part 1: Envision` -- Part 2 was unwritten even in a project where
# Solution Analysis was marked `[x]` complete -- and the `## 페인 포인트 분석 요약` inside it
# is a lossily compressed restatement of `pain-point-analysis.md` below. So its only unique
# contribution is the PR/FAQ prose, and that document's internal FAQ carries more than 20
# questions on pricing, TAM, unit economics and time to profitability (measured: 21 in ship, 24
# in test1111). Those are exactly the axes survey/builder.py's prompt **explicitly says not to
# ask about**. What it drags in outweighs what it gives.
#
# **Every lookup is fail-soft.** A supporting document reinforces a survey rather than being
# its premise. An exception escaping here would fall to the 502 in routes/surveys.py, making
# the survey impossible to build at all because of one document that would merely have been
# nice to have.
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aipds.agent.question_file_answers import looks_like_question_file

_log = logging.getLogger(__name__)

#: The directory the Envision artifacts live in. It is per project rather than per prototype
#: -- which is why this module owns it rather than proto/layout.py (that one is the sole owner
#: of the prototype output layout).
ENVISION_PREFIX = "aiplc-docs/discovery/envision/"

#: The fixed key the rules declare (envision.md:190). Present in 3 of 3 measured projects.
PAIN_POINTS_KEY = ENVISION_PREFIX + "pain-point-analysis.md"

#: The canonical name for the business context -- **the rules do not declare it.** The agent
#: invents the name, so `_business_context_keys` below widens the candidates by prefix. When
#: this name is present it is the synthesised version and so beats the other variants.
BUSINESS_CONTEXT_KEY = ENVISION_PREFIX + "business-context.md"

_BUSINESS_CONTEXT_STEM = "business-context"

#: The maximum number of characters allowed from one document. The measured maximum was
#: 19,472 bytes (a Korean pain point analysis), so only a pathological document reaches this
#: cap. The cap exists less for token cost than to stop **one document from dominating the
#: prompt**.
MAX_CHARS = 40_000


@dataclass(frozen=True)
class DiscoveryContext:
    """The Envision evidence to carry into survey generation. Both may be absent."""

    pain_points: str | None = None
    business_context: str | None = None


def _clip(text: str, key: str) -> str:
    """Clip to the cap. **Say so when it was clipped** -- a silent truncation reads as
    "all of it was included".
    """
    if len(text) <= MAX_CHARS:
        return text
    _log.warning("truncated %s to %d chars for the survey prompt (was %d)",
                 key, MAX_CHARS, len(text))
    return text[:MAX_CHARS]


#: A questionnaire's option line. The same shape as `_OPTION` in `parsers/questions.py`.
_OPTION_LINE = re.compile(r"^([A-F]|X)\)\s")

#: The answer tag. The `^` anchor is essential for the same reason as `_ANSWER_SLOT` in
#: `looks_like_question_file` -- an audit document **quotes** this tag inside a sentence.
_ANSWER_LINE = re.compile(r"^\[Answer\]:[ \t]*(.*)$")


def _scrub(text: str) -> tuple[str, int, int]:
    """The body with the questionnaire skeleton stripped, the number of filled answers, and the
    total number of answer slots.

    **Stripping line by line, without reconstructing paragraphs, is the point.** An answer can
    be several paragraphs (measured: `ship`'s Question 1 answer is 4 paragraphs), and the
    paragraphs that follow describe how work is done today and where the bottlenecks are --
    exactly the context we want. Extracting the answers with `parse_question_file` loses those
    paragraphs: that parser's `[Answer]:` regex catches one line (its purpose is the
    AskUserQuestion answer round trip) and the remaining paragraphs are absorbed into the next
    question's body. Measured, 1,153 characters became 263, and what was cut was the body.

    Only two things are stripped -- the option lines and the `[Answer]:` tag. The tag has only
    **its prefix** removed rather than its line deleted, which preserves the answer body
    attached after it.
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
    """Strip the skeleton from a questionnaire-shaped document. Prose passes through unchanged.

    **Why the decision is not made by name (measured 2026-08-20).** `ship`'s
    `business-context-freeform.md` has no `question` in its name and so passes a name filter,
    while its body was an AIPLC questionnaire of `## Question 1` through 5 plus `[Answer]:`
    tags (1 of 5 answered). Unanswered tags and `A)` / `X) Other` options carried into the
    prompt make the model copy someone else's question format into its survey questions.

    **Why the skeleton is stripped rather than the document discarded.** Discarding it
    outright would lose `ship`'s business context entirely -- the answered Question 1 is the
    real context, carrying the industry, the size and how work is done today.

    **`looks_like_question_file` makes the decision alone.** The reason that module decided so
    applies unchanged here: two copies of the decision means two answers to "what is a question
    file", and documents that match on one side and not the other.

    Three branches, each a different event:

      not a questionnaire                          -> the original unchanged
      a questionnaire with at least one answer      -> the body with the skeleton stripped
      a questionnaire with answer slots, all empty  -> None (there is nothing to salvage)

    Why the last branch is needed: what remains in a questionnaire with no answers at all is
    the question sentences, and carrying those as "business context" has the model move someone
    else's questions into its own.

    Prose that happens to quote `[Answer]:` counts as having a filled `slots`, so its body
    survives as in the first branch -- one tag line does not make a perfectly good document
    disappear.
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
    except Exception:  # noqa: BLE001 -- a failed supporting lookup must not block the survey
        _log.exception("could not read %s for the survey prompt", key)
        return None
    if not text.strip():
        return None
    distilled = _distill(key, text)
    if distilled is None or not distilled.strip():
        return None
    return _clip(distilled, key)


def _business_context_keys(keys: list[str]) -> list[str]:
    """The `business-context*.md` candidates with the questionnaires removed. When the canonical
    name is present, only that one.

    Excluding names containing `question` is the point. `business-context-questions.md` is the
    **questionnaire** the rules declare (envision.md:52) and its body is options and
    `[Answer]:` tags -- carried in as context, the model copies someone else's question format.
    The measured bucket also held `-clarification-questions.md` and `-followup-questions.md`.
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
        # The synthesised version beats the raw input (`-input.md`) -- test1111's actual
        # state.
        return [BUSINESS_CONTEXT_KEY]
    return sorted(candidates)


async def _business_context(s3) -> str | None:
    try:
        keys = await s3.list(ENVISION_PREFIX)
    except Exception:  # noqa: BLE001 -- demoted for the same reason as above
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
    # The source is attached only when there are several variants. The raw input and the
    # synthesised version can both be caught, and this keeps the model from reading two
    # versions of the same fact as two separate facts. Attaching it when there is only one
    # merely leaks a meaningless S3 key into the prompt -- the section's title is already
    # supplied by survey/builder.py's prompt.
    joined = "\n\n".join(f"[{key}]\n{text}" for key, text in found)
    return _clip(joined, ENVISION_PREFIX + "business-context*.md")


async def gather_context(s3) -> DiscoveryContext:
    """Gather this project's Envision evidence. What is absent stays None."""
    return DiscoveryContext(
        pain_points=await _get(s3, PAIN_POINTS_KEY),
        business_context=await _business_context(s3),
    )
