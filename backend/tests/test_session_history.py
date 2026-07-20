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
