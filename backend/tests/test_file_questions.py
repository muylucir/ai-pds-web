# backend/tests/test_file_questions.py
#
# 질문 파일이 써지는 순간 그 파일을 **결정론적으로** 읽어 물어본다.
#
# **왜 이 경로가 생겼는가(2026-08-17 test-wf 실측).** 지금은 에이전트가 질문 파일을
# 쓴 뒤 같은 내용을 AskUserQuestion 입력으로 **다시 만든다.** 그 재생성에서 19문항 중
# 15문항(79%)이 훼손됐다:
#
#     원문 그대로            4 (21%)
#     문자 치환(한글 깨짐)  11 (58%)   ← 파일에는 없고 도구 호출에서만 발생
#     축약으로 매칭 실패     4 (21%)   ← 답변이 파일에 기록되지 않았다
#
# 참가자 화면에 실제로 이렇게 떴다: `푸로토하이프가 가장 먼저 증명해야 하는 것은
# 어느 쉘입니까?`(프로토타입/어느 쪽), `이상적인 솜루션은 어떤 모심입니까?`.
# claude-code#83033(모델이 `\uXXXX` 이스케이프를 오타냄)이고, 파일을 그대로 읽으면
# **그 실패 종류 자체가 사라진다.**
#
# 상류 계약으로 돌아가는 것이기도 하다: AI-PLC는 "에이전트가 질문 파일을 쓰고,
# 사람이 `[Answer]:`를 채우고, 에이전트가 되읽는다"를 전제한다. 이 경로는 그 계약을
# 오버라이드하지 않고 GUI로 구현한다.
#
# **왜 PostToolUse 훅인가.** 모델이 배울 새 도구가 없다 — 트리거가 상류 룰이 이미
# 지시하는 Write다. 그리고 훅은 사람을 기다리지 않는다: 즉시 `continue_: False`로
# 턴만 멈추고, 답변은 파일에 들어가고, 다음 턴이 그 파일을 읽는다. 실측한 종료
# 신호는 `terminal_reason='hook_stopped'` + `is_error=False`이므로 기존 `_translate`가
# 이미 정상 `done`으로 처리한다.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from tests.fakes.in_memory_s3 import FakeS3Store

QUESTION_MD = """# 페인 포인트 — 명확화 질문

서두.

## 모호성 1: 두 답변이 다른 문제를 겨냥함

Question 2에서는 C를 고르셨습니다.

그런데 Question 5에서는 D를 고르셨습니다.

### Clarification Question 1
어느 쪽입니까?

A) 왼쪽
B) 오른쪽
X) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)

[Answer]:
"""


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    """이 경로는 기본 꺼져 있다 — 프론트 제출 경로가 붙기 전까지 켜면 턴이 멈춘
    뒤 아무도 답변을 보낼 수 없다. 테스트는 명시적으로 켠다."""
    monkeypatch.setenv("PATHFINDER_FILE_QUESTIONS", "1")


def _driver(tmp_path: Path) -> tuple[ClaudeDriver, Path]:
    ws = tmp_path / "ws"
    (ws / "aiplc-docs" / "discovery" / "envision").mkdir(parents=True)
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    return d, ws


def _write(ws: Path, rel: str, text: str) -> dict:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"tool_name": "Write", "tool_input": {"file_path": str(p)}}


REL = "aiplc-docs/discovery/envision/pain-point-clarification-questions.md"


async def _post(d: ClaudeDriver, payload: dict) -> dict:
    return await d._on_post_tool_use(payload, "t1", None)


def _questions_events(d: ClaudeDriver) -> list:
    return [e for e in d._queue if e.kind == "questions"]


# ---- 발동 ----

@pytest.mark.asyncio
async def test_writing_a_question_file_asks_and_stops_the_turn(tmp_path):
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    # 턴을 멈춘다 — 멈추지 않으면 모델이 AskUserQuestion으로 같은 질문을 다시
    # 만들고(79% 훼손 경로) 화면에 두 번 뜬다.
    assert out.get("continue_") is False, out
    assert out.get("stopReason")
    evs = _questions_events(d)
    assert len(evs) == 1


@pytest.mark.asyncio
async def test_the_payload_carries_the_file_and_the_parsed_questions(tmp_path):
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    payload = json.loads(_questions_events(d)[0].payload)
    # `file`이 판별자다: 파킹된 턴이 없으므로 프론트는 답변을 이 파일로 PUT한다.
    assert payload["file"] == REL
    assert payload["interrupt_id"] == ""
    qs = payload["questions"]["questions"]
    assert len(qs) == 1
    assert qs[0]["number"] == 1
    # 원문 그대로다 — 여기가 이 경로의 존재 이유다.
    assert qs[0]["ask"] == "어느 쪽입니까?"
    assert [o["letter"] for o in qs[0]["options"]] == ["A", "B", "X"]
    # 그리고 "왜 이걸 묻는가"까지 간다(ef24791의 context).
    assert "Question 2에서는 C를 고르셨습니다." in qs[0]["context"]


@pytest.mark.asyncio
async def test_file_changed_is_still_emitted(tmp_path):
    """질문 파일도 산출물이다 — 문서 패널이 이 이벤트로 갱신된다."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    assert any(e.kind == "file_changed" and e.path == REL for e in d._queue)


# ---- 발동하지 않아야 하는 경우 ----

@pytest.mark.asyncio
async def test_an_ordinary_document_does_not_ask(tmp_path):
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/discovery/pain-point-analysis.md",
                                "# 분석\n\n본문이다.\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_fully_answered_file_does_not_ask(tmp_path):
    """되읽기·재작성 때 다시 묻지 않는다."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD.replace("[Answer]:", "[Answer]: A")))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_an_unparseable_file_with_answer_tags_does_not_ask(tmp_path):
    """파싱이 안 되면 물어볼 것이 없다 — 조용히 지나가고 오늘 동작으로 남는다.

    차단이 아니라 열화여야 한다: 상류 포맷은 안정적이지 않다(2026-08-17에 8파일
    중 1개가 파서를 벗어났다)."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, "# 제목\n\n[Answer]:\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_the_same_unanswered_set_is_not_asked_twice(tmp_path):
    """에이전트가 같은 파일을 다시 쓰거나 Edit해도 재발동하지 않는다."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    d._queue.clear()
    out = await _post(d, _write(ws, REL, QUESTION_MD + "\n<!-- 주석 추가 -->\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_newly_added_question_asks_again(tmp_path):
    """답변 뒤 문항이 추가되면 그것은 물어야 한다.

    재발동 가드는 파일이 아니라 **미답 문항 집합**을 기준으로 한다."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    d._queue.clear()
    answered_plus_new = QUESTION_MD.replace("[Answer]:", "[Answer]: A") + """
### Clarification Question 2
언제입니까?

A) 지금
B) 나중

[Answer]:
"""
    out = await _post(d, _write(ws, REL, answered_plus_new))
    assert out.get("continue_") is False
    qs = json.loads(_questions_events(d)[0].payload)["questions"]["questions"]
    # 파일 전체를 보낸다 — 이미 답한 문항은 채워진 채로 보여 맥락이 남는다.
    assert [(q["number"], q["answer"]) for q in qs] == [(1, "A"), (2, None)]


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_path, monkeypatch):
    """기본은 꺼짐. 프론트 제출 경로가 붙기 전에 켜지면 턴이 멈춘 뒤 아무도
    답변을 보낼 수 없다 — 되돌릴 수 있는 상태로 들어간다."""
    monkeypatch.delenv("PATHFINDER_FILE_QUESTIONS", raising=False)
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    assert out == {}
    assert not _questions_events(d)
    # 산출물 이벤트는 여전히 나간다.
    assert any(e.kind == "file_changed" for e in d._queue)
