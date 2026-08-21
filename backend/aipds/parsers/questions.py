# backend/aipds/parsers/questions.py
#
# NOTE: the Korean in this file is intentional and must not be translated. It is what
# the parser **matches** (`## 질문 N` headings, the multi-select qualifiers
# `복수|중복|여러|모두`, the single-select emphasis `하나만`) plus the measured heading
# and marker examples that justify each of those patterns. Translating any of it stops
# the parser from reading a Korean project's question files -- the sarang-hpt failure,
# where a perfectly valid file was read as zero questions and the card never appeared.
from __future__ import annotations
import logging
import re
from aipds.models import Question, QuestionOption, QuestionFile

logger = logging.getLogger(__name__)

#: The question header. A **suffix after the number is allowed** -- the upstream format
#: specifies only `## Question [Number]` and does not forbid anything following it, and
#: the agent attaches an explanation to follow-up questions (measured:
#: `## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)`).
#:
#: This used to require end-of-line with `(\d+)\s*$`, which meant such a question was
#: **not seen at all**. Then the answer is never recorded (question_file_answers does
#: not know that number) and the on-screen question count, answeredCount and progress
#: all go out of step -- the 2026-08-16 keumkang-v5 defect.
#:
#: `\b` is the boundary: a question needs a number, so `## Questions 개요` and
#: `## Questionnaire` do not match. Swallowing those too would mash a category header
#: into a single question.
#:
#: **One qualifier word and up to four hashes are allowed.** Upstream does not write the
#: question heading in one single form -- `question-format-guide.md` carries both
#: `## Question [Number]` (line 22) and `### Clarification Question 1` (line 223,
#: "Creating Clarification Questions") as templates, and the ruleset also has
#: `#### Question 1: Brand & Design Context`. 2026-08-17 test-wf:
#: `pain-point-clarification-questions.md` used that second form and was read as
#: **zero questions**, and that file's answers were never recorded.
#:
#: **Limiting the qualifier to one word is the crux.** Allowing an arbitrary prefix
#: would catch a prose cross-reference heading such as `## Answer to Question 3` as a
#: question and mash that whole section into one -- the same failure the `\b` boundary
#: above prevents. The number is still the only discriminator, so
#: `### Question File Format`, `### Context Questions (Per Use Case)` and
#: `### ⛔ GATE: Await PRFAQ Clarifying Question Answers` still do not match.
#:
#: `serialize_answers` uses this same regex (see that function's comment) -- recognising
#: the header *is* writing the answer back, so if the two paths diverge the result is a
#: state where "parsing works but answers are not written".
#:
#: **Localised headings are read too (`## 질문 1`).** 2026-08-17 sarang-hpt: a perfectly
#: valid question file was read as zero questions and no card appeared, and the only
#: difference was the heading word. The cause was a change of convention -- once the
#: question file became a user-facing artifact rather than a copy of a tool call, the
#: agent translated the heading into the project language too. With AskUserQuestion
#: denied, that question now **disappears entirely.**
#:
#: The leniency lives here and the instruction to write `## Question N` lives in
#: `discovery-config/CLAUDE.md` -- upstream `question-format-guide.md` is the canonical
#: source for that format and is not touched.
#:
#: **It is an allowlist, not a general rule.** Loosening it to something like "a heading
#: ending in a number" swallows category headings that really exist -- `## 모호성 1` in a
#: clarification question file is one, and taking that as a question absorbs the real
#: questions below it into its section. There are only two project languages, ko and en
#: (`_LANGUAGES` in models and prompts), so a list is enough.
_QUESTION_WORDS = ("Question", "질문")
_Q_HEADER = re.compile(
    r"^#{2,4}\s+(?:\S+\s+)??(?:" + "|".join(_QUESTION_WORDS) + r")\s+(\d+)\b",
    re.MULTILINE)
#: The multi-select marker. **The upstream format has no such concept** --
#: `question-format-guide.md` specifies only single selection as `[Answer]: C` and
#: defines no notation for choosing several.
#:
#: **Why it is needed (measured 2026-08-21).** A question's body said
#: "(복수 선택 가능)" while the screen showed a "하나만 선택" badge and radio buttons,
#: and the user worked around it by typing "A, B" into the `Other — 직접 입력` box --
#: a structured answer demoted to free text, and a value the next stage cannot read as
#: option letters.
#:
#: The cause was that there was **no path** by which `multi_select` reached the UI from
#: a file question. In the AskUserQuestion era it arrived structured as a tool argument
#: (`multiSelect` in agent/questions_payload.py), and when questions moved into files
#: that one value was left behind. The frontend is already ready -- QuestionCard draws
#: checkboxes from this flag and joins the letters with commas.
#:
#: **The same division of labour** as the heading leniency above: the leniency here, the
#: instruction to write it that way in `discovery-config/CLAUDE.md`, and upstream
#: untouched.
#:
#: **Parentheses are required.** Questions that ask *about* multi-select really exist
#: ("복수 선택 UI가 필요합니까?"), so looking at the word alone renders that one as
#: checkboxes. The form the agent actually writes is parenthesised (measured), so the
#: parentheses are the boundary between a marker and a topic -- an allowlist, like the
#: other judgements in this file, not a general rule.
#:
#: **Korean wording is accepted broadly.** `discovery-config/CLAUDE.md` has to be
#: language-neutral (every project shares it -- Korean prose is itself a language
#: signal), so a Korean expression cannot be pinned into the instruction. The
#: instruction goes only as far as "a parenthesised note meaning 'select all' in the
#: project language" and the model picks the actual wording, so the breadth is absorbed
#: here -- the UI's own badge reading "여러 개 선택 가능" is the first piece of evidence
#: for that breadth (i18n `q.multiSelectBadge`).
_PAREN = re.compile(r"[(（]([^)）]{0,120})[)）]")
#: Any of these inside the parentheses means multi-select. A bare `all` is not accepted
#: -- an unrelated parenthetical such as "(all prices in KRW)" would match.
_MULTI_EN = re.compile(
    r"(?:select|choose|check|pick|mark)\s+all\b|all\s+that\s+apply"
    r"|multiple\s+(?:selection|selections|answers|choices)", re.IGNORECASE)
#: For Korean the test is "선택" co-occurring with a plurality qualifier.
_MULTI_KO_QUALIFIER = re.compile(r"복수|중복|여러|모두")
#: Wording that **emphasises** single selection. It is needed because
#: "여러 개 중 하나만 선택" matches the qualifier above -- turning that into checkboxes
#: would let several be chosen on a question where exactly one must be, and the next
#: stage reads that answer as a single value.
_SINGLE_ONLY = re.compile(
    r"하나만|한\s*개만|1\s*개만|only\s+one|pick\s+one|choose\s+one|select\s+one",
    re.IGNORECASE)
_CAT_HEADER = re.compile(r"^##\s+(?!Question\b)(.+?)\s*$", re.MULTILINE)
_OPTION = re.compile(r"^([A-F]|X)\)\s+(.*)$")
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$")
_RECO = re.compile(r"\s*←\s*(추천|recommended).*$", re.IGNORECASE)


def _is_multi_select(text: str) -> bool:
    for inner in _PAREN.findall(text):
        if _SINGLE_ONLY.search(inner):
            continue
        if _MULTI_EN.search(inner):
            return True
        if "선택" in inner and _MULTI_KO_QUALIFIER.search(inner):
            return True
    return False

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
    # Top-level prose after a category header and before a question header. It becomes
    # the next question's `context`. It is discarded when the category changes -- that
    # prose belongs to the previous category, so attaching it to a question in another
    # category would give it the wrong explanation.
    #
    # **Inside** a question block the inner loop below consumes everything up to the
    # next header, so top-level prose can only occur right after a category header. That
    # is why this buffer stays empty to the end in most files and existing parse results
    # do not change.
    context_blocks: list[list[str]] = [[]]

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
            context_blocks = [[]]
            i += 1
            continue
        if qm:
            seen_first_header = True
            number = int(qm.group(1))
            i += 1
            # Inside a block the join is `\n`, unlike `text`/`ask` which join with
            # `" "`. Those are used for similarity comparison, whereas context is
            # **rendered as markdown**, so line structure carries meaning. Measured: an
            # approval-gate question's premise is a 5-line table, and joining with
            # spaces turns it into `| # | … | |---|---| | 1 | …`, which is no longer a
            # table.
            context = "\n\n".join("\n".join(b) for b in context_blocks if b).strip()
            context_blocks = [[]]
            # Collected by paragraph. The last paragraph is the actual question
            # sentence (see models.Question.ask) and what precedes it is metadata and
            # background. A blank line is the paragraph boundary.
            text_blocks: list[list[str]] = [[]]
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
                    text_blocks[-1].append(raw)
                elif not raw and text_blocks[-1] and not options:
                    # Blank line = paragraph boundary. Blank lines after the options
                    # have started are ignored.
                    text_blocks.append([])
                i += 1
            blocks = [b for b in text_blocks if b]
            text = " ".join(l for b in blocks for l in b).strip()
            questions.append(Question(
                number=number, category=current_category,
                text=text,
                ask=" ".join(blocks[-1]).strip() if blocks else "",
                context=context,
                options=options, answer=answer,
                # The marker is read from the question body only. Looking at `context`
                # (the top-level prose before the question) as well would let one line
                # such as "다음 두 문항은 복수 선택입니다" apply to every question that
                # follows, leaving the scope unclear -- each question carrying its own
                # marker is unambiguous.
                multi_select=_is_multi_select(text),
            ))
            continue
        if not seen_first_header and line.strip():
            preamble_lines.append(line.rstrip())
        elif seen_first_header:
            # Top-level prose after the first header -- collected as the next
            # question's context. A blank line is the paragraph boundary (the same rule
            # as the question body).
            #
            # Only `rstrip`: indentation is markdown's meaning (nested lists, code
            # blocks). preamble_lines uses rstrip for the same reason.
            kept = line.rstrip()
            if kept.strip() and kept.strip() != "---":
                context_blocks[-1].append(kept)
            elif not kept.strip() and context_blocks[-1]:
                context_blocks.append([])
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
