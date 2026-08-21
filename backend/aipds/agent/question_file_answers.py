# backend/aipds/agent/question_file_answers.py -- writing submitted answers back into
# the `[Answer]:` slots of the question file.
#
# **Why it is needed.** The ai-plc workflow runs on the premise that the question file
# is the answer sheet: aws-aiplc-rule-details/common/question-format-guide.md instructs
# "Read the question file / Extract answers after [Answer]: tags", and
# common/session-continuity.md:31-33 says to **read** files such as
# `strategy-questions.md` when resuming a stage. AI-PDS delivered questions through
# AskUserQuestion and left these slots empty, so a resumed session could not recover
# from the file the decisions the user had already made.
#
# **Why matching is on question text rather than number.** AskUserQuestion has a hard
# schema limit of 4 questions x 4 options. A 10-question file splits into three rounds
# of 4+4+2 and each round's question numbers restart at 1, so matching by number would
# have round 2's answers overwrite questions 1-4 while 5-8 stay permanently blank -- the
# **wrong answers get recorded**, with no error. The model writes the same question
# sentence into both the file and the tool, so that sentence is the only stable key that
# survives a round boundary.
#
# **Why only one file is edited.** Question files accumulate per stage in the workspace,
# and a sentence such as "Would you like to proceed with these settings?" can be
# duplicated across several of them. Writing to every matching file would corrupt a past
# stage's record. One AskUserQuestion round belongs to one stage, so it belongs to one
# file -- the file with the most matches wins, and on a tie the most recently modified
# one does (the agent just wrote the current stage's file).
#
# **A failed match is left blank.** An answer planted on the wrong question makes the
# next stage that reads the file treat a decision the user never made as fact. A human
# can recognise a blank slot; a human cannot recognise a wrong answer.
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from aipds.parsers.questions import parse_question_file, serialize_answers

_log = logging.getLogger("aipds.agent")

#: The candidate files. **Chosen by content, not by name.**
#:
#: This used to be `*-questions.md`, and that lost `design-context.md`'s answers
#: wholesale (2026-08-16 keumkang-v5: 3 questions, 3 slots, 0 recorded). The reason a
#: name cannot be relied on is that **upstream does not keep to its own naming
#: convention** -- question-format-guide.md specifies `{phase-name}-questions.md`, while
#: prototype-validation.md names Step 2's artifact `design-context.md` and tells the
#: agent to use the question format inside it.
#:
#: That widening this is safe was verified by measurement (the 15 files under
#: keumkang-v5's aiplc-docs): the only addition is design-context.md, and 8 others such
#: as audit.md, discovery-document.md and prototype-spec.md are all filtered out by the
#: two gates below.
#:
#: Note: Workspace.list_question_files (the dashboard's question file list) still looks
#: at the name convention. The two sets diverging is deliberate -- answers are already
#: visible in the document panel (components/Markdown.tsx surfaces `[Answer]:`), so this
#: avoids dragging along a UI change unrelated to this defect.
_GLOB = "*.md"

#: The first gate. With no line-initial `[Answer]:` at all, it is not a question file.
#:
#: Filtering **before parsing** is the point. Running parse_question_file over every
#: document prints a warning plus a stack trace for each failure (that function's
#: fallback warning) and buries the log in noise -- which defeats the purpose of adding
#: diagnostic logging. A cheap string scan settles it.
#:
#: The `^` is essential: audit.md **quotes** the answer tag, as in
#: `**Recorded Answer Tag**: \`[Answer]: B\``. Not being line-initial, it does not match
#: (the second gate, parse_ok, would also catch it, but settling it here is cheaper).
_ANSWER_SLOT = re.compile(r"^\[Answer\]:", re.MULTILINE)
_DOCS_DIR = "aiplc-docs"

#: Documents that are **not** question files whatever their content. The one place
#: exclusion is by name.
#:
#: **Why exclude by name (measured 2026-08-18).** The `^` anchor in `_ANSWER_SLOT` above
#: only stops the case where audit.md **quotes** the tag
#: (`**Recorded Answer Tag**: \`[Answer]: B\``). But `core-workflow.md:303-304` requires
#: of audit.md: "**MANDATORY**: Log ALL user inputs ... Capture user's COMPLETE RAW
#: INPUT exactly as provided" -- a faithful record of an answer round can put
#: `[Answer]: A` **at the start of a line**, and then the anchor passes. That is what
#: happened: the tag wording the agent had written into the audit record was caught by
#: the question parser, and the agent then **corrupted its own record**, saying
#: "audit.md is not a question file, so I will remove that notation".
#:
#: In other words audit.md is by definition a verbatim copy of user input, so **it
#: cannot be classified by its content.** The name is the only signal left.
#: `aiplc-state.md` is listed for the same reason -- it is our state file, it may quote
#: answers, and it will never be a question file.
#:
#: This does not contradict declining to **include** by name convention: upstream has
#: broken its own convention before (it put questions in `design-context.md`), so
#: inclusion has to be judged by content. These two, conversely, are files whose
#: **purpose upstream specifies**, so their names can be trusted.
NEVER_QUESTION_FILES = frozenset({"audit.md", "aiplc-state.md"})


def looks_like_question_file(rel: str, markdown: str) -> bool:
    """Whether this document may be treated as a question round.

    **The hook and the write-back must use the same decision.** They diverged until
    2026-08-18 -- the write-back anchored on `^\\[Answer\\]:` while the hook
    (claude_driver) used the plain containment `"[Answer]:" in md`. A document that only
    quoted the tag therefore matched in the hook but not in the write-back. When the two
    gates differ, "what is a question file" has two answers.

    `rel` is a workspace-relative path. The name test uses the basename -- whether it is
    `aiplc-docs/audit.md` or an audit.md in a subdirectory, the purpose is the same.
    """
    if PurePosixPath(rel).name in NEVER_QUESTION_FILES:
        return False
    return bool(_ANSWER_SLOT.search(markdown))


def _norm(text: object) -> str:
    """The matching key. Collapses whitespace runs to one and ignores case.

    The parser joins a body's several lines with " " (parsers/questions.py:72), so a
    newline left on the tool side would make a raw comparison miss. It does no more than
    that -- flattening endings or punctuation as well could collide two different
    questions onto the same key, and this function's failure direction has to be "not
    found".
    """
    return " ".join(str(text or "").split()).casefold()


def record_answers(workspace: str, sdk_questions: list[dict],
                   answers: dict[str, str]) -> list[str]:
    """Record answers into the question file and return the relative paths actually changed.

    **No failure escapes as an exception.** The user has already submitted the answers
    and the turn has to resume -- killing the turn over a failure in an incidental record
    would lose those answers (the same discipline as
    claude_driver._save_answers_quietly).
    """
    try:
        return _record(workspace, sdk_questions, answers)
    except Exception:
        _log.exception("question-file answer write-back failed")
        return []


def _record(workspace: str, sdk_questions: list[dict],
            answers: dict[str, str]) -> list[str]:
    docs = Path(workspace) / _DOCS_DIR
    if not docs.is_dir():
        # On the first turn there are no artifacts yet. That is a normal state.
        return []

    wanted = _wanted_by_text(sdk_questions, answers)
    if not wanted:
        return []

    miss = _Miss()
    best: _FileMatch | None = None
    # rglob guarantees no order -- sorting is needed to make tie-breaking reproducible
    # (when even the mtimes match, path order decides).
    for path in sorted(docs.rglob(_GLOB)):
        found = _match_file(path, wanted, miss)
        if found is None:
            continue
        if best is None or found.score() > best.score():
            best = found

    if best is None:
        # **Do not fail silently.** On 2026-08-16 the write-back did not happen and the
        # log was completely empty, which made the cause take a long time to find.
        #
        # But the two kinds are **separated by level.** Three of keumkang-v5's five
        # failures were not defects -- they were gate/approval questions, for which no
        # question file exists in the first place and the record goes only into audit.md
        # (with nowhere to record, there is no blank slot either). Lumping those into the
        # same warning as a real defect buries it in noise. The best score separates
        # them: if some candidate came close it is worth investigating (warning); if all
        # were far off, the question was never in a file (info).
        detail = ("no match for %d answer(s); best %.3f asked=%r candidate=%r",
                  len(wanted), miss.ratio, miss.asked[:60], miss.candidate[:60])
        if miss.ratio >= _NEAR_MISS_MIN:
            _log.warning("question-file write-back: " + detail[0], *detail[1:])
        else:
            _log.info("question-file write-back: " + detail[0], *detail[1:])
        return []
    try:
        best.path.write_text(best.new_md, encoding="utf-8")
    except OSError:
        _log.exception("question file not writable: %s", best.path)
        return []
    rel = best.path.relative_to(Path(workspace)).as_posix()
    if best.fuzzy:
        # A fuzzy match happening means **this round's Hangul was mangled**
        # (claude-code#83033). It is the only signal of whether the literal-UTF-8
        # instruction is suppressing that, so it is logged at info -- without counting it
        # there is no way to measure the first layer's effect.
        _log.info("question-file write-back: %s (%d exact, %d fuzzy — corrupted "
                  "Hangul in this round, see claude-code#83033)",
                  rel, best.exact, best.fuzzy)
    else:
        _log.info("question-file write-back: %s (%d exact)", rel, best.exact)
    return [rel]


#: The floor for a fuzzy match, and the minimum gap to the runner-up.
#:
#: **Why fuzzy matching is needed (claude-code#83033).** When the model writes Korean in
#: a tool parameter as `\uXXXX` escapes and mistypes the hex, that code point decodes to
#: a "valid but wrong" syllable. The density is 3-5% of syllables and it switches on
#: intermittently per call, so within one turn the file (Write) can be clean while only
#: the question (AskUserQuestion) is mangled. Upstream is officially unresolved (handed
#: to the model team; not recoverable from the CLI) and the hex typo is random, so there
#: is no inverse transform either.
#:
#: **Why these numbers.** Measured over keumkang-v3's 6 rounds and 21 questions:
#:   correct pair 0.9677 / closest wrong pair 0.5806 / everything else <=0.375 /
#:   maximum within a single round 0.32-0.40.
#: The range between 0.97 and 0.58 is empty. Three mangled syllables in 60 characters
#: still scores about 0.95, and one in 20 characters also 0.95, so 0.85 has ample room
#: on both sides.
#:
#: **Why a gap is required too.** With a threshold alone, two similar questions can both
#: clear it, and the choice between them is then a coin flip. With no gap, nothing is
#: written -- a human can recognise a blank slot but cannot recognise a wrong answer.
_FUZZY_MIN = 0.85
_FUZZY_MARGIN = 0.10

#: The line dividing a failure into "worth investigating" and "a question that was
#: never in a file".
#:
#: At or above this value the file holds a similar question that did not clear the
#: threshold, so it is worth investigating (warning). Below it, no candidate came close
#: -- the case of a gate/approval question with no question file at all, which is normal
#: (info).
#:
#: Measurement backs this line: all five of keumkang-v5's failures scored 0.345-0.552 and
#: every one of them was a "question not in a file". Conversely the correct pair for
#: mangled Hangul scored 0.9677.
_NEAR_MISS_MIN = 0.70


class _FileMatch:
    """One file's match result. exact and fuzzy are held separately because they are used
    for choosing the file -- a file with an exact match beats one with only fuzzy
    matches."""

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
        """The file ordering: exact match count -> total match count -> last modified time.

        Putting the exact count first is load-bearing: if a past stage's file holds a
        similar sentence a fuzzy match catches it, and on mtime alone that file wins over
        the exactly matching one whenever it happens to be more recent.
        """
        return (self.exact, self.total, self.mtime())


#: The best candidate for a round that failed to match. Diagnostics only -- on
#: 2026-08-16 the write-back failed silently and the empty log delayed tracing the
#: cause. This number separates "the threshold is too tight" from "we are looking at the
#: wrong file".
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
    """{question number: value}, the exact match count, and the fuzzy match count.

    Every exact match is settled **first**. If fuzzy matching ran first, a mangled
    question would take another question's slot and that question would be left blank.
    """
    # There are **two** strings to compare per question: the whole body (text) and the
    # last paragraph (ask).
    #
    # The file holds background prose together with the question, while only the question
    # sentence goes to AskUserQuestion (see models.Question.ask). Comparing text alone
    # collapses the similarity through the length difference -- keumkang-v5's
    # design-context.md Q4 was lost at 0.3721. Whichever fits better is used. With a
    # single paragraph the two values are equal, so there is no cost either.
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

    # Candidates are collected and assigned in descending score order. Two questions can
    # target the same slot, so rather than assigning immediately, the slot goes to
    # whichever we are more confident about.
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
            # There is no basis for deciding which one -- nothing is written.
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
    """{normalised question text: answer value}.

    The keys of answers are 1-based indices within this round (the frontend QuestionForm
    sends `String(q.number)` and question_file_from_sdk assigns those numbers). Skipping
    an out-of-range key is the same defence claude_driver applies when assembling
    sdk_answers -- using a 0 or a negative number directly as an index makes Python count
    from the end and **attaches the answer to a different question.**

    The question text has two field names: the original AskUserQuestion input uses
    `question`, and the payload normalised to the UI contract uses `text`
    (questions_payload._normalize_question -- the shape the frontend reads and that is
    stored in the answer record). The driver passes the original, but both are accepted so
    that a normalised shape arriving does not quietly become a blank slot.
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


def _match_file(path: Path, wanted: dict[str, str],
                miss: _Miss) -> _FileMatch | None:
    """The result of planting answers in this file. None when there is nothing to plant
    (the best candidate is left in _miss).
    """
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        _log.warning("unreadable question file skipped: %s", path)
        return None
    if not looks_like_question_file(path.name, md):
        # Not a question file. Not parsed (see that function and the _ANSWER_SLOT
        # comment).
        return None
    qfile = parse_question_file(path.name, md)
    if not qfile.parse_ok:
        # parse_question_file swallows a failure and returns parse_ok=False. There is no
        # way to assign question numbers in such a file, so it is left alone.
        return None

    mapping, exact, fuzzy = _assign(qfile.questions, wanted, miss)
    if not mapping:
        return None
    try:
        new_md = serialize_answers(md, mapping)
    except (KeyError, ValueError):
        # serialize_answers raises KeyError for a number the file does not have. The
        # mapping above is built only from the file's own questions, so that cannot
        # arrive through the normal path.
        _log.exception("answer serialization refused for %s", path)
        return None
    if new_md == md:
        # Questions were found but the file is unchanged = there is no `[Answer]:` line
        # (serialize_answers replaces only that line). Logged so that we do not quietly
        # report "recorded" -- it was the agent that produced a file outside the
        # convention.
        _log.warning("question file has no [Answer]: slot to fill: %s", path)
        return None
    return _FileMatch(path=path, new_md=new_md, exact=exact, fuzzy=fuzzy)
