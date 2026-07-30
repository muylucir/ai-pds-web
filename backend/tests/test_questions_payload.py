# ask_questions로 들어온 모델 생성 페이로드의 정규화 계약.
#
# 왜 코드가 교정하는가: 마크다운 경로(parsers/questions.py)는 is_other를 코드가
# 판정한다(letter == "X" 또는 텍스트가 "other"로 시작). 하지만 ask_questions는
# dict를 그대로 받아 UI까지 흘려보내므로 모델이 틀리면 그대로 렌더된다.
# 실측 사고(question.png): is_other가 두 개(B와 X) 와서 "Other — 직접 입력"이
# 중복 렌더됐고, 두 라디오가 같은 otherActive 상태를 공유해 선택이 깨졌다.
# 프롬프트 규약만으로는 못 막는다 — 모델은 이미 규약을 받고도 틀렸다.
import pytest
from pathfinder.agent.questions_payload import normalize_questions_payload


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
    """실측 사고. B가 실질 선택지인데 is_other=True로 와서 텍스트가 사라지고
    "Other — 직접 입력"으로 렌더됐다. X만 Other로 남기고 B는 되살린다."""
    payload = normalize_questions_payload(_q([
        {"letter": "B", "text": "이 사양서 그대로 핸드오프", "is_other": True, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
    ]))
    assert _letters(payload) == [("B", False), ("X", True)]
    # 텍스트가 보존돼야 사용자가 무엇을 고르는지 알 수 있다.
    assert payload["questions"][0]["options"][0]["text"] == "이 사양서 그대로 핸드오프"


def test_marks_the_x_option_as_other_even_when_the_model_says_false():
    # X는 스키마 규약상 Other 자리다. is_other=False로 오면 자유 입력창이
    # 사라져 사용자가 "위 선택지에 없음"을 표현할 방법을 잃는다.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "진행", "is_other": False, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": False, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("X", True)]


def test_treats_an_other_prefixed_text_as_other():
    # 마크다운 파서와 같은 규칙(questions.py: otext.lower().startswith("other")).
    # letter가 X가 아니어도 텍스트가 Other면 Other다.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "진행", "is_other": False, "recommended": False},
        {"letter": "D", "text": "Other — 직접 입력", "is_other": False, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("D", True)]


def test_keeps_only_the_last_other_when_several_look_like_other():
    """Other가 여러 개면 마지막 하나만 남긴다 — 관례상 Other는 목록 끝이고,
    앞쪽에 온 것은 모델이 실질 선택지를 잘못 표시한 경우다."""
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
        {"letter": "B", "text": "핸드오프", "is_other": False, "recommended": False},
        {"letter": "X", "text": "Other — 직접 입력", "is_other": True, "recommended": False},
    ]))
    assert _letters(payload) == [("A", False), ("B", False), ("X", True)]


def test_fills_a_blank_text_left_behind_by_a_demoted_other():
    # is_other=True로 온 옵션은 텍스트가 비어 있을 수 있다(UI가 문구를 넣어
    # 주므로 모델이 생략). 강등하면 빈 라벨이 되어 고를 수 없는 보기가 된다.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "", "is_other": True, "recommended": False},
        {"letter": "X", "text": "Other", "is_other": True, "recommended": False},
    ]))
    opt = payload["questions"][0]["options"][0]
    assert opt["is_other"] is False
    assert opt["text"].strip() != ""


def test_deduplicates_repeated_letters():
    # 같은 letter가 두 번 오면 프론트의 key={opt.letter}가 충돌하고 라디오
    # name/value가 겹쳐 선택이 서로를 덮어쓴다.
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "첫째", "is_other": False, "recommended": False},
        {"letter": "A", "text": "둘째", "is_other": False, "recommended": False},
    ]))
    letters = [o["letter"] for o in payload["questions"][0]["options"]]
    assert len(letters) == len(set(letters))


def test_supplies_missing_letters_in_order():
    # letter 누락 시 빈 배지가 뜨고 답변 값 계약("A" / "A,C")이 깨진다.
    payload = normalize_questions_payload(_q([
        {"text": "첫째", "is_other": False, "recommended": False},
        {"text": "둘째", "is_other": False, "recommended": False},
    ]))
    assert [o["letter"] for o in payload["questions"][0]["options"]] == ["A", "B"]


def test_defaults_the_optional_flags():
    # multi_select / recommended / is_other 누락은 흔하다. 프론트는 bool을
    # 기대하므로(multi_select === true) None이 흘러가면 안 된다.
    payload = normalize_questions_payload(_q([{"letter": "A", "text": "진행"}]))
    q = payload["questions"][0]
    assert q["multi_select"] is False
    assert q["options"][0]["is_other"] is False
    assert q["options"][0]["recommended"] is False
    assert q["answer"] is None
    assert q["parse_ok"] is True if "parse_ok" in q else True


def test_normalize_sets_the_file_level_contract_fields():
    # 프론트 QuestionsPayload 계약: parse_ok=True + raw_markdown=None이어야
    # RawMarkdownFallback이 아니라 폼으로 렌더된다.
    payload = normalize_questions_payload(_q([{"letter": "A", "text": "진행"}]))
    assert payload["parse_ok"] is True
    assert payload["raw_markdown"] is None
    assert payload["name"] == "next-step"


def test_renumbers_questions_sequentially():
    # number는 라디오 name(q{number})과 답변 dict 키로 쓰인다. 중복되면
    # 두 질문의 라디오가 같은 그룹이 되어 서로를 해제한다.
    payload = normalize_questions_payload({
        "name": "dup", "questions": [
            {"number": 1, "text": "첫째", "options": [{"letter": "A", "text": "a"}]},
            {"number": 1, "text": "둘째", "options": [{"letter": "A", "text": "b"}]},
        ]})
    assert [q["number"] for q in payload["questions"]] == [1, 2]


def test_rejects_a_payload_with_no_questions():
    # 조용히 빈 폼을 띄우지 않는다 — 도구가 이유를 돌려줘 모델이 고치게 한다.
    with pytest.raises(ValueError):
        normalize_questions_payload({"name": "empty", "questions": []})


def test_rejects_a_question_with_no_selectable_option():
    # Other 하나만 있는 질문은 객관식이 아니다(자유 입력 = 채팅으로 충분).
    with pytest.raises(ValueError):
        normalize_questions_payload(_q([
            {"letter": "X", "text": "Other", "is_other": True},
        ]))


def test_accepts_a_json_string_payload():
    """모델이 dict 대신 JSON 문자열을 넘기는 경우(실측: "질문 폼 전송 형식에
    오류가 있어 다시 보내겠습니다"). 파싱 가능하면 받아준다 — 재전송 왕복은
    사용자에게 빈 대기로 보인다."""
    payload = normalize_questions_payload(
        '{"name": "s", "questions": [{"number": 1, "text": "q", '
        '"options": [{"letter": "A", "text": "a"}]}]}')
    assert payload["questions"][0]["text"] == "q"


def test_keeps_hangul_literal_rather_than_escaped():
    # 실측: "잘못된 유니코드 이스케이프가 있어 실패했습니다". 정규화를 통과한
    # 뒤에도 한글은 그대로 남아야 한다(SSE는 ensure_ascii=False로 직렬화).
    payload = normalize_questions_payload(_q([
        {"letter": "A", "text": "승인 — 다음 단계로 진행"},
    ]))
    assert payload["questions"][0]["options"][0]["text"] == "승인 — 다음 단계로 진행"


# ---- SDK AskUserQuestion input → QuestionFile ----
# builder._to_question_file과 Discovery의 정규화가 같은 일을 하던 것을 합친다.
# 합치면 is_other 중복 교정(2026-07-26 버그)이 프로토타입 빌더에도 적용된다.
from pathfinder.agent.questions_payload import question_file_from_sdk

SDK_Q = [{"question": "다음 단계는?", "header": "Next",
          "multiSelect": False,
          "options": [{"label": "진행", "description": "다음 스테이지로"},
                      {"label": "종료", "description": "핸드오프"}]}]


def test_maps_sdk_options_to_letters_in_order():
    f = question_file_from_sdk(SDK_Q, name="next-step")
    opts = f["questions"][0]["options"]
    # 실제 옵션만 본다 — 목록 끝에는 자유 입력용 X가 붙는다
    # (test_sdk_questions_always_get_an_other_option 참조). letter 인덱스가 SDK
    # 옵션 순서와 1:1이어야 답변 되번역이 맞으므로, X가 그 흐름에 끼어들지
    # 않는다는 것이 이 단정의 핵심이다.
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
    # 두 경로가 한 계약으로 수렴하는지 — SDK 입력을 변환한 결과가 정규화를
    # 통과해도 그대로여야 한다(옵션이 강등되거나 letter가 바뀌지 않는다).
    f = question_file_from_sdk(SDK_Q, name="n")
    assert normalize_questions_payload(f) == f


def test_rejects_a_question_with_no_options():
    # SDK가 옵션 없는 질문을 보내면 폼에 고를 게 없다.
    with pytest.raises(ValueError):
        question_file_from_sdk([{"question": "q", "options": []}], name="n")


def test_sdk_questions_always_get_an_other_option():
    """SDK 경로에도 자유 입력 선택지가 있어야 한다.

    실측 사고: "다시 빌드"를 누르면 에이전트가 현재 상태를 파악하고 무엇을 할지
    AskUserQuestion으로 묻는데, 선택지에 없는 일(예: "로그인 화면만 다시")을
    시킬 방법이 없었다. AskUserQuestion에는 is_other 필드가 아예 없고 이 경로는
    guess_other=False로 정규화하므로, Other 옵션이 생길 수 있는 경로가 하나도
    없었다.

    배선의 나머지는 이미 있다 — QuestionCard가 is_other에 자유 입력창을 그리고
    (QuestionCard.tsx), builder._answer_to_sdk가 옵션 letter에 매칭되지 않는
    값을 자유 텍스트로 그대로 SDK에 돌려준다(builder.py). 없던 것은 옵션 자체다.
    """
    f = question_file_from_sdk(SDK_Q, name="n")
    opts = f["questions"][0]["options"]
    others = [o for o in opts if o["is_other"]]
    assert len(others) == 1, opts
    assert others[0] is opts[-1], "Other는 목록 끝이어야 한다"
    assert others[0]["letter"] == "X", opts


def test_the_other_option_does_not_shift_real_option_letters():
    """letter 인덱스는 _answer_to_sdk가 답변을 SDK 라벨로 되번역하는 근거다
    (sdk_options[_LETTERS.find(letter)]). Other가 A/B/C 흐름에 끼어들면 모든
    답변이 한 칸씩 밀려 엉뚱한 옵션으로 번역된다 — 그래서 X를 쓴다."""
    f = question_file_from_sdk(SDK_Q, name="n")
    opts = f["questions"][0]["options"]
    real = [o for o in opts if not o["is_other"]]
    assert [o["letter"] for o in real] == ["A", "B"]
    assert real[0]["text"].startswith("진행")


def test_a_model_supplied_other_still_gets_a_real_free_text_option():
    """모델이 "Other" 라벨을 직접 넣은 경우.

    그것을 감지해서 우리 X를 건너뛰는 쪽을 먼저 시도했는데 더 나빴다: 이 경로는
    guess_other=False로 정규화하므로 모델이 넣은 옵션의 is_other는 False로 남고,
    결과는 자유 입력창이 하나도 없는 상태였다 — 고치려던 문제 그대로다. 그래서
    항상 붙이고, 자유 입력창이 정확히 하나 있다는 것만 보장한다.
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
    # 모델의 "Other"는 평범한 보기로 살아 있다 — 텍스트가 지워지지 않는다.
    assert opts[1]["text"] == "Other — 직접 입력", opts


def test_an_option_literally_labeled_other_is_not_reclassified():
    """리뷰 finding 1: normalize_questions_payload의 _looks_like_other 휴리스틱
    (텍스트가 "other"로 시작하면 Other로 간주)은 마크다운/Discovery 경로처럼
    모델이 자유형 dict를 직접 만드는 경로에서만 필요하다. SDK 경로는 모델이
    이미 명시적 options를 구조화해서 주므로, "Other database"처럼 실제 옵션
    라벨이 우연히 "other"로 시작해도 그대로 살아 있어야 한다 — 강등되면
    프론트가 라벨을 하드코딩된 "Other — 직접 입력"으로 덮어쓰고,
    _answer_to_sdk는 모델이 정의한 옵션이 아니라 사용자가 입력한 원문
    텍스트를 SDK로 돌려주게 된다."""
    sdk_q = [{"question": "어떤 DB를 쓸까?", "options": [
        {"label": "Other database", "description": "specify your own"},
        {"label": "Postgres", "description": "relational"},
    ]}]
    f = question_file_from_sdk(sdk_q, name="n")
    opts = f["questions"][0]["options"]
    assert opts[0]["is_other"] is False
    assert opts[0]["text"] == "Other database — specify your own"
