# backend/aipds/agent/question_file_answers.py — writes submitted answers back into the
# `[Answer]:` slots of the question file.
#
# **Why this is needed.** The ai-plc workflow runs on the premise that the question file is
# the answer sheet: aws-aiplc-rule-details/common/question-format-guide.md instructs "Read the
# question file / Extract answers after [Answer]: tags", and
# common/session-continuity.md:31-33 says to **read** files such as `strategy-questions.md`
# when resuming a stage. AI-PDS delivered questions through AskUserQuestion and left these
# slots empty, so a resumed session could not recover decisions the user had already made
# from the file.
#
# **Why match on question text rather than number.** AskUserQuestion has a schema hard limit
# of 4 questions × 4 options. A 10-question file is split into three rounds of 4+4+2, and each
# round's question numbers restart from 1, so matching by number makes round 2's answers
# overwrite questions 1-4 while 5-8 stay empty forever — **wrong answers are recorded** with
# no error. The model writes the same question sentence in the file and in the tool, so that
# sentence is the only stable key that crosses round boundaries.
#
# **Why only one file is modified.** Question files pile up in the workspace per stage, and a
# sentence like "Would you like to proceed with these settings?" can be duplicated across
# several files. Writing to every matching file would corrupt earlier stages' records.
# One AskUserQuestion round belongs to one stage, so there is one file — pick the file with
# the most matches, and on a tie the most recently modified file wins (the agent just wrote
# the current stage's file).
#
# **A failed match is left empty.** If an answer lands on the wrong question, the next stage
# that reads the file treats a decision the user never made as fact. A person can notice an
# empty slot, but cannot notice a wrong answer.
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from aipds.parsers.questions import parse_question_file, serialize_answers
from aipds.pathsafe import read_text_beneath, write_beneath

_log = logging.getLogger("aipds.agent")

#: Candidate files. **Chosen by content, not by name.**
#:
#: It used to be `*-questions.md`, and as a result the answers in `design-context.md` were
#: lost entirely (2026-08-16 ws-a-v5: 3 questions and 3 slots, 0 recorded). The name cannot
#: be relied on because **upstream does not follow its own naming rule** —
#: question-format-guide.md prescribes `{phase-name}-questions.md`, while
#: prototype-validation.md designates Step 2's artifact as `design-context.md` and tells
#: the agent to use the question format inside it.
#:
#: Widening this was verified safe by measurement (15 files in ws-a-v5's aiplc-docs):
#: the only addition is design-context.md, and the 8 files such as audit.md,
#: discovery-document.md, and prototype-spec.md are all filtered out by the two gates below.
#:
#: Note: Workspace.list_question_files (the dashboard's question file list) still uses the
#: naming rule. The two sets diverging is intentional — answers are already visible in the
#: document panel (`[Answer]:` exposure in components/Markdown.tsx), so this avoids dragging
#: in a UI change unrelated to this defect.
_GLOB = "*.md"

#: First gate. If there is not a single `[Answer]:` at the start of a line, it is not a question file.
#:
#: The point is filtering **before parsing**. Running parse_question_file on every document
#: prints a warning + stack trace on each failure (that function's fallback warning) and
#: buries the log in noise — defeating the purpose of adding diagnostic logs. A cheap string
#: scan settles it.
#:
#: `^` is required: audit.md **quotes** the answer tag, as in `**Recorded Answer Tag**: \`[Answer]: B\``.
#: That is not at the start of a line, so it is not caught (it would also be caught by the
#: second gate, parse_ok, but ending here first is cheaper).
_ANSWER_SLOT = re.compile(r"^\[Answer\]:", re.MULTILINE)
_DOCS_DIR = "aiplc-docs"

#: Documents that are **not** question files regardless of content. The only place that excludes by name.
#:
#: **Why exclude by name (observed 2026-08-18).** The `^` anchor on `_ANSWER_SLOT` above only
#: blocks the case where audit.md **quotes** the tag (`**Recorded Answer Tag**: \`[Answer]: B\`').
#: But `core-workflow.md:303-304` requires of audit.md:
#: "**MANDATORY**: Log ALL user inputs ... Capture user's COMPLETE RAW INPUT
#: exactly as provided" — faithfully recording an answer round can put `[Answer]: A`
#: **at the start of a line**, and then the anchor lets it through. That actually happened:
#: the tag wording the agent wrote in the audit record was caught by the question parser, and
#: the agent **corrupted its own record**, saying "audit.md is not a question file, so I will remove that notation".
#:
#: In other words, audit.md is by definition a verbatim copy of user input, so **it cannot be
#: classified by its content.** The name is the only signal left. `aiplc-state.md` is included
#: for the same reason — it is our state file, it can quote answers, and it will never be a question file.
#:
#: This does not contradict not **including** files by naming rule: upstream has a precedent of
#: breaking its own naming rule (it put questions in `design-context.md`), so inclusion must be
#: judged by content. Conversely, these two are files whose **purpose upstream defined**, so their names can be trusted.
NEVER_QUESTION_FILES = frozenset({"audit.md", "aiplc-state.md"})


def looks_like_question_file(rel: str, markdown: str) -> bool:
    """Whether this document may be treated as a question round.

    **The hook and the write-back must use the same check.** Until 2026-08-18 they diverged —
    the write-back used the `^\\[Answer\\]:` anchor while the hook (claude_driver) used a simple
    `"[Answer]:" in md` containment check. So a document that merely quoted the tag was caught by the
    hook but not by the write-back. If the two gates differ, "what is a question file" has two answers.

    `rel` is a workspace-relative path. The name check uses the basename —
    `aiplc-docs/audit.md` and an audit.md in a subdirectory serve the same purpose.
    """
    if PurePosixPath(rel).name in NEVER_QUESTION_FILES:
        return False
    return bool(_ANSWER_SLOT.search(markdown))


def _norm(text: object) -> str:
    """Matching key. Collapses whitespace runs into one and ignores case.

    The parser joins multiple body lines with " " (parsers/questions.py:72), so if line breaks
    remain on the tool side, a raw comparison misses. It does nothing beyond that — flattening
    endings or punctuation could make different questions collide on the same key, and this
    function's failure direction must be "not found".
    """
    return " ".join(str(text or "").split()).casefold()


def record_answers(workspace: str, sdk_questions: list[dict],
                   answers: dict[str, str]) -> list[str]:
    """Records answers in the question file and returns the relative paths of files that actually changed.

    **No failure leaks out as an exception.** The user has already submitted the answers and
    the turn must resume — killing the turn over a failed side record would lose those answers
    (same discipline as claude_driver._save_answers_quietly).
    """
    try:
        return _record(workspace, sdk_questions, answers)
    except Exception:
        _log.exception("question-file answer write-back failed")
        return []


def _record(workspace: str, sdk_questions: list[dict],
            answers: dict[str, str]) -> list[str]:
    ws = Path(workspace)
    docs = ws / _DOCS_DIR
    if not docs.is_dir():
        # On the first turn there are no artifacts yet. This is the normal state.
        return []

    wanted = _wanted_by_text(sdk_questions, answers)
    if not wanted:
        return []

    miss = _Miss()
    best: _FileMatch | None = None
    # rglob does not guarantee order — sorting is needed to make tie-breaking reproducible
    # (when even mtime is equal, path order decides).
    for path in sorted(docs.rglob(_GLOB)):
        if path.is_symlink():
            continue  # this is the tree the agent writes — what lies past a link is not a question file
        found = _match_file(ws, path, wanted, miss)
        if found is None:
            continue
        if best is None or found.score() > best.score():
            best = found

    if best is None:
        # **Do not fail silently.** On 2026-08-16 the write-back did not happen and the log was
        # completely empty, so finding the cause took a long time.
        #
        # But the two kinds are **separated by level.** Of the 5 failures in ws-a-v5, 3 were not
        # defects — they were gate/approval questions, which have no question file in the first place
        # and are recorded only in audit.md (no place to record, so no empty slot either). Lumping those
        # with real defects under the same warning buries them in noise. The best score separates the two:
        # if a candidate came close, it is worth investigating (warning); if all are far, the question never existed (info).
        detail = ("no match for %d answer(s); best %.3f asked=%r candidate=%r",
                  len(wanted), miss.ratio, miss.asked[:60], miss.candidate[:60])
        if miss.ratio >= _NEAR_MISS_MIN:
            _log.warning("question-file write-back: " + detail[0], *detail[1:])
        else:
            _log.info("question-file write-back: " + detail[0], *detail[1:])
        return []
    rel = best.path.relative_to(ws).as_posix()
    try:
        write_beneath(ws, rel, best.new_md)
    except OSError:
        _log.exception("question file not writable: %s", best.path)
        return []
    if best.fuzzy:
        # A fuzzy match happened = **this round's Hangul was corrupted** (claude-code#83033).
        # It is the only signal of whether the literal UTF-8 instruction is working as suppression,
        # so it is logged at info — without counting it there is no way to measure layer 1's effect.
        _log.info("question-file write-back: %s (%d exact, %d fuzzy — corrupted "
                  "Hangul in this round, see claude-code#83033)",
                  rel, best.exact, best.fuzzy)
    else:
        _log.info("question-file write-back: %s (%d exact)", rel, best.exact)
    return [rel]


#: Lower bound for fuzzy matching and minimum gap from second place.
#:
#: **Why fuzzy matching is needed (claude-code#83033).** When the model writes Hangul in tool
#: parameters as `\uXXXX` escapes and mistypes the hex, that code point decodes to a "valid but wrong"
#: syllable. The density is 3-5% of syllables and it switches on intermittently per call, so in the
#: same turn the file (Write) can be clean while only the question (AskUserQuestion) is corrupted.
#: Upstream it is officially unresolved (handed to the model team, unrecoverable from the CLI), and
#: the hex typos are random, so there is no inverse transform either.
#:
#: **Why these numbers.** Measured on ws-a-v3, 6 rounds, 21 questions:
#:   correct pair 0.9677 / most similar wrong answer 0.5806 / others ≤0.375 /
#:   maximum within one round 0.32-0.40.
#: The range between 0.97 and 0.58 is empty. Even with 3 syllables corrupted in 60 characters it is about 0.95,
#: and with 1 syllable corrupted in 20 characters also 0.95, so 0.85 has plenty of margin on both sides.
#:
#: **Why a gap is also required.** Looking only at the threshold, two similar questions can both
#: exceed it, and choosing between them is a coin toss. Without a gap it does not write — a person
#: notices an empty slot but cannot notice a wrong answer.
_FUZZY_MIN = 0.85
_FUZZY_MARGIN = 0.10

#: The line that splits failures into "worth investigating" and "a question that never existed".
#:
#: At or above this value the file has a similar question that did not clear the threshold, so it is
#: worth investigating (warning). Below it no candidate was even close — cases like gate/approval
#: questions where the question file itself does not exist, which is normal (info).
#:
#: Measurements support this line: all 5 failures in ws-a-v5 were between 0.345 and 0.552, and all were
#: "questions not in the file". By contrast, the correct pair with corrupted Hangul was 0.9677.
_NEAR_MISS_MIN = 0.70


class _FileMatch:
    """Match result for one file. exact/fuzzy are held separately because they are used in
    file selection — a file with exact matches beats a file with only fuzzy matches."""

    __slots__ = ("path", "new_md", "exact", "fuzzy")

    def __init__(self, path: Path, new_md: str, exact: int, fuzzy: int) -> None:
        self.path, self.new_md, self.exact, self.fuzzy = path, new_md, exact, fuzzy

    @property
    def total(self) -> int:
        return self.exact + self.fuzzy

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def score(self) -> tuple[int, int, float]:
        """File selection order. Exact match count → total match count → most recent modification time.

        Putting exact matches first is load-bearing: if a previous stage's file has a similar
        sentence, a fuzzy match hits, and looking only at mtime, that file would beat the exactly
        matching file whenever it is more recent.
        """
        return (self.exact, self.total, self.mtime())


#: Best candidate of a round that failed to match. Diagnostics only — on 2026-08-16 the write-back
#: failed silently and the log was completely empty, delaying root-cause tracing. This number separates
#: "is the threshold too tight" from "are we looking at the wrong file".
class _Miss:
    __slots__ = ("ratio", "asked", "candidate")

    def __init__(self) -> None:
        self.ratio, self.asked, self.candidate = 0.0, "", ""

    def offer(self, ratio: float, asked: str, candidate: str) -> None:
        if ratio > self.ratio:
            self.ratio, self.asked, self.candidate = ratio, asked, candidate


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _assign(questions, wanted: dict[str, str],
            miss: _Miss) -> tuple[dict[int, str], int, int]:
    """{question number: value}, exact match count, fuzzy match count.

    All exact matches are settled **first**. If fuzzy matching ran first, a corrupted question
    would steal another question's place, and that question itself would be left empty.
    """
    # Each question has **two** strings to compare: the full body (text) and the last paragraph (ask).
    #
    # The file holds background prose + the question together, but only the question sentence goes to
    # AskUserQuestion (see models.Question.ask). Comparing only text collapses similarity due to the length
    # difference — ws-a-v5's design-context.md Q4 was lost at 0.3721. Use whichever of the two matches
    # better. With a single paragraph the two values are equal, so it costs nothing.
    norms = [(q.number, _norm(q.text), _norm(q.ask)) for q in questions]
    mapping: dict[int, str] = {}
    claimed: set[int] = set()

    remaining = dict(wanted)
    for number, norm, ask in norms:
        if number in claimed:
            continue
        value = remaining.pop(norm, None)
        if value is None and ask and ask != norm:
            value = remaining.pop(ask, None)
        if value is not None:
            mapping[number], _ = value, claimed.add(number)
    exact = len(mapping)
    if not remaining:
        return mapping, exact, 0

    # Collect candidates and assign in descending score order. Two questions may target one slot,
    # so instead of assigning immediately, the more confident one gets the slot first.
    candidates: list[tuple[float, str, int]] = []
    for asked, value in remaining.items():
        scored = sorted(((max(_ratio(asked, norm),
                              _ratio(asked, ask) if ask else 0.0), number)
                         for number, norm, ask in norms if number not in claimed),
                        reverse=True)
        if not scored:
            continue
        best_ratio, best_number = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else None
        miss.offer(best_ratio, asked,
                   next(n for num, n, _a in norms if num == best_number))
        if best_ratio < _FUZZY_MIN:
            continue
        if runner_up is not None and best_ratio - runner_up < _FUZZY_MARGIN:
            # No basis for deciding which one — do not write.
            continue
        candidates.append((best_ratio, asked, best_number))

    fuzzy = 0
    for best_ratio, asked, number in sorted(candidates, reverse=True):
        if number in claimed:
            continue
        mapping[number] = remaining[asked]
        claimed.add(number)
        fuzzy += 1
    return mapping, exact, fuzzy


def _wanted_by_text(sdk_questions: list[dict],
                    answers: dict[str, str]) -> dict[str, str]:
    """{normalized question text: answer value}.

    The keys of answers are 1-based indices within this round (the frontend QuestionForm sends
    `String(q.number)`, and question_file_from_sdk assigns those numbers).
    Skipping out-of-range keys is the same defense claude_driver applies when assembling
    sdk_answers — using 0 or a negative number directly as an index makes Python count
    from the end and **attach the answer to a different question.**

    The question text field has two names: the raw AskUserQuestion input uses `question`,
    and the payload normalized to the UI contract uses `text`
    (questions_payload._normalize_question — the shape the frontend reads and that is stored
    in answer records). The driver passes the raw form, but both are accepted so that the
    normalized shape coming in does not silently end up as empty slots.
    """
    out: dict[str, str] = {}
    for key, value in (answers or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if index < 1 or index > len(sdk_questions):
            continue
        raw = sdk_questions[index - 1]
        if not isinstance(raw, dict):
            continue
        text = _norm(raw.get("question") or raw.get("text"))
        if text and isinstance(value, str):
            out[text] = value
    return out


def _match_file(ws: Path, path: Path, wanted: dict[str, str],
                miss: _Miss) -> _FileMatch | None:
    """The result of planting answers in this file. None if there is nothing to plant (the best candidate stays in _miss).

    Reads without following links (`pathsafe`) — if the file were swapped for a link after walking
    and before reading, an outside file's contents would be carried into this question file and promoted to the source of truth.
    """
    try:
        md = read_text_beneath(ws, path.relative_to(ws).as_posix())
    except OSError:
        _log.warning("unreadable question file skipped: %s", path)
        return None
    if not looks_like_question_file(path.name, md):
        # Not a question file. Do not parse it (see that function and the _ANSWER_SLOT comment).
        return None
    qfile = parse_question_file(path.name, md)
    if not qfile.parse_ok:
        # parse_question_file swallows failures and returns parse_ok=False. There is no way to
        # assign question numbers in that file, so leave it alone.
        return None

    mapping, exact, fuzzy = _assign(qfile.questions, wanted, miss)
    if not mapping:
        return None
    try:
        new_md = serialize_answers(md, mapping)
    except (KeyError, ValueError):
        # serialize_answers raises KeyError for numbers not in the file. The mapping above was built
        # only from the file's questions, so the normal path cannot get here.
        _log.exception("answer serialization refused for %s", path)
        return None
    if new_md == md:
        # Questions were found but the file is unchanged = there is no `[Answer]:` line
        # (serialize_answers replaces only that line). Logged so as not to silently report
        # "recorded" — it is the agent that produced a file outside the convention.
        _log.warning("question file has no [Answer]: slot to fill: %s", path)
        return None
    return _FileMatch(path=path, new_md=new_md, exact=exact, fuzzy=fuzzy)
