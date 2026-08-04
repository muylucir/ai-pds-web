import json
import pytest
from pathfinder.session_history import transform_messages, list_history
from pathfinder.models import HistoryItem

def _msg(role, content, mid):
    return {"message": {"role": role, "content": content}, "message_id": mid}

RAW = [
    _msg("user", [{"text": "AI-PLC를 시작해줘"}], 0),
    _msg("assistant", [
        {"reasoningContent": {"reasoningText": {"text": "", "signature": "sig"}}},
        {"text": "환영합니다."},
        {"toolUse": {"toolUseId": "tu-write", "name": "file_write",
                     "input": {"path": "aiplc-docs/audit.md", "content": "x"}}}], 1),
    _msg("user", [
        {"toolResult": {"toolUseId": "tu-write", "status": "success",
                        "content": [{"text": "written: aiplc-docs/audit.md"}]}}], 2),
    _msg("assistant", [
        {"text": "질문 드립니다."},
        {"toolUse": {"toolUseId": "tu-ask", "name": "ask_questions",
                     "input": {"questions_file": {"name": "discovery-mode-selection",
                                                  "questions": []}}}}], 3),
    _msg("user", [
        {"toolResult": {"toolUseId": "tu-ask", "status": "success",
                        "content": [{"text": '사용자 답변: {"1": "A"}'}]}}], 4),
]

def test_transform_user_and_assistant_text():
    items = transform_messages(RAW)
    assert items[0] == HistoryItem(role="user", text="AI-PLC를 시작해줘")
    ai = next(i for i in items if i.role == "ai" and i.text == "환영합니다.")
    assert ai is not None  # trace 유무와 무관하게 텍스트 아이템 존재

def test_transform_ask_questions_becomes_card_and_answer_message():
    items = transform_messages(RAW)
    assert HistoryItem(role="card", card="questions", name="discovery-mode-selection") in items
    answers = [i for i in items if i.role == "user" and i.text and i.text.startswith("답변 제출")]
    assert answers and answers[0].text == "답변 제출 — 1: A"

def test_transform_skips_reasoning_and_other_tool_blocks():
    items = transform_messages(RAW)
    texts = [i.text or "" for i in items]
    assert not any("written: aiplc-docs" in t for t in texts)  # file_write toolResult 생략
    assert len(items) == 5  # user, ai(환영), ai(질문 드립니다), card, user(답변 제출...)

def test_transform_joins_multiple_text_blocks():
    raw = [_msg("assistant", [{"text": "앞"}, {"text": "뒤"}], 0)]
    assert transform_messages(raw) == [HistoryItem(role="ai", text="앞\n뒤")]

def test_transform_redacts_credentials():
    raw = [_msg("assistant", [{"text": "key AKIAIOSFODNN7EXAMPLE here"}], 0)]
    assert "AKIAIOSFODNN7EXAMPLE" not in transform_messages(raw)[0].text

@pytest.mark.asyncio
async def test_list_history_fetches_messages_concurrently():
    # 성능 회귀 가드: 81개 메시지를 순차로 읽으면 (개수 × S3 왕복)초가 걸려
    # /history가 ~5초씩 걸렸다. get()들이 실제로 "겹쳐서" 실행되는지 —
    # 최대 동시 in-flight 수가 1을 넘는지 — 를 직접 검증한다.
    import asyncio
    from tests.fakes.in_memory_s3 import FakeS3Store

    class SlowFakeS3(FakeS3Store):
        def __init__(self):
            super().__init__()
            self.in_flight = 0
            self.max_in_flight = 0

        async def get(self, key: str) -> str:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)  # S3 왕복 흉내 — 겹침이 없으면 순차 대기
            self.in_flight -= 1
            return await super().get(key)

    s3 = SlowFakeS3()
    base = "session_p1/agents/agent_default/messages"
    for n in range(10):
        s3.blobs[f"{base}/message_{n}.json"] = json.dumps(
            _msg("user", [{"text": f"m{n}"}], n))
    items = await list_history(s3, "p1")
    # 순서는 여전히 message_id 순이어야 하고,
    assert [i.text for i in items] == [f"m{n}" for n in range(10)]
    # 읽기는 병렬이어야 한다 (순차라면 max_in_flight == 1).
    assert s3.max_in_flight > 1


@pytest.mark.asyncio
async def test_list_history_reads_sorted_and_tolerates_empty():
    from tests.fakes.in_memory_s3 import FakeS3Store
    s3 = FakeS3Store()
    base = "session_p1/agents/agent_default/messages"
    s3.blobs[f"{base}/message_10.json"] = json.dumps(_msg("user", [{"text": "열번째"}], 10))
    s3.blobs[f"{base}/message_2.json"] = json.dumps(_msg("user", [{"text": "두번째"}], 2))
    items = await list_history(s3, "p1")
    assert [i.text for i in items] == ["두번째", "열번째"]  # 숫자 정렬 (문자열 정렬이면 10<2)
    assert await list_history(FakeS3Store(), "없는세션") == []


def test_transform_tolerates_questions_file_as_json_string():
    # 실 세션 회귀: Strands/LLM이 ask_questions 인자의 questions_file을 dict가
    # 아니라 직렬화된 JSON '문자열'로 넘기는 경우가 섞여 있다. dict로 가정한
    # .get("name") 호출이 AttributeError를 던지면 transform_messages 전체가
    # 죽어 list_history가 히스토리를 통째로 []로 강등했다(채팅 전체 미로딩).
    qf_str = json.dumps({"name": "envision-pain-point-gathering-questions",
                         "parse_ok": True, "preamble": "QA 팀", "questions": []},
                        ensure_ascii=False)
    raw = [
        _msg("assistant", [
            {"text": "질문 드립니다."},
            {"toolUse": {"toolUseId": "tu-s", "name": "ask_questions",
                         "input": {"questions_file": qf_str}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "tu-s", "status": "success",
                            "content": [{"text": '사용자 답변: {"1": "A"}'}]}}], 1),
    ]
    items = transform_messages(raw)
    # 카드가 파싱된 name으로 생성되고, 답변 말풍선도 정상 매칭되어야 한다.
    assert HistoryItem(role="card", card="questions",
                       name="envision-pain-point-gathering-questions") in items
    answers = [i for i in items if i.role == "user" and (i.text or "").startswith("답변 제출")]
    assert answers and answers[0].text == "답변 제출 — 1: A"


def test_transform_tolerates_unparseable_questions_file_string():
    # questions_file 문자열이 JSON이 아니어도(파싱 실패) 죽지 않고 name=None
    # 카드로 강등되어야 한다 — 여전히 전체 변환은 계속된다.
    raw = [
        _msg("assistant", [
            {"toolUse": {"toolUseId": "tu-x", "name": "ask_questions",
                         "input": {"questions_file": "not-json-just-text"}}}], 0),
    ]
    items = transform_messages(raw)
    assert HistoryItem(role="card", card="questions", name=None) in items


def test_answer_summary_is_human_readable_not_raw_json():
    raw = [
        _msg("assistant", [
            {"toolUse": {"toolUseId": "tu-a", "name": "ask_questions",
                         "input": {"questions_file": {"name": "q", "questions": []}}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "tu-a", "status": "success",
                            "content": [{"text": '사용자 답변: {"1": "A", "2": "B,C"}'}]}}], 1),
    ]
    items = transform_messages(raw)
    answer = next(i for i in items if i.role == "user")
    # raw JSON braces가 아니라 "1: A · 2: B,C" 형태
    assert answer.text == "답변 제출 — 1: A · 2: B,C"


def test_answer_summary_falls_back_to_raw_when_not_json():
    raw = [
        _msg("assistant", [
            {"toolUse": {"toolUseId": "tu-a", "name": "ask_questions",
                         "input": {"questions_file": {"name": "q", "questions": []}}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "tu-a", "status": "success",
                            "content": [{"text": "사용자 답변: 자유 서술 응답"}]}}], 1),
    ]
    items = transform_messages(raw)
    answer = next(i for i in items if i.role == "user")
    assert answer.text == "답변 제출: 자유 서술 응답"


def test_ai_items_carry_tool_trace():
    raw = [
        _msg("assistant", [
            {"text": "작업 중입니다."},
            {"toolUse": {"toolUseId": "t1", "name": "file_read",
                         "input": {"path": "aiplc-rules/x.md"}}},
            {"toolUse": {"toolUseId": "t2", "name": "report_stage",
                         "input": {"stage": "Envision", "status": "in_progress", "summary": ""}}},
            {"toolUse": {"toolUseId": "t3", "name": "file_write",
                         "input": {"path": "aiplc-docs/audit.md", "content": "x"}}},
        ], 0),
    ]
    items = transform_messages(raw)
    ai = next(i for i in items if i.role == "ai")
    assert [t.model_dump() for t in ai.trace] == [
        {"kind": "status", "text": "file_read", "path": None},
        {"kind": "status", "text": "report_stage", "path": None},
        {"kind": "file_changed", "text": None, "path": "aiplc-docs/audit.md"},
    ]


def test_trace_only_assistant_message_still_yields_ai_item():
    # 텍스트 블록 없이 도구만 부른 어시스턴트 메시지 — 라이브에서는 트레이스가
    # 붙은 빈 말풍선으로 보였던 턴. 트레이스를 잃지 않도록 text "" AI 아이템 생성.
    raw = [
        _msg("assistant", [
            {"toolUse": {"toolUseId": "t1", "name": "file_append",
                         "input": {"path": "aiplc-docs/audit.md", "content": "e"}}},
        ], 0),
    ]
    items = transform_messages(raw)
    ai = next(i for i in items if i.role == "ai")
    assert ai.text == ""
    assert [t.model_dump() for t in ai.trace] == [
        {"kind": "file_changed", "text": None, "path": "aiplc-docs/audit.md"}]


def test_ask_questions_not_duplicated_in_trace():
    raw = [
        _msg("assistant", [
            {"text": "질문 드립니다."},
            {"toolUse": {"toolUseId": "ta", "name": "ask_questions",
                         "input": {"questions_file": {"name": "q"}}}},
        ], 0),
    ]
    items = transform_messages(raw)
    ai = next(i for i in items if i.role == "ai")
    # ask_questions는 카드로 표현되므로 트레이스에는 넣지 않는다
    assert not ai.trace
    assert any(i.role == "card" for i in items)


# ---- claude 드라이버(CLI 트랜스크립트) 경로 ----
#
# Discovery가 StrandsDriver → ClaudeDriver로 바뀌면서 트랜스크립트의 위치와
# 포맷이 모두 바뀌었는데 이 모듈이 함께 옮겨오지 않았다. 위의 transform_messages는
# Bedrock Converse 모양(toolUse/toolResult, camelCase)이고, CLI는 Anthropic
# Messages 모양(block["type"] == "tool_use")이다. 프리픽스도 달라서 히스토리가
# 항상 빈 목록이었고, list_history의 except → [] 강등이 그것을 조용히 삼켰다.
from pathfinder.session_history import transform_cli_transcript
from pathfinder.agent.session_store import DiscoverySessionStore
from fakes.in_memory_s3 import FakeS3Store


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
        {"kind": "file_changed", "text": None, "path": "aiplc-docs/audit.md"},
        {"kind": "status", "text": "Read", "path": None},
    ]


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
             "content": [{"text": '사용자 답변: {"1": "A"}'}]}]),
        _cli("assistant", "assistant", [{"type": "text", "text": "작성했습니다."}]),
    ]
    items = transform_cli_transcript(raw)

    assert [i.role for i in items] == ["user", "ai", "card", "user", "ai"], \
        [(i.role, i.text) for i in items]
    assert items[1].text == "질문 드립니다."
    assert [t.text for t in items[1].trace] == ["Read"]
    assert items[2].card == "questions"
    assert items[3].text == "답변 제출 — 1: A"


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
             "content": [{"type": "text",
                          "text": '사용자 답변: {"1": "A", "2": "B"}'}]}]),
    ]
    users = [i for i in transform_cli_transcript(raw) if i.role == "user"]
    assert len(users) == 1, [i.text for i in users]
    # raw JSON을 그대로 노출하지 않는다 — Converse 경로와 같은 규칙.
    assert users[0].text == "답변 제출 — 1: A · 2: B"


def _driver_session_key(project_id: str) -> dict:
    """드라이버가 미러링에 실제로 쓰는 세션 키.

    project_id를 그대로 쓰면 안 된다 — 그것이 이 버그의 두 번째 겹이었다. CLI가
    비-UUID session-id를 거부하므로 드라이버는 uuid5를 유도해 그 값으로 쓰고,
    읽는 쪽이 원본 project_id를 쓰면 아무것도 쓰이지 않은 프리픽스를 뒤진다.
    양쪽이 같은 실수를 하면 어긋난 상태로도 테스트는 통과한다.
    """
    from pathfinder.agent.claude_driver import _sdk_session_id
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
    items = await list_history(None, "sess-1", project_s3=s3)
    assert [(i.role, i.text) for i in items] == [
        ("user", "시작해줘"), ("ai", "네, 시작합니다.")]


async def test_list_history_still_reads_the_strands_layout():
    """드라이버를 strands로 되돌렸을 때(또는 교체 전 기존 세션) 여전히 복원된다."""
    s3 = FakeS3Store()
    s3.blobs["session_sess-2/agents/agent_default/messages/message_0.json"] = \
        json.dumps(_msg("user", [{"text": "예전 대화"}], 0))
    items = await list_history(s3, "sess-2")
    assert [(i.role, i.text) for i in items] == [("user", "예전 대화")]


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
    items = await list_history(None, "sess-3", project_s3=s3)
    assert [i.text for i in items] == ["첫 턴", "두 번째 턴"]


# ---- 언어 중립 접두사 + answers 전달 ----

def test_parses_the_language_neutral_prefix():
    # strands_tools가 이제 "[answers] {...}"를 만든다.
    items = transform_messages([
        _msg("assistant", [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '[answers] {"1": "A"}'}]}}], 1),
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.answers == {"1": "A"}


def test_still_parses_the_legacy_korean_prefix():
    # 이미 S3에 있는 트랜스크립트는 옛 접두사를 쓴다 — 이것이 깨지면 진행 중인
    # 워크숍의 채팅 히스토리가 전부 빈 말풍선이 된다.
    items = transform_messages([
        _msg("assistant", [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '사용자 답변: {"1": "A"}'}]}}], 1),
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.answers == {"1": "A"}


def test_free_text_answer_has_no_answers_dict():
    # JSON이 아닌 자유 서술은 dict로 펼 수 없다. text 폴백만 남는다.
    items = transform_messages([
        _msg("assistant", [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": "[answers] 자유 서술 응답"}]}}], 1),
    ])
    answer = next(i for i in items if i.role == "user" and i.text)
    assert answer.answers is None
    assert "자유 서술 응답" in answer.text


def test_text_is_still_filled_as_a_fallback():
    # answers를 모르는 구 프론트가 빈 말풍선을 띄우지 않게 한다.
    items = transform_messages([
        _msg("assistant", [
            {"toolUse": {"toolUseId": "t1", "name": "ask_questions", "input": {}}}], 0),
        _msg("user", [
            {"toolResult": {"toolUseId": "t1",
                            "content": [{"text": '[answers] {"1": "A"}'}]}}], 1),
    ])
    answer = next(i for i in items if i.role == "user" and i.answers)
    assert answer.text and answer.text.strip() != ""
