# backend/aipds/agent/questions_payload.py -- normalising the ask_questions payload.
#
# Why it is needed: on the markdown path (parsers/questions.py) our code decides
# is_other (letter == "X", or the text starting with "other"). ask_questions, by
# contrast, passes a model-authored dict straight through to the UI, so a payload that
# violates the prompt contract (QUESTIONS_SCHEMA_HINT) is rendered as-is. A measured
# incident: two options arrived with is_other (B and X), both rendered as
# "Other — 직접 입력", and because they shared one otherActive state each selection
# overwrote the other. Tightening the prompt is not a fix -- the model already had the
# contract and got it wrong, and a resend round-trip looks to the user like waiting at
# a blank screen.
#
# The policy: what can be fixed, the code corrects quietly (keeping the screen the user
# sees alive comes first), and only a question that does not hold up at all is refused
# with ValueError so the model builds it again.

# NOTE: the Korean in the comments below is intentional and must not be translated.
# It quotes strings the UI actually renders ("Other — 직접 입력") and text the model
# actually sent (measured), so it is the evidence for the judgement being recorded.
from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger("aipds.agent")

# The same order as the markdown parser (_LETTERS is separate from builder's A..J --
# a question's options are A..F + X per the schema).
_LETTERS = "ABCDEFGHIJ"
_OTHER_LETTER = "X"
_OTHER_TEXT = "Other — 직접 입력"


def _looks_like_other(letter: str, text: str) -> bool:
    """Uses the same rule as the markdown parser (parsers/questions.py) -- if the two
    paths diverge, the same question renders differently depending on the input
    format."""
    return letter == _OTHER_LETTER or text.strip().lower().startswith("other")


def _normalize_options(raw_options: list[Any], *, guess_other: bool = True) -> list[dict]:
    """Repair letters -> decide Other -> reduce to a single Other.

    Why only the last Other is kept: by convention Other goes at the end of a list, and
    one arriving earlier with is_other=True is the model mislabelling a substantive
    option (which is what B did in the measured incident). A demoted option gets its
    text restored.

    guess_other=False is for the SDK AskUserQuestion path (question_file_from_sdk):
    there the model supplies already-structured options, so there is no reason to guess
    an Other from prose (if a real option label starts with "other", as in "Other
    database", _looks_like_other misjudges it and erases the real option's text). The
    markdown/Discovery path (normalize_questions_payload's default) keeps using the
    heuristic -- because there the model builds the dict itself, and the measured
    incident happened on that path.
    """
    opts: list[dict] = []
    used: set[str] = set()
    for i, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        letter = str(raw.get("letter") or "").strip()
        # Repair a missing or duplicated letter: otherwise an empty badge appears, or
        # the frontend's key / radio value collide and selections overwrite each other.
        if not letter or letter in used:
            letter = next((c for c in _LETTERS if c not in used), None) or f"Z{i}"
        used.add(letter)
        # The model's is_other is taken as a hint only; the decision is remade with
        # the same rule as the markdown path -- which also catches the case of X sent
        # as False, where the free-text box disappears. With guess_other=False the
        # text/letter guessing is off and only the model's is_other is trusted.
        is_other = bool(raw.get("is_other")) or (
            guess_other and _looks_like_other(letter, text))
        opts.append({
            "letter": letter,
            "text": text,
            "is_other": is_other,
            "recommended": bool(raw.get("recommended")),
        })

    others = [i for i, o in enumerate(opts) if o["is_other"]]
    for i in others[:-1]:
        opts[i]["is_other"] = False
        # An option that arrived as is_other may have empty text (the UI supplies the
        # wording, so the model omits it). Demoting it would leave an empty,
        # unselectable option, so the label is filled in.
        if not opts[i]["text"] or (
                guess_other and _looks_like_other(opts[i]["letter"], opts[i]["text"])):
            opts[i]["text"] = f"보기 {opts[i]['letter']}"
    if others:
        opts[others[-1]]["text"] = opts[others[-1]]["text"] or _OTHER_TEXT
    return opts


def _normalize_question(raw: Any, number: int, *, guess_other: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"질문 {number}이 객체가 아니다: {type(raw).__name__}")
    options = _normalize_options(raw.get("options") or [], guess_other=guess_other)
    # A question whose only option is Other is not multiple choice -- for free text the
    # chat is enough, and shown as a form the user sees an empty card with nothing to
    # choose.
    if not [o for o in options if not o["is_other"]]:
        raise ValueError(
            f"질문 {number}에 고를 수 있는 보기가 없다 — Other 외에 최소 1개의 "
            "실질 보기가 필요하다")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError(f"질문 {number}의 text가 비어 있다")
    category = raw.get("category")
    return {
        # number is used as the radio name (q{number}) and as the answer dict's key.
        # The model's value is not trusted and the ordinals are reassigned -- a
        # duplicate would put two questions' radios in the same group, where each
        # clears the other.
        "number": number,
        "category": str(category).strip() if category else None,
        "text": text,
        "answer": None,
        "multi_select": bool(raw.get("multi_select")),
        "options": options,
    }


def normalize_questions_payload(payload: Any, *, guess_other: bool = True) -> dict:
    """Fit a model-authored questions_file to the frontend QuestionsPayload contract.

    Violations that can be fixed are corrected quietly; a question that does not hold
    up raises ValueError (the tool returns that message to the model so it builds the
    question again).

    guess_other: whether to enable the heuristic that treats text starting with "other"
    as an Other. The default True is for the markdown/Discovery path (ask_questions) --
    a policy that exists because a model-authored dict cannot be trusted as-is.
    question_file_from_sdk calls with False: the SDK already gives explicit options, so
    guessing from prose is unnecessary and instead misjudges a real option's label such
    as "Other database" and erases it. The is_other reduction (keeping only the last one
    as Other) still applies on both paths -- that is not what this switches off."""
    # The model does sometimes pass a JSON string instead of a dict (measured: "질문 폼
    # 전송 형식에 오류가 있어 다시 보내겠습니다"). If it parses, we accept it -- a
    # resend round-trip looks to the user like waiting at a blank screen.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"questions_file이 유효한 JSON이 아니다: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError(
            f"questions_file은 객체여야 한다 (받은 타입: {type(payload).__name__})")

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("questions_file.questions가 비어 있다 — 최소 1개의 질문이 필요하다")

    questions = [_normalize_question(q, i + 1, guess_other=guess_other)
                 for i, q in enumerate(raw_questions)]

    preamble = payload.get("preamble")
    return {
        "name": str(payload.get("name") or "questions").strip() or "questions",
        "preamble": str(preamble).strip() if preamble else None,
        # The frontend contract: it renders as a form rather than a
        # RawMarkdownFallback only with parse_ok=True and raw_markdown=None.
        "parse_ok": True,
        "raw_markdown": None,
        "questions": questions,
    }


# Move the SDK AskUserQuestion input into the frontend's QuestionFile shape. The letter
# indexes the SDK option order directly -- that index is the key when translating an
# answer back into an SDK label (_answer_to_sdk), so an order that slips means a
# different option was chosen.
#
# Moved here from builder._to_question_file. With both paths converging on one function,
# the is_other duplicate repair (normalize_questions_payload) applies to prototype
# builds too.
def normalize_sdk_questions(raw: object) -> list[dict]:
    """Normalise AskUserQuestion's `questions` argument into a list[dict].

    The model does sometimes pass this argument as a **serialised JSON string**
    (measured: 3 of 18 rounds in one session). Without stopping it here,
    question_file_from_sdk iterates the string character by character and blows up with
    AttributeError -- and that exception escapes the permission callback and kills the
    turn.

    ⚠️ **The 3 observed cases are not rescued by this function.** The CLI rejected them
    by schema validation **before** calling our callback (the evidence: the tool_use and
    the `InputValidationError` tool_result are paired immediately in the same transcript
    file, and the backend log has not one related warning in that window). The model
    reads that error and retries with a correct array on the next turn, so no question is
    lost to the user. This function is a defence for the path where the same shape does
    reach the callback (an SDK/CLI version difference).

    Anything that is not a list, or that fails to parse, becomes an empty list -- the
    caller then takes the same path as an option-less payload (ValueError -> refusal
    reason returned to the model).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [q for q in raw if isinstance(q, dict)]


def question_file_from_sdk(sdk_questions: list[dict], *, name: str) -> dict:
    questions = []
    for i, q in enumerate(sdk_questions, start=1):
        raw_options = q.get("options") or []
        options = []
        for j, o in enumerate(raw_options):
            label = str(o.get("label") or "")
            desc = str(o.get("description") or "")
            text = f"{label} — {desc}".rstrip(" —") if desc else label
            options.append({
                "letter": _LETTERS[j] if j < len(_LETTERS) else f"Z{j}",
                "text": text, "is_other": False, "recommended": False,
            })
        # Append the free-text option. AskUserQuestion has no field corresponding to
        # is_other and this path normalises with guess_other=False, so without this
        # there is no route by which an Other can ever appear -- the user loses any way
        # to ask for something outside the options the model offered (measured: after
        # "rebuild", a question about what to do next had no matching item and nothing
        # could be requested at all).
        #
        # The letter is X. Why it must not be slotted into the A/B/C run is in the
        # contract: builder._answer_to_sdk translates an answer back into an SDK label
        # with `sdk_options[_LETTERS.find(letter)]`, so a real option's letter shifted
        # by even one position translates every answer into the wrong option. X sits
        # outside _LETTERS (A-J) and so takes no part in that index arithmetic, and a
        # value that matches nothing passes through as free text (builder.py's
        # `return value  # free text (Other)`).
        #
        # One is appended even when the model already included an "Other" label. Not
        # appending was tried first (detecting it with _looks_like_other) and was worse:
        # this path normalises with guess_other=False, so the is_other of the option the
        # model added stays False, and the result was a state with **no free-text box at
        # all** (measured). Detecting and skipping simply reproduces the problem this is
        # here to fix.
        #
        # One seemingly duplicate option is accepted as a cost -- the model's "Other"
        # renders as an ordinary radio item and X as the free-text box, so nothing the
        # user can do is taken away. Normalisation's is_other reduction (demoting the
        # earlier one) also does not fire, since X is the only is_other.
        if options:
            options.append({"letter": _OTHER_LETTER, "text": _OTHER_TEXT,
                            "is_other": True, "recommended": False})
        questions.append({
            "number": i,
            "category": q.get("header") or None,
            "text": str(q.get("question") or ""),
            "answer": None,
            "multi_select": bool(q.get("multiSelect")),
            "options": options,
        })
    # Normalisation enforces the final contract -- a question with no options raises
    # ValueError here. guess_other=False: SDK options are already structured, so an
    # Other is not guessed from the text (which keeps a real option label such as
    # "Other database" from being misread). The is_other duplicate reduction still
    # applies.
    return normalize_questions_payload(
        {"name": name, "preamble": None, "questions": questions},
        guess_other=False)
