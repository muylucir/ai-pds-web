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

from aipds.agent.claude_driver import ClaudeDriver
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
async def test_an_unparseable_file_tells_the_agent_instead_of_going_silent(tmp_path):
    """파싱 실패를 **조용히 넘기지 않는다.**

    처음에는 조용히 지나가게 만들었다("차단이 아니라 열화"). 그 판단은
    AskUserQuestion이 폴백으로 살아 있을 때만 옳았다 — 그 도구가 거부되는 지금
    파싱 실패는 **질문의 완전한 소실**이다. 2026-08-17 sarang-hpt에서 정확히 그렇게
    됐다: 파일은 만들어졌고 카드는 뜨지 않았고 채팅에도 아무 말이 없었다.

    턴을 멈추지 않고 `additionalContext`로 알린다. 멈추면 모델이 그 이유를 읽고
    고칠 기회가 없어 사용자가 막힌다. 실측(2026-08-17): 모델은 이 노트를 읽고
    **같은 턴 안에서** 파일을 고쳐 다시 쓰고, 그 재작성이 훅을 다시 태워 정상
    카드가 뜬다."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, "# 제목\n\n[Answer]:\n"))
    assert out.get("continue_") is not False, "턴을 멈추면 안 된다"
    note = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    assert note, out
    # 무엇을 고쳐야 하는지 지목해야 한다 — 이유만 주면 같은 파일을 다시 쓴다.
    assert "## Question" in note
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_the_same_unparseable_content_is_not_reported_twice(tmp_path):
    """같은 내용에 같은 노트를 반복하지 않는다 — 턴 안 무한 왕복을 막는다.

    내용이 **달라지면** 다시 알린다: 모델이 고쳐 썼는데 여전히 틀린 경우가 그것이고,
    그때 침묵하면 다시 질문이 사라진다."""
    d, ws = _driver(tmp_path)
    broken = "# 제목\n\n[Answer]:\n"
    first = await _post(d, _write(ws, REL, broken))
    assert (first.get("hookSpecificOutput") or {}).get("additionalContext")
    second = await _post(d, _write(ws, REL, broken))
    assert second == {}, second
    # 다른(여전히 틀린) 내용이면 다시 알린다.
    third = await _post(d, _write(ws, REL, broken + "\n다른 내용\n"))
    assert (third.get("hookSpecificOutput") or {}).get("additionalContext")


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
async def test_enabled_by_default(tmp_path, monkeypatch):
    """**기본 켜짐이다.** 실제 Discovery 턴으로 한 바퀴 돌려 확인한 뒤 뒤집었다
    (2026-08-17): 훅이 질문 파일을 읽어 카드를 띄우고 턴이 멈추고, 답변이 파일에
    기록되고, 다음 턴에 모델이 그 답을 읽어 워크플로우를 이어갔다 — 그 마지막
    지점이 유일한 미검증 항목이었다."""
    monkeypatch.delenv("AIPDS_FILE_QUESTIONS", raising=False)
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    assert out.get("continue_") is False
    assert len(_questions_events(d)) == 1


@pytest.mark.asyncio
async def test_can_be_switched_off(tmp_path, monkeypatch):
    """탈출로는 남긴다 — env를 falsy로 두면 옛 AskUserQuestion 경로로 돌아간다.

    인스턴스에서는 user-data가 systemd `Environment=`로 값을 주입하므로 그 파일을
    고치면 인스턴스 교체가 필요하다. 대신 gitignore된 `backend/.env`를 만들면
    `pathfinder-update`가 되돌리지 않으므로(추적되지 않는 파일) 재배포 없이 끌 수
    있다."""
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, REL, QUESTION_MD))
    assert out == {}
    assert not _questions_events(d)
    # 산출물 이벤트는 여전히 나간다.
    assert any(e.kind == "file_changed" for e in d._queue)


# ---- 새로고침·재시작 복원 (GET /pending) ----
# 훅이 흘리는 `questions` 이벤트는 **라이브 한 번**뿐이다. 사용자가 카드를 받은 뒤
# 브라우저를 새로고침하면 그 이벤트는 다시 오지 않으므로, `GET /pending`이 카드를
# 되살려야 한다. 옛 경로는 `pending_store`가 그 일을 했다.
#
# **저장 기제가 문제였던 적은 없다 — 퍼지 매칭이 문제였다.** 그래서 그 기제를 쓰되
# 담는 것을 **파일 경로 하나**로 줄인다. 질문 내용은 저장하지 않고 매번 파일에서
# 다시 읽는다:
#
#   - 항상 최신이다. 에이전트가 파일을 고쳐도 카드가 따라간다.
#   - **clear 단계가 필요 없다.** `runner.write_file`이 S3에 직접 쓰므로
#     (runner.py:57-59) 답변이 기록되는 순간 S3가 최신이고, 미답 문항이 없으면
#     pending()이 자연히 None이 된다. 지울 것을 잊어 죽은 카드가 남는 경로가
#     구조적으로 없다.
#
# 경로를 저장하는 이유는 모호성이다: 실측한 프로젝트에 미답 파일이 **동시에 3개**
# 있었다(답변이 유실된 결과). 스캔만으로는 어느 라운드가 열려 있는지 알 수 없고,
# 틀린 카드를 보여주는 것은 안 보여주는 것보다 나쁘다.

@pytest.mark.asyncio
async def test_the_hook_records_which_file_is_open(tmp_path):
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    from aipds.agent.pending_store import load_pending_file
    assert await load_pending_file(d._s3) == REL


@pytest.mark.asyncio
async def test_pending_rebuilds_the_card_from_s3_after_a_refresh(tmp_path):
    """인메모리 상태가 없어도(=재시작) 복원돼야 한다."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    # 턴이 끝나면 러너가 워크스페이스를 S3로 올린다 — 그 상태를 만든다.
    await d._s3.put(REL, QUESTION_MD)
    # 새 프로세스인 척: 인메모리 pending 상태를 비운다.
    d._pending_payload = None
    payload = json.loads(await d.pending({"session_id": "s-1"}))
    assert payload["file"] == REL
    assert payload["interrupt_id"] == ""
    qs = payload["questions"]["questions"]
    assert [q["number"] for q in qs] == [1]
    assert qs[0]["ask"] == "어느 쪽입니까?"


@pytest.mark.asyncio
async def test_pending_goes_away_once_the_answers_are_in_the_file(tmp_path):
    """clear를 부르지 않는다 — 파일에서 파생되므로 답변이 곧 종료 신호다."""
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    await d._s3.put(REL, QUESTION_MD.replace("[Answer]:", "[Answer]: A"))
    d._pending_payload = None
    assert await d.pending({"session_id": "s-1"}) is None


@pytest.mark.asyncio
async def test_pending_survives_a_missing_file(tmp_path):
    """마커는 있는데 파일이 없으면(삭제·경로 변경) 조용히 None이다.

    복원은 편의이므로 500을 내지 않는다 — pending_store의 같은 규율."""
    d, _ = _driver(tmp_path)
    from aipds.agent.pending_store import save_pending_file
    await save_pending_file(d._s3, file="aiplc-docs/gone.md")
    d._pending_payload = None
    assert await d.pending({"session_id": "s-1"}) is None


# ---- AskUserQuestion을 더 이상 쓰지 않는다 ----
# 삭제가 아니라 **거부 + 대체 지시**다. 가로채기를 그냥 없애면 모델이 도구를 부른
# 순간 질문이 조용히 사라진다 — 화면에 카드도 없고 채팅에도 없다. 거부는 모델에게
# "질문 파일을 써라"를 돌려주므로 그 구멍이 생기지 않는다. `write_outside_docs`가
# 같은 패턴을 쓴다(거부만 하면 모델이 경로만 바꿔 재시도하며 루프에 빠진다).
#
# 스위치는 하나다: `AIPDS_FILE_QUESTIONS`가 켜지면 파일 경로가 유일한 경로이고,
# 꺼지면 옛 가로채기가 그대로 돈다. 두 경로가 동시에 살아 있으면 같은 질문이 화면에
# 두 번 뜬다.

def _ctx():
    class _C:
        tool_use_id = "toolu_x"
    return _C()


@pytest.mark.asyncio
async def test_ask_user_question_is_denied_when_file_questions_are_on(tmp_path):
    d, _ = _driver(tmp_path)
    result = await d._on_can_use_tool(
        "AskUserQuestion",
        {"questions": [{"question": "누구?", "header": "대상",
                        "options": [{"label": "PM"}, {"label": "PO"}]}]},
        _ctx())
    assert type(result).__name__ == "PermissionResultDeny", result
    # 대체 행동을 말해야 한다 — 거부만 하면 모델이 같은 도구를 재시도한다.
    assert "[Answer]:" in result.message
    # 그리고 질문이 사라지지 않았다: 카드는 뜨지 않고 파킹도 없다.
    assert not _questions_events(d)
    assert d._pending_question is None


@pytest.mark.asyncio
async def test_ask_user_question_still_works_when_file_questions_are_off(
        tmp_path, monkeypatch):
    """스위치가 꺼져 있으면 옛 경로가 그대로다 — 되돌릴 수 있어야 한다."""
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")
    d, _ = _driver(tmp_path)
    # **직접 await하지 않는다.** 꺼진 경로는 답변을 기다리며 future에 파킹되는 것이
    # 정상 동작이라 await하면 영원히 돌아오지 않는다(실제 SDK도 별도 태스크에서
    # 부른다 — tests/fakes/fake_sdk_asking.AskingSdkClient가 그 모양이다).
    import asyncio
    task = asyncio.create_task(d._on_can_use_tool(
        "AskUserQuestion",
        {"questions": [{"question": "누구?", "header": "대상",
                        "options": [{"label": "PM"}, {"label": "PO"}]}]},
        _ctx()))
    for _ in range(4):
        await asyncio.sleep(0)
    assert not task.done(), "파킹되지 않았다 — 옛 경로가 깨졌다"
    assert d._pending_question is not None
    assert len(_questions_events(d)) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_other_tools_are_unaffected(tmp_path):
    d, _ = _driver(tmp_path)
    result = await d._on_can_use_tool("Write", {"file_path": "x.md"}, _ctx())
    assert type(result).__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_the_question_file_is_in_s3_before_the_card_is_advertised(tmp_path):
    """훅이 파일을 **직접** S3에 올린다 — 턴 종료 sync를 기다리지 않는다.

    2026-08-17 실측한 실패: 실제 턴에서 훅이 발동해 카드가 떴는데
    `GET /pending`은 `file=None`을, 답변 제출은 404를 돌려줬다. 훅은 로컬 파일을
    읽고 마커만 S3에 쓰는데, 질문 파일 자체는 러너가 **턴이 끝난 뒤** 올리기
    때문이다(runner의 done/error sync). 그 사이가 창이다:

      - `pending()`은 마커가 가리키는 파일을 S3에서 못 찾아 None으로 떨어진다
        (새로고침하면 카드가 사라진다)
      - 답변 제출은 `runner.read_file`이 S3를 읽으므로 404가 된다

    카드를 광고하는 순간 그 파일은 이미 정본(S3)에 있어야 한다. 내용을 이미
    손에 들고 있으므로 여기서 올리는 것이 가장 싸고 확실하다 — 한 라운드에
    S3 put 하나.
    """
    d, ws = _driver(tmp_path)
    await _post(d, _write(ws, REL, QUESTION_MD))
    assert await d._s3.get(REL) == QUESTION_MD
    # 그리고 마커보다 먼저 있어야 한다 — 마커가 없는 파일을 가리키는 상태가
    # 되면 위의 두 실패가 그대로 재현된다.
    from aipds.agent.pending_store import load_pending_file
    assert await load_pending_file(d._s3) == REL


# ---- audit.md는 내용이 무엇이든 질문 파일이 아니다 (2026-08-18 실측) ----
# `core-workflow.md:303-304`가 audit.md에 대해 "**MANDATORY**: Log ALL user
# inputs ... Capture user's COMPLETE RAW INPUT exactly as provided"를 요구한다.
# 답변 라운드를 충실히 기록하면 `[Answer]: A`가 줄 맨 앞에 놓인다.
#
# 그때 이 훅이 audit.md를 질문 파일로 잡았고, 에이전트가 이렇게 반응했다:
# "audit 기록에 적어 둔 [Answer] 태그 문구가 질문 파서에 걸렸습니다 — audit.md는
# 질문 파일이 아니므로 그 표기를 없애 기록만 남기겠습니다." 즉 우리 탐지가
# **상류가 원문 보존을 요구한 감사 기록을 훼손시켰다.**

@pytest.mark.asyncio
async def test_audit_md_with_a_raw_answer_tag_does_not_ask(tmp_path):
    d, ws = _driver(tmp_path)
    audit = (
        "# AI-PLC Audit Log\n"
        "\n"
        "## 2026-08-18 — Envision Step 0.1 답변 수신 (원문)\n"
        "\n"
        "`business-context-questions.md` 1번 문항의 답변 태그 원문:\n"
        "\n"
        "## Question 1\n"
        "\n"
        "A) 자유 서술\n"
        "B) 구조화된 질문\n"
        "\n"
        # 빈 슬롯이다 — 에이전트가 방금 만든 질문 파일을 원문 그대로 옮겨 적으면
        # 이 모양이 된다. 이것이 실제로 파서에 걸린 상태다.
        "[Answer]:\n"
    )
    out = await _post(d, _write(ws, "aiplc-docs/audit.md", audit))
    assert out == {}, "감사 기록으로 턴을 멈추면 에이전트가 그 기록을 지운다"
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_aiplc_state_md_with_a_raw_answer_tag_does_not_ask(tmp_path):
    """상태 파일도 같다 — 우리 파일이고 답변을 인용할 수 있다."""
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/aiplc-state.md",
                                "# AI-PLC State\n\n## Question 1\n\nA) x\nB) y\n\n[Answer]:\n"))
    assert out == {}
    assert not _questions_events(d)


@pytest.mark.asyncio
async def test_a_quoted_answer_tag_does_not_ask(tmp_path):
    """줄 맨 앞이 아닌 인용은 어느 파일에서도 질문이 아니다.

    훅이 `"[Answer]:" in md` 단순 포함이던 동안 여기서도 걸렸다 — 되기록 쪽
    (`^` 앵커)과 판정이 갈라져 있었다.
    """
    d, ws = _driver(tmp_path)
    out = await _post(d, _write(ws, "aiplc-docs/discovery/notes.md",
                                "# 메모\n\n답변 태그는 `[Answer]: B` 꼴로 적는다.\n"))
    assert out == {}
    assert not _questions_events(d)
