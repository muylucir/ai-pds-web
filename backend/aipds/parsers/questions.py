# backend/aipds/parsers/questions.py
from __future__ import annotations
import logging
import re
from aipds.models import Question, QuestionOption, QuestionFile

logger = logging.getLogger(__name__)

#: Question header. **A suffix is allowed** after the number — the upstream format only
#: specifies `## Question [Number]` and does not forbid what follows, and the agent attaches
#: descriptions to follow-up questions
#: (measured: `## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)`).
#:
#: Previously `(\d+)\s*$` required the end of line, so such questions were **not seen at all**.
#: Then the answer is not recorded (question_file_answers does not know that number), and the
#: question count, answeredCount, and progress on screen drift with it — this was the defect
#: in ws-a-v5 on 2026-08-16.
#:
#: `\b` is the boundary: a number is required to be a question, so `## Questions 개요` and
#: `## Questionnaire` do not match. Swallowing those too would mash a category header into a
#: single question.
#:
#: **One modifier word and four hashes are allowed.** Upstream does not write question
#: headings in a single form — `question-format-guide.md` carries both `## Question [Number]`
#: (line 22) and `### Clarification Question 1` (line 223, "Creating Clarification Questions")
#: as templates, and the ruleset also has `#### Question 1: Brand & Design Context`.
#: 2026-08-17 test-wf: `pain-point-clarification-questions.md` used that second form and was
#: read as **0 questions**, and that file's answers were not recorded.
#:
#: **Limiting the modifier to one word is the key.** Allowing an arbitrary prefix would catch
#: even reference prose headings like `## Answer to Question 3` as questions, mashing that
#: whole section into one question — the same failure the `\b` boundary above prevents. The
#: number is still the sole discriminator, so `### Question File Format`,
#: `### Context Questions (Per Use Case)`,
#: `### ⛔ GATE: Await PRFAQ Clarifying Question Answers` still do not match.
#:
#: `serialize_answers` uses the same regex (see that function's comment) — recognizing the
#: header is what writes the answer back, so if the two paths diverge you get a state where
#: "it parses but the answers are not written".
#:
#: **Localized headings are read too (`## 질문 1`).** 2026-08-17 ws-j: a perfectly normal
#: question file was read as 0 questions and no card appeared; the only difference was the
#: heading word. The cause was a convention change — once the question file became a
#: user-facing artifact rather than a copy of the tool call, the agent translated even the
#: headings into the project language. Now that AskUserQuestion is rejected, that question
#: **disappears entirely.**
#:
#: The tolerance lives here, and the instruction to write `## Question N` lives in
#: `discovery-config/CLAUDE.md` — upstream `question-format-guide.md` is the source of truth
#: for that format and is not touched.
#:
#: **It is an allowlist, not a general rule.** Loosening it to something like "headings ending
#: in a number" would swallow real category headings — `## 모호성 1` in clarification question
#: files is one, and treating it as a question would absorb the real questions below it into
#: the section. The project languages are only ko/en (`_LANGUAGES` in models and prompts), so
#: a list is enough.
_QUESTION_WORDS = ("Question", "질문")
_Q_HEADER = re.compile(
    r"^#{2,4}\s+(?:\S+\s+)??(?:" + "|".join(_QUESTION_WORDS) + r")\s+(\d+)\b",
    re.MULTILINE)
#: Multi-select marker. **The upstream format has no such concept** —
#: `question-format-guide.md` only specifies single selection as `[Answer]: C` and defines no
#: notation for choosing several.
#:
#: **Why it is needed (measured 2026-08-21).** The question body said "(복수 선택 가능)"
#: (multiple selections allowed), but the screen showed a "하나만 선택" (select one) badge and
#: radio buttons, and the user worked around it by writing "A, B" in the `Other — 직접 입력`
#: field — the structured answer was demoted to free text, and the next stage cannot read
#: that value as option letters.
#:
#: The cause was that there was **no** path for `multi_select` to travel from a file question
#: to the UI. In the AskUserQuestion era it arrived structured as a tool argument
#: (`multiSelect` in agent/questions_payload.py), but when questions moved to files only that
#: value was left behind. The frontend is already prepared — QuestionCard draws checkboxes
#: from this flag and joins the letters with commas.
#:
#: This is **the same division of labor** as the heading tolerance above: the tolerance lives
#: here, the instruction to write it that way lives in `discovery-config/CLAUDE.md`, and
#: upstream is not touched.
#:
#: **Parentheses are required.** Questions that ask about multi-select itself really exist
#: ("복수 선택 UI가 필요합니까?" — do you need a multi-select UI?), so looking only at the
#: words would render them as checkboxes. The form the agent actually writes is parenthesized
#: (measured), so the parentheses are the boundary between a marker and a topic — like the
#: other judgments in this file, it is an allowlist, not a general rule.
#:
#: **Korean phrasings are accepted broadly.** `discovery-config/CLAUDE.md` must be
#: language-neutral (all projects share it — Korean prose itself is a language signal), so
#: Korean expressions cannot be hard-coded into the instruction. The instruction only goes as
#: far as "a parenthetical note meaning 'select all that apply' in the project language", and
#: the model picks the actual wording, so that breadth is absorbed here — the UI's own badge
#: being "여러 개 선택 가능" is itself evidence of that breadth (i18n `q.multiSelectBadge`).
_PAREN = re.compile(r"[(（]([^)）]{0,120})[)）]")
#: Multi-select if the parenthetical is one of these. `all` is not accepted on its own —
#: unrelated parentheses like "(all prices in KRW)" would match.
_MULTI_EN = re.compile(
    r"(?:select|choose|check|pick|mark)\s+all\b|all\s+that\s+apply"
    r"|multiple\s+(?:selection|selections|answers|choices)", re.IGNORECASE)
#: For Korean, look for "선택" (select) appearing together with a plural qualifier.
_MULTI_KO_QUALIFIER = re.compile(r"복수|중복|여러|모두")
#: Phrases that **emphasize** single selection. Needed because "여러 개 중 하나만 선택"
#: (select only one of several) matches the qualifier above — turning it into checkboxes would
#: let several be chosen on a question where only one must be chosen, and the next stage reads
#: that answer as a single value.
_SINGLE_ONLY = re.compile(
    r"하나만|한\s*개만|1\s*개만|only\s+one|pick\s+one|choose\s+one|select\s+one",
    re.IGNORECASE)
_CAT_HEADER = re.compile(r"^##\s+(?!Question\b)(.+?)\s*$", re.MULTILINE)
#: Option letters are accepted up to A-J. The upstream format guide says up to F, but the model
#: actually writes `G)` (measured: two question files in one project). While only up to F was
#: accepted, that line was caught neither as an option nor as body text and was discarded, so
#: the card appeared with one option missing. The accepted range matches the SDK-side
#: translation table (`claude_driver._LETTERS`, A-J).
_OPTION = re.compile(r"^([A-J]|X)\)\s+(.*)$")
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$")
_RECO = re.compile(r"\s*←\s*(추천|recommended).*$", re.IGNORECASE)
#: Translated free-input option. The upstream template is
#: `X) Other (please describe after [Answer]: tag below)`, but agents in ko projects translate
#: the label and attach the letter in sequence too — measured (ws-h): all 61 "other" options in
#: the question files had the shape `D) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)`, and being
#: read as ordinary options they left no field to write in under multi-select (under single
#: select an elaboration field opens, so it looked normal).
#:
#: Only matched when "기타" (Other) stands as a word — so real options like "기타 행정 업무"
#: (other administrative work) are not changed. The `[Answer]` hint is not used as a signal:
#: measured (753 drill options), most options with that hint were options like "B) Iterate — …
#: ([Answer]: 뒤에 무엇을 바꿀지 적어 주세요)", where **choosing is the decision and an
#: elaboration is attached**. Turning them into free input would leave only the elaboration
#: from "B: 부연" (B: elaboration), losing what was chosen.
_OTHER_KO = re.compile(r"^기타\s*(?:$|[(（:：—–-])")


def looks_like_other(letter: str, text: str) -> bool:
    """Whether this is a free-input (Other) option. The ask_questions payload normalization uses
    this rule too — if the two paths diverged, the same question would render differently
    depending on the input format."""
    t = text.strip()
    return letter == "X" or t.lower().startswith("other") or bool(_OTHER_KO.match(t))


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
    # Top-level prose after a category header and before a question header. It becomes the
    # next question's `context`. It is discarded when the category changes — that prose belongs
    # to the previous category, so attaching it to a question in another category would add
    # an unrelated description.
    #
    # The inside of a question block is entirely consumed up to the next header by the inner
    # loop below, so top-level prose only occurs right after a category header. Therefore this
    # buffer stays empty to the end in most files, and existing parse results do not change.
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
            # Inside a block, join with `\n` — unlike `text`/`ask`, which join with `" "`.
            # Those are used for similarity comparison, but context is **rendered as markdown**,
            # so the line structure carries meaning. Measured: the premise of a confirmation gate
            # question is a 5-row table, and joining with spaces turns it into
            # `| # | … | |---|---| | 1 | …`, which is no longer a table.
            context = "\n\n".join("\n".join(b) for b in context_blocks if b).strip()
            context_blocks = [[]]
            # Gather by paragraph. The last paragraph is the actual question sentence (see
            # models.Question.ask) and what precedes it is meta/background. A blank line is a
            # paragraph boundary.
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
                        is_other=looks_like_other(letter, otext),
                        recommended=recommended,
                    ))
                elif raw and not options:
                    text_blocks[-1].append(raw)
                elif not raw and text_blocks[-1] and not options:
                    # Blank line = paragraph boundary. Blank lines after options have started
                    # are ignored.
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
                # The marker is read only from the question body. Looking at `context` (the
                # top-level prose before the question) too would make a single line like "the
                # next two questions are multi-select" apply to every following question,
                # blurring the scope — it is clearer for each question to carry its own
                # marker.
                multi_select=_is_multi_select(text),
            ))
            continue
        if not seen_first_header and line.strip():
            preamble_lines.append(line.rstrip())
        elif seen_first_header:
            # Top-level prose after the first header — gather it as the next question's context. A
            # blank line is a paragraph boundary (same rule as the question body).
            #
            # Only `rstrip`: indentation carries markdown meaning (nested lists, code blocks).
            # preamble_lines uses rstrip for the same reason.
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
