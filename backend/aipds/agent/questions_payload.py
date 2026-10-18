# backend/aipds/agent/questions_payload.py — ask_questions payload normalization.
#
# Why this exists: on the markdown path (parsers/questions.py), code decides is_other
# (parsers.questions.looks_like_other). ask_questions, by contrast, passes the
# model-built dict straight to the UI, so a payload that breaks the prompt contract
# (QUESTIONS_SCHEMA_HINT) is rendered as-is. Observed incident: two is_other options
# (B and X) arrived, both rendered as "Other — 직접 입력", and because they shared
# the same otherActive state, each selection overwrote the other. Tightening the
# prompt is not a root fix — the model already had the contract and still got it
# wrong, and a resend round trip looks like an empty wait to the user.
#
# Policy: whatever can be fixed, code fixes silently (keeping the user-visible
# screen working comes first); only when the question itself is invalid do we
# reject it with ValueError so the model rebuilds it.
from __future__ import annotations

import json
import logging
from typing import Any

from aipds.parsers.questions import looks_like_other

_log = logging.getLogger("aipds.agent")

# Same order as the markdown parser (_LETTERS is separate from builder's A..J —
# question options are A..F + X per the schema).
_LETTERS = "ABCDEFGHIJ"
_OTHER_LETTER = "X"
_OTHER_TEXT = "Other — 직접 입력"


def _looks_like_other(letter: str, text: str) -> bool:
    """Uses the same rule as the markdown parser (parsers/questions.py) — if the
    two paths diverge, the same question renders differently depending on input format."""
    return looks_like_other(letter, text)


def _normalize_options(raw_options: list[Any], *, guess_other: bool = True) -> list[dict]:
    """Fix letters → detect Other → collapse to a single Other.

    Why only the last Other is kept: by convention Other comes at the end of the
    list, and an earlier option arriving with is_other=True means the model
    mislabeled a real choice (B in the observed incident was exactly that).
    Demoted options get their text restored.

    guess_other=False is for the SDK AskUserQuestion path (question_file_from_sdk)
    — on that path the model already provides structured options, so there is no
    reason to guess Other from prose (if a real option label starts with "other",
    as in "Other database", _looks_like_other misjudges it and erases the real
    option's text). The markdown/Discovery path (the default of
    normalize_questions_payload) keeps using the heuristic — the model builds the
    dict directly there, and that is where the observed incident happened.
    """
    opts: list[dict] = []
    used: set[str] = set()
    for i, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        letter = str(raw.get("letter") or "").strip()
        # Fix missing/duplicate letters: otherwise an empty badge shows, or the
        # frontend key/radio value collides and selections overwrite each other.
        if not letter or letter in used:
            letter = next((c for c in _LETTERS if c not in used), None) or f"Z{i}"
        used.add(letter)
        # The model's is_other is only a hint; we re-decide with the same rule
        # as the markdown path — this also catches the case where X was sent as
        # False and the free-input box disappeared. With guess_other=False,
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
        # An option sent as is_other may have empty text (the model omits it because
        # the UI fills in the wording). Once demoted, it would be an empty, unselectable choice, so fill in a label.
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
    # A question with only Other is not multiple choice — free input is fine in chat,
    # and showing it as a form gives the user an empty card with no choices.
    if not [o for o in options if not o["is_other"]]:
        raise ValueError(
            f"질문 {number}에 고를 수 있는 보기가 없다 — Other 외에 최소 1개의 "
            "실질 보기가 필요하다")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError(f"질문 {number}의 text가 비어 있다")
    category = raw.get("category")
    return {
        # number is used as the radio name (q{number}) and the answer dict key. We
        # don't trust the model's value and renumber sequentially — duplicates would
        # put two questions' radios in the same group and they would deselect each other.
        "number": number,
        "category": str(category).strip() if category else None,
        "text": text,
        "answer": None,
        "multi_select": bool(raw.get("multi_select")),
        "options": options,
    }


def normalize_questions_payload(payload: Any, *, guess_other: bool = True) -> dict:
    """Conform a model-built questions_file to the frontend QuestionsPayload contract.

    Fixable violations are corrected silently; if a question is invalid, raises
    ValueError (the tool returns that message to the model so it rebuilds it).

    guess_other: whether to enable the heuristic that treats text starting with
    "other" as Other. The default True is for the markdown/Discovery path
    (ask_questions) — a policy born from not being able to trust the model-built
    dict as-is. question_file_from_sdk calls with False: the SDK already provides
    explicit options, so prose guessing is unnecessary, and worse, it misjudges and
    erases a real option label like "Other database". The is_other duplicate
    collapse (keeping only the last one as Other) still applies on both paths —
    this flag does not turn that off."""
    # The model really does sometimes pass a JSON string instead of a dict (observed: "질문 폼
    # 전송 형식에 오류가 있어 다시 보내겠습니다"). If it parses, accept it — a resend
    # round trip looks like an empty wait to the user.
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
        # Frontend contract: parse_ok=True + raw_markdown=None is required to render
        # as a form rather than RawMarkdownFallback.
        "parse_ok": True,
        "raw_markdown": None,
        "questions": questions,
    }


# Moves SDK AskUserQuestion input into the frontend QuestionFile shape. letter indexes
# the SDK option order as-is — when answers are translated back to SDK labels
# (_answer_to_sdk), that index is the key, so a misaligned order means a different choice was picked.
#
# Moved here from builder._to_question_file. With both paths converging on one function,
# the is_other duplicate fix (normalize_questions_payload) also applies to prototype builds.
def normalize_sdk_questions(raw: object) -> list[dict]:
    """Normalize AskUserQuestion's `questions` argument to list[dict].

    The model sometimes passes this argument as a **serialized JSON string**
    (observed: 3 of 18 rounds in one session). If not stopped here,
    question_file_from_sdk walks the string character by character and blows up
    with AttributeError — that exception leaks out of the permission callback and
    kills the turn.

    ⚠️ **The 3 observed cases are not rescued by this function.** The CLI rejected
    them with schema validation **before** calling our callback (evidence: the
    tool_use and the `InputValidationError` tool_result are paired immediately in
    the same transcript file, and the backend log has no related warning at all in
    that time window). The model reads that error and retries with a proper array
    on the next turn, so no question is lost for the user. This function defends
    the path where the same shape does reach the callback (SDK/CLI version differences).

    If it is not a list or parsing fails, returns an empty list — the caller takes
    the same path as a payload without options (ValueError → rejection reason returned to the model).
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
        # Append a free-input choice. AskUserQuestion has no field equivalent to
        # is_other, and this path normalizes with guess_other=False, so without
        # this there is no way at all for an Other to appear — the user would have
        # no way to ask for anything outside the choices the model offered (observed:
        # after "다시 빌드", a question about what to do next lacked the wanted item, so nothing could be instructed).
        #
        # The letter is X. Why it must not be slotted into the A/B/C sequence lies in
        # the contract — builder._answer_to_sdk translates answers back to SDK labels
        # via `sdk_options[_LETTERS.find(letter)]`, so if a real option's letter
        # shifts by even one, every answer maps to the wrong option. X is outside
        # _LETTERS (A-J), so it plays no part in that index calculation, and an
        # unmatched value passes through as free text (builder.py's
        # `return value  # free text (Other)`).
        #
        # Even if the model already included an "Other" label, append one more anyway.
        # Not appending was tried first (detecting via _looks_like_other) and was worse:
        # this path normalizes with guess_other=False, so the model's option keeps
        # is_other False, and the result was **no free-input box at all** (observed).
        # In other words, detecting and skipping reproduces the very problem being fixed.
        #
        # We accept one seemingly duplicate option — the model's "Other" renders as
        # a plain radio item and X as the free-input box, so nothing the user can do
        # is lost. The normalization's is_other collapse (demoting earlier ones) also
        # does not fire, since X is the only is_other.
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
    # Normalization enforces the final contract — a question without options raises ValueError here.
    # guess_other=False: SDK options are already structured, so we don't guess Other
    # from text (this prevents real option labels like "Other database" from being
    # misjudged). The is_other duplicate collapse still applies.
    return normalize_questions_payload(
        {"name": name, "preamble": None, "questions": questions},
        guess_other=False)
