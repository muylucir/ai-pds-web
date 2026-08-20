# claude 드라이버(CLI 트랜스크립트) 히스토리 복원.
#
# 예전에는 이 파일 앞쪽 절반이 strands 폴백 드라이버의 Bedrock Converse 모양
# (toolUse/toolResult, camelCase)을 검사했다. 그 드라이버를 삭제하면서
# transform_messages와 함께 지웠다 — 남은 것은 CLI의 Anthropic Messages 모양
# (block["type"] == "tool_use")뿐이다.
import json

import pytest

from fakes.in_memory_s3 import FakeS3Store
from aipds.agent.session_store import DiscoverySessionStore
from aipds.models import HistoryItem
from aipds.session_history import list_history, transform_cli_transcript


def _cli(type_, role=None, content=None, **extra):
    """CLI 트랜스크립트 한 줄. message가 없는 부기 줄도 만들 수 있다."""
    d = {"type": type_, **extra}
    if role is not None:
        d["message"] = {"role": role, "content": content}
    return d


def test_cli_bookkeeping_lines_are_skipped():
    # 실측: 실제 트랜스크립트에는 queue-operation/attachment/ai-title/last-prompt
    # 처럼 message가 없는 줄이 섞여 있다. 대화가 아니므로 복원 대상이 아니다.
    raw = [
        _cli("queue-operation", operation="enqueue"),
        _cli("attachment"),
        _cli("ai-title"),
        _cli("user", "user", "시작해줘"),
        _cli("last-prompt"),
    ]
    items = transform_cli_transcript(raw)
    assert [(i.role, i.text) for i in items] == [("user", "시작해줘")]


def test_cli_plain_string_content_is_not_iterated_per_character():
    # 실측: 첫 user 줄의 content는 리스트가 아니라 평문 문자열이다. 정규화하지
    # 않으면 블록 루프가 문자열을 문자 단위로 훑어 아무것도 남지 않는다.
    items = transform_cli_transcript([_cli("user", "user", "Say OK")])
    assert [(i.role, i.text) for i in items] == [("user", "Say OK")]


def test_cli_text_and_tools_match_the_live_representation():
    # 라이브(claude_driver._translate)와 같은 표현이어야 스크롤백이 방금 본
    # 화면과 달라 보이지 않는다: text는 말풍선, 파일 도구는 file_changed,
    # 그 외 도구는 status.
    raw = [_cli("assistant", "assistant", [
        {"type": "text", "text": "문서를 씁니다."},
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": "aiplc-docs/audit.md"}},
        {"type": "tool_use", "id": "t2", "name": "Read",
         "input": {"file_path": "rules.md"}},
    ])]
    ai = next(i for i in transform_cli_transcript(raw) if i.role == "ai")
    assert ai.text == "문서를 씁니다."
    assert [t.model_dump() for t in ai.trace] == [
        # 파일 도구는 경로를 `path`로 — 프론트가 "📝 파일 변경: …"을 그린다.
        {"kind": "file_changed", "text": None, "path": "aiplc-docs/audit.md",
         "detail": None},
        # 그 외 도구는 이름 + **무엇을 했는지**. 라이브도 같은 값을 보낸다
        # (둘 다 tool_trace.tool_detail을 쓴다) — 그것이 이 테스트의 요점이다.
        {"kind": "status", "text": "Read", "path": None, "detail": "rules.md"},
    ]


def test_cli_bash_commands_are_redacted_in_the_restored_trace():
    """Bash 명령은 자격증명이 나타나는 자리다.

    라이브는 `routes/turns._redacted`가 payload를 훑는다. 복원은 이 함수가 직접
    통과시켜야 한다 — 한쪽만 하면 새로고침이 리댁션을 우회하는 경로가 된다."""
    raw = [_cli("assistant", "assistant", [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE ls"}},
    ])]
    ai = next(i for i in transform_cli_transcript(raw) if i.role == "ai")
    detail = ai.trace[0].detail or ""
    assert "AKIAIOSFODNN7EXAMPLE" not in detail, detail


def test_cli_one_turn_is_one_bubble_not_one_per_tool_call():
    """한 턴은 말풍선 하나다 — 도구 호출마다 빈 말풍선을 만들면 안 된다.

    실측한 트랜스크립트 구조(실 CLI, 도구 5회 사용 턴):

        assistant [thinking]
        assistant [tool_use]   user [tool_result]   × 5
        assistant [text]       <- 여기서만 사람이 읽을 내용이 나온다

    CLI는 도구 호출마다 별도 assistant 줄을 쓴다. 줄마다 항목을 만들면 라이브에서
    말풍선 하나였던 턴이 6개로 쪼개지고 그중 5개가 텍스트 없는 빈 말풍선이 된다
    (실측: live message 이벤트 1개 -> restored ai 항목 6개, 빈 것 5개). 화면에서는
    "추론 과정"만 달린 빈 회색 상자가 줄줄이 나온다.

    라이브는 턴 하나당 AiItem 하나를 만들어 message를 그 하나에 누적하고 도구는
    같은 항목의 trace에 쌓는다(useTurnStream.ts:108,121-123). 복원도 같아야 한다.
    """
    raw = [
        _cli("user", "user", "두 파일을 쓰고 확인해줘"),
        _cli("assistant", "assistant", [{"type": "thinking", "thinking": "..."}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Write",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t2", "name": "Write",
             "input": {"file_path": "b.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t3", "name": "Read",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t3", "content": "..."}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "완료"}]),
    ]
    items = transform_cli_transcript(raw)

    assert [i.role for i in items] == ["user", "ai"], \
        [(i.role, i.text) for i in items]
    ai = items[1]
    assert ai.text == "완료"
    # 도구 3개가 모두 그 하나의 트레이스로 모인다.
    assert [(t.kind, t.path or t.text) for t in ai.trace] == [
        ("file_changed", "a.md"), ("file_changed", "b.md"), ("status", "Read")]


def test_cli_a_turn_that_never_produced_text_is_not_an_empty_bubble():
    """텍스트 없이 끝난 턴은 빈 말풍선이 아니라 트레이스만 남는다.

    중단된 턴(유휴 타임아웃, SSE 끊김)이 이 모양이다. 말풍선을 만들면 내용 없는
    회색 상자가 남는데, 라이브에서 그 자리에 있던 것은 진행 표시였고 그것은
    복원 대상이 아니다.
    """
    raw = [
        _cli("user", "user", "시작해줘"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "x.md"}}]),
    ]
    items = transform_cli_transcript(raw)
    ai = [i for i in items if i.role == "ai"]
    assert len(ai) == 1, [(i.role, i.text) for i in items]
    assert ai[0].text == ""
    assert [t.text for t in ai[0].trace] == ["Read"]


def test_cli_separate_user_turns_stay_separate():
    """병합은 턴 안에서만 한다 — 실제 사용자 발화가 경계다.

    tool_result를 담은 user 줄은 사용자 발화가 아니므로 경계가 아니다(라이브는
    그 줄을 아무것도 렌더하지 않는다). 진짜 입력만 새 턴을 시작해야 두 번의
    질문이 하나로 뭉치지 않는다.
    """
    raw = [
        _cli("user", "user", "첫 질문"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "a.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "첫 답"}]),
        _cli("user", "user", "둘째 질문"),
        _cli("assistant", "assistant", [{"type": "text", "text": "둘째 답"}]),
    ]
    items = transform_cli_transcript(raw)
    assert [(i.role, i.text) for i in items] == [
        ("user", "첫 질문"), ("ai", "첫 답"),
        ("user", "둘째 질문"), ("ai", "둘째 답")]
    assert [t.text for t in items[1].trace] == ["Read"]
    assert not items[3].trace


def test_cli_question_card_comes_after_the_bubble_that_asked():
    """카드는 그것을 낸 말풍선 뒤에 온다.

    라이브에서는 모델이 "왜 묻는지" 설명을 먼저 흘리고 그 다음 질문 카드가
    붙는다(claude_driver의 _CONTACT_ADDENDUM이 그 설명을 요구한다). 카드가
    말풍선보다 앞서면 스크롤백에서 설명 없는 질문이 먼저 뜨고 그 아래에 설명이
    따라오는, 라이브에서는 본 적 없는 순서가 된다.

    턴 누적 때문에 생기는 순서다 — 말풍선 확정이 다음 사용자 발화까지 미뤄지는
    동안 카드는 곧바로 붙을 수 있다.
    """
    raw = [
        _cli("user", "user", "시작해줘"),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "r1", "name": "Read",
             "input": {"file_path": "rules.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "r1", "content": "ok"}]),
        _cli("assistant", "assistant", [
            {"type": "text", "text": "질문 드립니다."},
            {"type": "tool_use", "id": "qa", "name": "AskUserQuestion",
             "input": {"questions": []}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "qa",
             "content": [{"text": _CLI_ANSWER_SENTENCE}]}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "작성했습니다."}]),
    ]
    items = transform_cli_transcript(raw)

    assert [i.role for i in items] == ["user", "ai", "card", "user", "ai"], \
        [(i.role, i.text) for i in items]
    assert items[1].text == "질문 드립니다."
    assert [t.text for t in items[1].trace] == ["Read"]
    assert items[2].card == "questions"
    # 순서만 검사한다 — 문구는 위 테스트가 덮는다.
    assert items[3].text.startswith("답변 제출")


def test_cli_ask_user_question_becomes_a_card_not_a_trace_row():
    raw = [_cli("assistant", "assistant", [
        {"type": "text", "text": "질문 드립니다."},
        {"type": "tool_use", "id": "ta", "name": "AskUserQuestion",
         "input": {"questions": []}},
    ])]
    items = transform_cli_transcript(raw)
    ai = next(i for i in items if i.role == "ai")
    assert not ai.trace, "카드로 표현되므로 트레이스에 중복 노출하지 않는다"
    assert any(i.role == "card" and i.card == "questions" for i in items)


#: CLI가 AskUserQuestion 결과로 쓰는 **자기** 문장. 우리 접두사도 JSON도 아니다 —
#: 그래서 이 본문에서는 답변을 펼 수 없고, 정확한 값은 answer_records가 준다
#: (agent/answer_store.py 헤더가 이 결정의 근거다).
_CLI_ANSWER_SENTENCE = ('Your questions have been answered: "질문"="보기". '
                        "You can now continue with these answers in mind.")


def test_cli_answer_tool_result_becomes_a_readable_user_bubble():
    # 답변 결과만 골라야 한다 — 실제 트랜스크립트에는 Write/Read의 tool_result가
    # 섞여 있으므로 AskUserQuestion의 id와 매칭한다.
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "ta", "name": "AskUserQuestion",
             "input": {"questions": []}}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tw", "name": "Write",
             "input": {"file_path": "x.md"}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tw",
             "content": [{"type": "text", "text": "written"}]}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "ta",
             "content": [{"type": "text", "text": _CLI_ANSWER_SENTENCE}]}]),
    ]
    users = [i for i in transform_cli_transcript(raw) if i.role == "user"]
    assert len(users) == 1, [i.text for i in users]
    # 레코드가 없으면 CLI 문장을 그대로 폴백으로 실어 보낸다 — 빈 말풍선보다 낫다.
    # 정확한 문항 번호·letter는 answer_records가 준다(agent/answer_store.py).
    assert users[0].text.startswith("답변 제출")
    assert users[0].answers is None


#: SDK AskUserQuestion의 input 모양. 실제 트랜스크립트에서 옮겨온 것이다.
_SDK_QUESTIONS = [
    {"question": "무엇을 만드나요?", "header": "도메인", "multiSelect": False,
     "options": [{"label": "자동차 부품", "description": "IATF 16949 환경"},
                 {"label": "가전", "description": "소비자 제품"}]},
    {"question": "월 클레임 건수는?", "header": "규모", "multiSelect": True,
     "options": [{"label": "10건 미만"}, {"label": "10~30건"}]},
]

#: CLI가 AskUserQuestion 결과로 쓰는 **자기** 문장. 우리 접두사도 JSON도 아니다 —
#: 이것을 되파싱하지 않는 것이 답변 레코드를 도입한 이유다.
_CLI_ANSWER_SENTENCE = ('Your questions have been answered: '
                        '"무엇을 만드나요?"="자동차 부품". '
                        'You can now continue with these answers in mind.')


def test_cli_answer_uses_the_answer_record_when_one_exists():
    """레코드가 있으면 라이브와 같은 값으로 복원된다.

    종전에는 CLI가 쓴 영어 문장을 그대로 `답변 제출: <문장>`으로 내보냈다 —
    문항 번호·보기 letter·보기 텍스트가 전부 사라져서, 같은 대화가 새로고침
    전후로 다르게 보였다. 레코드에는 우리가 받은 그대로의 답변 dict와 그 순간의
    질문 payload가 있으므로 프론트가 answerSummary로 라이브와 같은 문구를 만든다.
    """
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tu-1", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu-1",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    records = {"tu-1": {"tool_use_id": "tu-1", "interrupt_id": "iid",
                        "questions": {"name": "discovery-questions",
                                      "questions": [{"number": 1}]},
                        "answers": {"1": "A", "2": "A,B"}}}

    items = transform_cli_transcript(raw, answer_records=records)

    user = next(i for i in items if i.role == "user")
    assert user.answers == {"1": "A", "2": "A,B"}
    assert user.questions == records["tu-1"]["questions"]
    # 영어 문장이 화면 문구로 새어나가지 않는다 — 프론트가 UI 언어로 다시 만든다.
    assert "have been answered" not in (user.text or "")


def test_cli_answer_falls_back_to_the_sentence_without_a_record():
    """레코드 없는 세션(이 기능 이전)은 종전 동작을 유지한다 — 다만 그 라운드의
    질문 payload는 트랜스크립트에서 복원되므로 카드가 무엇을 물었는지 말해준다."""
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "tu-1", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "tu-1",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    items = transform_cli_transcript(raw)

    card = next(i for i in items if i.role == "card")
    assert card.questions is not None
    assert [q["text"] for q in card.questions["questions"]] == [
        "무엇을 만드나요?", "월 클레임 건수는?"]
    # 보기는 라이브와 같은 함수로 조립된다(letter + "라벨 — 설명" + Other 추가).
    first = card.questions["questions"][0]["options"]
    assert first[0]["letter"] == "A" and first[0]["text"] == "자동차 부품 — IATF 16949 환경"
    assert first[-1]["is_other"] is True
    user = next(i for i in items if i.role == "user")
    assert user.answers is None and "have been answered" in (user.text or "")


def test_cli_rejected_question_round_leaves_no_ghost_card():
    """CLI가 스키마 검증으로 막은 라운드는 사용자에게 뜬 적이 없다.

    실측: 모델이 `questions`를 JSON 문자열로 넘긴 3건이 `is_error: true`로
    즉시 거절됐고, 모델은 다음 턴에 올바른 배열로 재시도했다. 거절된 라운드까지
    카드를 만들면 아무도 보지 않은 질문 카드가 스크롤백에 남는다.
    """
    raw = [
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "bad", "name": "AskUserQuestion",
             "input": {"questions": '[{"question": "..."}]'}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "bad", "is_error": True,
             "content": "<tool_use_error>InputValidationError: ... "
                        "`questions` type is expected as `array` but provided "
                        "as `string`</tool_use_error>"}]),
        _cli("assistant", "assistant", [
            {"type": "tool_use", "id": "good", "name": "AskUserQuestion",
             "input": {"questions": _SDK_QUESTIONS}}]),
        _cli("user", "user", [
            {"type": "tool_result", "tool_use_id": "good",
             "content": _CLI_ANSWER_SENTENCE}]),
    ]
    items = transform_cli_transcript(raw)

    cards = [i for i in items if i.role == "card"]
    assert len(cards) == 1, [i.questions for i in cards]
    # 거절된 라운드의 결과는 답변 말풍선도 만들지 않는다(답변이 아니다).
    assert len([i for i in items if i.role == "user"]) == 1


def _driver_session_key(project_id: str) -> dict:
    """드라이버가 미러링에 실제로 쓰는 세션 키.

    project_id를 그대로 쓰면 안 된다 — 그것이 이 버그의 두 번째 겹이었다. CLI가
    비-UUID session-id를 거부하므로 드라이버는 uuid5를 유도해 그 값으로 쓰고,
    읽는 쪽이 원본 project_id를 쓰면 아무것도 쓰이지 않은 프리픽스를 뒤진다.
    양쪽이 같은 실수를 하면 어긋난 상태로도 테스트는 통과한다.
    """
    from aipds.agent.claude_driver import _sdk_session_id
    session_id, _ = _sdk_session_id({"session_id": project_id})
    return {"session_id": session_id}


async def test_list_history_reads_what_the_session_store_wrote():
    """쓰기와 읽기가 같은 키를 본다는 것 — 이 버그의 핵심이 경로 불일치였다."""
    s3 = FakeS3Store()
    store = DiscoverySessionStore(s3)
    key = _driver_session_key("sess-1")
    await store.append(key, [
        {"type": "user", "message": {"role": "user", "content": "시작해줘"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "네, 시작합니다."}]}},
    ])
    # 프로덕션 배선과 같게: claude 트랜스크립트는 project_s3에서 읽는다
    # (드라이버가 s3_store_factory(pid)로 받은 그 스토어에 쓴다).
    items = await list_history(s3, "sess-1")
    assert [(i.role, i.text) for i in items] == [
        ("user", "시작해줘"), ("ai", "네, 시작합니다.")]


async def test_append_across_instances_does_not_overwrite_earlier_batches():
    """resume은 새 store 인스턴스를 만든다. 시퀀스를 0에서 시작하면 첫 append가
    이전 세션의 초반 배치를 같은 키로 덮어써 히스토리 앞부분이 사라진다."""
    s3 = FakeS3Store()
    key = _driver_session_key("sess-3")
    first = DiscoverySessionStore(s3)
    await first.append(key, [{"type": "user",
                              "message": {"role": "user", "content": "첫 턴"}}])
    second = DiscoverySessionStore(s3)   # resume이 만드는 새 인스턴스
    await second.append(key, [{"type": "user",
                               "message": {"role": "user", "content": "두 번째 턴"}}])
    items = await list_history(s3, "sess-3")
    assert [i.text for i in items] == ["첫 턴", "두 번째 턴"]
