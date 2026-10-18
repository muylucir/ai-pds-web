# Normalization contract for model-generated payloads that arrive through ask_questions.
#
# Why code corrects it: on the markdown path (parsers/questions.py), code decides
# is_other (letter == "X" or the text starts with "other"). But ask_questions takes the
# dict as is and passes it all the way to the UI, so if the model gets it wrong, it is
# rendered wrong. Observed incident (question.png): two is_other entries (B and X) came in,
# "Other — 직접 입력" was rendered twice, and the two radios shared the same otherActive
# state, breaking selection. A prompt convention alone cannot prevent this: the model
# had already been given the convention and still got it wrong.
import pytest
from aipds.agent.questions_payload import normalize_questions_payload


def _q(options, **kw):
    q = {"number": 1, "text": "다음 단계로 무엇을 할까요?", "options": options}
    q.update(kw)
    return {"name": "next-step", "questions": [q]}


def _letters(payload, qi=0):
    return [(o["letter"], o["is_other"]) for o in payload["questions"][qi]["options"]]


def test_keeps_a_well_formed_payload_intact():
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "핸드오프하고 종료", "is_other": False, "recommended": True},
        {"letter": "B", "text": "다음 단계로 진행", "is_other": False, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("B", False), ("X", True)]
    assert payload["questions"][0]["options"][0]["recommended"] is True


def test_collapses_a_duplicate_other_into_a_real_option():
    """Observed incident. B was a real option but came in with is_other=True, so its text
    vanished and it rendered as "Other — 직접 입력". Keep only X as Other and restore B."""
    payload = normalize_questions_payload(_q([
        {"letter": "B", "text": "이 사양서 그대로 핸드오프", "is_other": True, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
    ]))
    assert _letters(payload) == [("B", False), ("X", True)]
    # The text must be preserved so the user knows what they are choosing.
    assert payload["questions"][0]["options"][0]["text"] == "이 사양서 그대로 핸드오프"


def test_marks_the_x_option_as_other_even_when_the_model_says_false():
    # By schema convention, X is the Other slot. If it comes in with is_other=False, the
    # free-text input disappears and the user loses any way to say "none of the above".
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "진행", "is_other": False, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": False, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("X", True)]


def test_treats_an_other_prefixed_text_as_other():
    # Same rule as the markdown parser (questions.py: otext.lower().startswith("other")).
    # Even if the letter is not X, it is Other when the text is Other.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "진행", "is_other": False, "recommended": False},
        {"letter": "D", "text": "Other — 직접 입력", "is_other": False, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("D", True)]


def test_keeps_only_the_last_other_when_several_look_like_other():
    """With several Others, keep only the last: by convention Other is at the end of the
    list, and an earlier one is the model mislabeling a real option."""
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
        {"letter": "B", "text": "핸드오프", "is_other": False, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("B", False), ("X", True)]


def test_fills_a_blank_text_left_behind_by_a_demoted_other():
    # An option that came in with is_other=True may have empty text (the UI fills in the
    # wording, so the model omits it). Demoting it would leave an empty, unselectable label.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "", "is_other": True, "recommended": False},
        {"letter": "X", "text": "Other", "is_other": True, "recommended": False},
    ]))
    opt = payload["questions"][0]["options"][0]
    assert opt["is_other"] is False
    assert opt["text"].strip() != ""


def test_deduplicates_repeated_letters():
    # If the same letter appears twice, the frontend's key={opt.letter} collides and the
    # radio name/value overlap, so selections overwrite each other.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "첫째", "is_other": False, "recommended": False},
        {"letter": "A", "text": "둘째", "is_other": False, "recommended": False},
    ]))
    letters = [o["letter"] for o in payload["questions"][0]["options"]]
    assert len(letters) == len(set(letters))


def test_supplies_missing_letters_in_order():
    # A missing letter shows an empty badge and breaks the answer value contract ("A" / "A,C").
    payload = normalize_questions_payload(_q([
        {"text": "첫째", "is_other": False, "recommended": False},
        {"text": "둘째", "is_other": False, "recommended": False},
    ]))
    assert [o["letter"] for o in payload["questions"][0]["options"]] == ["A", "B"]


def test_defaults_the_optional_flags():
    # Missing multi_select / recommended / is_other is common. The frontend expects bools
    # (multi_select === true), so None must not flow through.
    payload = normalize_questions_payload(_q([{"letter": "A", "text": "진행"}]))
    q = payload["questions"][0]
    assert q["multi_select"] is False
    assert q["options"][0]["is_other"] is False
    assert q["options"][0]["recommended"] is False
    assert q["answer"] is None
    assert q["parse_ok"] is True if "parse_ok" in q else True


def test_normalize_sets_the_file_level_contract_fields():
    # Frontend QuestionsPayload contract: it renders as a form rather than
    # RawMarkdownFallback only with parse_ok=True + raw_markdown=None.
    payload = normalize_questions_payload(_q([{"letter": "A", "text": "진행"}]))
    assert payload["parse_ok"] is True
    assert payload["raw_markdown"] is None
    assert payload["name"] == "next-step"


def test_renumbers_questions_sequentially():
    # number is used as the radio name (q{number}) and the answer dict key. If duplicated,
    # the two questions' radios join the same group and deselect each other.
    payload = normalize_questions_payload({
        "name": "dup", "questions": [
            {"number": 1, "text": "첫째", "options": [{"letter": "A", "text": "a"}]},
            {"number": 1, "text": "둘째", "options": [{"letter": "A", "text": "b"}]},
        ]})
    assert [q["number"] for q in payload["questions"]] == [1, 2]


def test_rejects_a_payload_with_no_questions():
    # Do not silently show an empty form; the tool returns the reason so the model fixes it.
    with pytest.raises(ValueError):
        normalize_questions_payload({"name": "empty", "questions": []})


def test_rejects_a_question_with_no_selectable_option():
    # A question with only Other is not multiple choice (free text = chat is enough).
    with pytest.raises(ValueError):
        normalize_questions_payload(_q([
            {"letter": "X", "text": "Other", "is_other": True},
        ]))


def test_accepts_a_json_string_payload():
    """When the model passes a JSON string instead of a dict (observed: "질문 폼 전송 형식에
    오류가 있어 다시 보내겠습니다", i.e., "the question form format had an error, I will
    resend"). Accept it if it parses: a resend round trip looks like an empty wait to the user."""
    payload = normalize_questions_payload(
        '{"name": "s", "questions": [{"number": 1, "text": "q", '
        '"options": [{"letter": "A", "text": "a"}]}]}')
    assert payload["questions"][0]["text"] == "q"


def test_keeps_hangul_literal_rather_than_escaped():
    # Observed: "잘못된 유니코드 이스케이프가 있어 실패했습니다" ("failed due to an invalid
    # Unicode escape"). Hangul must survive normalization intact (SSE serializes with
    # ensure_ascii=False).
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "승인 — 다음 단계로 진행"},
    ]))
    assert payload["questions"][0]["options"][0]["text"] == "승인 — 다음 단계로 진행"


# ---- SDK AskUserQuestion input → QuestionFile ----
# Merges builder._to_question_file and Discovery's normalization, which did the same job.
# Merged, the duplicate-is_other correction (2026-07-26 bug) also applies to the prototype builder.
from aipds.agent.questions_payload import question_file_from_sdk

SDK_Q = [{"question": "다음 단계는?", "header": "Next",
          "multiSelect": False,
          "options": [{"label": "진행", "description": "다음 스테이지로"},
                      {"label": "종료", "description": "핸드오프"}]}]


def test_maps_sdk_options_to_letters_in_order():
    f = question_file_from_sdk(SDK_Q, name="next-step")
    opts = f["questions"][0]["options"]
    # Look only at the real options; a free-text X is appended at the end of the list
    # (see test_sdk_questions_always_get_an_other_option). The letter index must map 1:1
    # to the SDK option order for answers to translate back correctly, so the point of
    # this assertion is that X does not cut into that sequence.
    assert [o["letter"] for o in opts if not o["is_other"]] == ["A", "B"]
    assert opts[0]["text"].startswith("진행")


def test_joins_label_and_description():
    f = question_file_from_sdk(SDK_Q, name="n")
    assert f["questions"][0]["options"][0]["text"] == "진행 — 다음 스테이지로"


def test_drops_the_dash_when_description_is_empty():
    f = question_file_from_sdk([{"question": "q", "options": [{"label": "진행"}]}],
                               name="n")
    assert f["questions"][0]["options"][0]["text"] == "진행"


def test_carries_header_as_category_and_multiselect():
    f = question_file_from_sdk(
        [{"question": "q", "header": "Audience", "multiSelect": True,
          "options": [{"label": "A"}, {"label": "B"}]}], name="n")
    q = f["questions"][0]
    assert q["category"] == "Audience"
    assert q["multi_select"] is True


def test_sdk_sets_the_file_level_contract_fields():
    f = question_file_from_sdk(SDK_Q, name="next-step")
    assert f["name"] == "next-step"
    assert f["parse_ok"] is True
    assert f["raw_markdown"] is None


def test_result_passes_the_normalizer_unchanged():
    # Whether the two paths converge on one contract: the converted SDK input must come
    # through normalization unchanged (no option is demoted, no letter changes).
    f = question_file_from_sdk(SDK_Q, name="n")
    assert normalize_questions_payload(f) == f


def test_rejects_a_question_with_no_options():
    # If the SDK sends a question with no options, the form has nothing to choose.
    with pytest.raises(ValueError):
        question_file_from_sdk([{"question": "q", "options": []}], name="n")


def test_sdk_questions_always_get_an_other_option():
    """The SDK path must also offer a free-text option.

    Observed incident: pressing "다시 빌드" (rebuild) makes the agent assess the current
    state and ask what to do via AskUserQuestion, but there was no way to ask for something
    not among the options (e.g., "로그인 화면만 다시", redo just the login screen).
    AskUserQuestion has no is_other field at all, and this path normalizes with
    guess_other=False, so there was no path at all through which an Other option could
    appear.

    The rest of the wiring already exists: QuestionCard draws a free-text input for
    is_other (QuestionCard.tsx), and builder._answer_to_sdk returns a value that matches
    no option letter to the SDK as free text (builder.py). What was missing was the option itself.
    """
    f = question_file_from_sdk(SDK_Q, name="n")
    opts = f["questions"][0]["options"]
    others = [o for o in opts if o["is_other"]]
    assert len(others) == 1, opts
    assert others[0] is opts[-1], "Other는 목록 끝이어야 한다"
    assert others[0]["letter"] == "X", opts


def test_the_other_option_does_not_shift_real_option_letters():
    """The letter index is what _answer_to_sdk uses to translate an answer back to the SDK
    label (sdk_options[_LETTERS.find(letter)]). If Other cut into the A/B/C sequence, every
    answer would shift by one and map to the wrong option; hence X."""
    f = question_file_from_sdk(SDK_Q, name="n")
    opts = f["questions"][0]["options"]
    real = [o for o in opts if not o["is_other"]]
    assert [o["letter"] for o in real] == ["A", "B"]
    assert real[0]["text"].startswith("진행")


def test_a_model_supplied_other_still_gets_a_real_free_text_option():
    """When the model adds an "Other" label itself.

    Detecting that and skipping our X was tried first, and it was worse: this path
    normalizes with guess_other=False, so the model's option keeps is_other False, and the
    result had no free-text input at all, which is exactly the problem being fixed. So X is
    always appended, and the only guarantee is that there is exactly one free-text input.
    """
    sdk_q = [{"question": "q", "options": [
        {"label": "진행"},
        {"label": "Other", "description": "직접 입력"},
    ]}]
    f = question_file_from_sdk(sdk_q, name="n")
    opts = f["questions"][0]["options"]
    others = [o for o in opts if o["is_other"]]
    assert len(others) == 1, opts
    assert others[0]["letter"] == "X", opts
    # The model's "Other" survives as an ordinary option; its text is not erased.
    assert opts[1]["text"] == "Other — 직접 입력", opts


def test_an_option_literally_labeled_other_is_not_reclassified():
    """Review finding 1: the _looks_like_other heuristic in normalize_questions_payload
    (treat text starting with "other" as Other) is needed only on paths where the model
    builds a free-form dict itself, such as the markdown/Discovery path. On the SDK path
    the model already supplies structured, explicit options, so a real option label that
    happens to start with "other", like "Other database", must survive as is. If demoted,
    the frontend overwrites the label with the hardcoded "Other — 직접 입력", and
    _answer_to_sdk returns the user's raw typed text to the SDK instead of the option the
    model defined."""
    sdk_q = [{"question": "어떤 DB를 쓸까?", "options": [
        {"label": "Other database", "description": "specify your own"},
        {"label": "Postgres", "description": "relational"},
    ]}]
    f = question_file_from_sdk(sdk_q, name="n")
    opts = f["questions"][0]["options"]
    assert opts[0]["is_other"] is False
    assert opts[0]["text"] == "Other database — specify your own"
