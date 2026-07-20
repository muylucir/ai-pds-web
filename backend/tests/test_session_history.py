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
    assert HistoryItem(role="ai", text="환영합니다.") in items

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
async def test_list_history_reads_sorted_and_tolerates_empty():
    from tests.fakes.in_memory_s3 import FakeS3Store
    s3 = FakeS3Store()
    base = "session_p1/agents/agent_default/messages"
    s3.blobs[f"{base}/message_10.json"] = json.dumps(_msg("user", [{"text": "열번째"}], 10))
    s3.blobs[f"{base}/message_2.json"] = json.dumps(_msg("user", [{"text": "두번째"}], 2))
    items = await list_history(s3, "p1")
    assert [i.text for i in items] == ["두번째", "열번째"]  # 숫자 정렬 (문자열 정렬이면 10<2)
    assert await list_history(FakeS3Store(), "없는세션") == []


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
