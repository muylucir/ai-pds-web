# 계약(driver_contract.py) 밖의 ClaudeDriver 고유 동작.
#
# 질문 대본은 tests/fakes/fake_sdk_asking.AskingSdkClient를 쓴다 — 실제 SDK처럼
# 드라이버의 can_use_tool 콜백을 별도 태스크에서 호출하고 그 동안
# receive_response()가 아무것도 내지 않는다. 그 콜백이 questions 이벤트를 만드는
# 유일한 경로이므로, 대본에 AskUserQuestion ToolUseBlock을 넣는 가짜로는 이
# 파일의 어떤 질문 테스트도 실제 경로를 타지 못한다(자세한 근거는 그 모듈 참고).
import json

import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from pathfinder.agent.pending_store import PENDING_KEY, save_pending
from tests.fakes.fake_sdk_asking import (
    PREFACE_TEXT, cancel_pending_callbacks, sdk_client_for,
)
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture(autouse=True)
def _cleanup_parked_callbacks():
    """질문에 답하지 않고 끝나는 테스트는 파킹된 can_use_tool 태스크를 남긴다 —
    루프가 닫힐 때 "Task was destroyed but it is pending!"으로 새어나오므로
    각 테스트 뒤에 걷어낸다."""
    yield
    cancel_pending_callbacks()


def _driver(tmp_path, scripted, s3=None):
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    captured: dict = {}

    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=s3 or FakeS3Store(),
                     client_factory=lambda session: None)

    def factory(session):
        captured["session"] = session
        client = sdk_client_for(scripted, d._on_can_use_tool)
        captured["client"] = client
        return client

    d._client_factory = factory  # type: ignore[assignment]
    return d, ws, captured


@pytest.mark.asyncio
async def test_places_the_rules_before_the_first_turn(tmp_path):
    # 룰이 없으면 에이전트가 워크플로우를 모르는 채로 돈다.
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "WORKFLOW"


@pytest.mark.asyncio
async def test_persists_pending_questions_to_s3(tmp_path):
    # 새로고침 후 폼 복원의 근거. 인메모리 Future만으로는 재시작을 못 넘는다.
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert PENDING_KEY in s3.blobs
    saved = json.loads(s3.blobs[PENDING_KEY])
    assert saved["interrupt_id"]
    assert saved["sdk_questions"]  # 답변 되번역에 필요


@pytest.mark.asyncio
async def test_the_question_turn_ends_so_answers_can_be_submitted(tmp_path):
    # 질문 턴은 questions 다음에 반드시 종결 이벤트로 끝나야 한다. 두 가지가
    # 여기에 걸려 있다: 프론트는 스트림이 열려 있는 동안 답변 제출을 거부하고
    # (useWorkspaceStream.ts:230), runner는 done/error에서만 워크스페이스를
    # S3로 올린다(runner.py:134-140) — 질문에 걸려 멈춘 턴은 에이전트가 이미
    # 쓴 파일을 휘발성 로컬에만 남긴다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert "questions" in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_the_prose_before_a_question_is_not_lost(tmp_path):
    # 실제 CLI는 모델의 "왜 묻는지" 설명(assistant 메시지)과 control_request를
    # 읽기 루프 한 패스에서 연달아 쓴다(query.py:250-322) — 사이에 모델 지연이
    # 없다. 그리고 driver.py의 _CONTACT_ADDENDUM:44-45는 질문 전에 설명을
    # 요구한다. 그래서 이건 드문 레이스가 아니라 매번 일어나는 정상 경로다.
    #
    # asyncio.wait는 타임아웃에 done=∅을 주지만 같은 tick에 메시지가 도착할 수
    # 있고, 읽지 않고 return하면 finally의 cancel이 그 메시지를 버린다 —
    # anyio의 send_nowait는 파킹된 수신자에게 버퍼를 거치지 않고 직접 건네므로
    # (memory.py:210-217) 새 이터레이터에도 남아 있지 않다. 영구 유실이다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds == ["message", "questions", "done"], kinds
    assert next(e.text for e in events if e.kind == "message") == PREFACE_TEXT


@pytest.mark.asyncio
async def test_answers_reach_the_sdk_as_the_tool_result(tmp_path):
    # 정상 왕복(같은 프로세스): 대기 중인 future를 풀어 답변이 AskUserQuestion의
    # 도구 결과로 모델에 간다. 번호→글자 답변이 SDK 라벨로 되번역돼야 모델이
    # 무엇이 선택됐는지 안다.
    d, _, captured = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    payload = json.loads(next(e.payload for e in events if e.kind == "questions"))
    iid = payload["interrupt_id"]
    queries_after_run = list(captured["client"].queries)

    kinds = [e.kind async for e in d.run_answers(iid, {"1": "B"},
                                                {"session_id": "s-1"})]
    assert kinds[-1] == "done"
    # AskingSdkClient가 콜백의 반환값을 기록해 둔다 — 이벤트 스트림이 아니라
    # 실제로 SDK에 돌려준 updated_input을 본다.
    allow = captured["client"].permission_results[0]
    assert allow.updated_input["answers"] == {"다음 단계는?": "종료"}
    # 이 경로는 새 query()를 보내지 않는다 — 턴은 아직 진행 중이고 CLI는
    # permission 응답만 기다리고 있었다. 여기서 query()를 또 보내면 모델에
    # 사용자 메시지가 하나 더 들어가 턴이 중복된다.
    assert captured["client"].queries == queries_after_run


@pytest.mark.asyncio
async def test_a_second_message_while_a_question_is_parked_re_asks(tmp_path):
    # 질문이 파킹된 동안 CLI는 permission 응답을 기다리며 막혀 있다 — 그 상태에서
    # query()를 보내면 응답이 오지 않아 턴이 허공을 폴링한다. 모델을 부르지 않고
    # 질문을 다시 띄운다(StrandsDriver의 B1 단축과 같은 판단).
    d, _, captured = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    before = len(captured["client"].queries)

    events = [ev async for ev in d.run("또 다른 말", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds == ["message", "questions", "done"]
    assert len(captured["client"].queries) == before  # 모델을 부르지 않았다


@pytest.mark.asyncio
async def test_pending_reads_from_s3_after_a_restart(tmp_path):
    # 인메모리 상태가 전혀 없는 새 드라이버 — 백엔드 재시작을 재현한다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "q", "options": []}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    payload = await d.pending({"session_id": "s-1"})
    assert payload is not None
    assert json.loads(payload)["interrupt_id"] == "i-1"


@pytest.mark.asyncio
async def test_clears_pending_after_answers_are_submitted(tmp_path):
    # 남아 있으면 새로고침 시 답변 불가한 옛 폼이 뜬다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "질문",
                                       "options": [{"label": "예"}]}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    assert PENDING_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_resumes_with_the_answer_as_text_when_the_future_is_gone(tmp_path):
    # 재시작 후 답변: 기다리던 Future가 없으므로 resume + 텍스트 턴으로 전달한다.
    # 프롬프트에 질문과 고른 라벨이 함께 들어가야 모델이 맥락을 잇는다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"},
                                                   {"label": "종료"}]}],
                       session_id="s-1")
    d, _, captured = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    sent = " ".join(captured["client"].queries)
    assert "다음 단계는?" in sent
    assert "진행" in sent
    # resume 경로여야 --session-id/--resume 충돌 없이 트랜스크립트를 잇는다.
    assert captured["session"]["resume"] is True


@pytest.mark.asyncio
async def test_the_answer_record_echoes_the_received_values_not_stored_ones(tmp_path):
    # 계약의 echo_answers 검사가 정직한지를 여기서 증명한다. 계약 어댑터는
    # 라운드 검증 때문에 심는 interrupt_id를 계약이 넘기는 값과 같게 둬야 하므로
    # "받은 값 vs 저장된 값"을 구분할 수 없다 — 그 구분을 이 테스트가 한다.
    #
    # 두 값을 저장된 것과 모두 다르게 만든다: interrupt_id는 (라운드 검증을
    # 통과해야 하므로) 저장된 것과 같되 answers는 저장된 대본과 무관한 값으로,
    # 그리고 answers 키/값 둘 다 계약이 쓰는 {"1": "A"}가 아닌 것으로 한다.
    # 드라이버가 어느 쪽이든 하드코딩하면 여기서 깨진다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-round-7",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "첫 질문",
                                       "options": [{"label": "가"}, {"label": "나"}]},
                                      {"question": "둘째 질문",
                                       "options": [{"label": "다"}, {"label": "라"}]}],
                       session_id="s-1")
    d, _, cap = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    submitted = {"2": "B", "1": "A"}
    [ev async for ev in d.run_answers("i-round-7", submitted,
                                      {"session_id": "s-1"})]

    record = json.loads(cap["client"].queries[-1].rsplit("\n", 1)[-1])
    assert record["interrupt_id"] == "i-round-7"
    assert record["answers"] == submitted     # 하드코딩이면 실패
    # 사람이 읽는 줄도 저장된 질문/라벨로 되번역돼야 한다.
    sent = cap["client"].queries[-1]
    assert "둘째 질문 → 라" in sent
    assert "첫 질문 → 가" in sent


@pytest.mark.asyncio
async def test_answers_without_any_pending_question_are_refused(tmp_path):
    # 계약 문자열. pending 레코드도 future도 없으면 되살릴 맥락이 없다.
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    events = [ev async for ev in d.run_answers("i-gone", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"


@pytest.mark.asyncio
async def test_a_stale_interrupt_id_does_not_answer_the_live_question(tmp_path):
    # 옛 탭이 지나간 라운드의 답을 보내는 경우. 살아 있는 future를 그것으로 풀면
    # 모델이 다른 질문의 답을 받는다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    events = [ev async for ev in d.run_answers("i-stale", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"
    assert await d.pending({"session_id": "s-1"}) is not None  # 질문은 살아 있다


@pytest.mark.asyncio
async def test_a_pending_s3_failure_does_not_kill_the_turn(tmp_path):
    # pending 영속은 복원 편의다. 그것 때문에 진행 중인 질문을 잃는 게 더 큰 손실.
    class _Broken(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    d, _, _ = _driver(tmp_path, {"questions": True}, s3=_Broken())
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert "questions" in kinds
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_a_missing_rules_dir_is_reported_as_a_turn_failure(tmp_path):
    # place_rules는 룰이 없으면 FileNotFoundError를 낸다 — 조용히 진행하면
    # 에이전트가 워크플로우를 모르는 채로 돈다. 계약 문자열로 강등한다.
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    d._rules_dir = str(tmp_path / "does-not-exist")
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "agent turn failed"


@pytest.mark.asyncio
async def test_places_the_rules_on_the_restart_answers_path_too(tmp_path):
    # 이 경로가 워크스페이스가 확실히 비어 있는 유일한 경로다: future가 없다는
    # 것은 백엔드가 재배포됐다는 뜻이고, runner.py:36은 aiplc-docs/, prototype/,
    # uploads/만 복원한다 — 룰은 절대 복원하지 않는다. 재시작 직후 첫 행동이
    # 워크플로우 없이 돌면 안 된다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "Q",
                                       "options": [{"label": "진행"}]}],
                       session_id="s-1")
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    assert not (ws / "CLAUDE.md").exists()  # 차갑게 시작한다
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "WORKFLOW"


@pytest.mark.asyncio
async def test_a_stale_interrupt_id_is_refused_on_the_restart_path_too(tmp_path):
    # 옛 탭이 지나간 라운드의 답을 재시작 후에 보내는 경우. 가드가 없으면 저장된
    # 현재 질문을 엉뚱한 답으로 답해버리고, 나가는 길에 진짜 pending 레코드까지
    # 지운다 — 살아 있는 폼이 답변 불가가 된다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-CURRENT",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "NEW question",
                                       "options": [{"label": "진행"}]}],
                       session_id="s-1")
    d, _, cap = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    events = [ev async for ev in d.run_answers("i-STALE-FROM-OLD-TAB", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"
    assert "client" not in cap                # 모델을 부르지 않았다
    assert PENDING_KEY in s3.blobs            # 진짜 질문은 살아 있다


@pytest.mark.asyncio
async def test_an_abandoned_turn_frees_the_slot_immediately(tmp_path):
    # runner.py:144-152는 이 경로를 일상적으로 탄다(SSE 끊김, 프록시 타임아웃,
    # 사용자 이탈). runner는 자기 _turn_active를 같은 finally에서 동기적으로
    # 지우므로, 우리 쪽이 tick을 더 먹으면 바로 다음 tick에 재접속한 브라우저의
    # 재시도가 "turn already in progress"로 튕긴다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    await agen.__anext__()
    await agen.aclose()
    assert d._turn_active is False   # aclose() 직후, 추가 tick 없이

    # 재시도가 실제로 받아들여진다.
    kinds = [e.kind async for e in d.run("retry", {"session_id": "s-1"})]
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_a_concurrent_turn_is_still_rejected(tmp_path):
    # 슬롯 소유권을 run/run_answers로 올렸어도 계약 문자열은 유지된다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    await agen.__anext__()
    try:
        events = [ev async for ev in d.run("동시", {"session_id": "s-1"})]
        assert [e.kind for e in events] == ["error"]
        assert events[0].text == "turn already in progress"
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_disconnect_tears_down_the_subprocess_and_clears_pending(tmp_path):
    # runner.stop()은 rmtree만 한다 — disconnect가 없으면 삭제된 프로젝트마다
    # claude 서브프로세스가 샌다. 파킹된 질문도 함께 정리해야 pending()이 영원히
    # 답할 수 없는 질문을 광고하지 않는다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert await d.pending({"session_id": "s-1"}) is not None

    await d.disconnect()
    assert cap["client"].disconnect_calls == 1
    assert d._client is None
    assert d._pending_payload is None
    await d.disconnect()  # 멱등


@pytest.mark.asyncio
async def test_uses_the_discovery_config_dir_not_the_prototype_one(tmp_path):
    # 공유하면 Discovery가 shadcn-design 스킬을 켠 채로 돈다.
    d, _, captured = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # client_factory에 넘어간 옵션에서 config dir을 확인한다.
    assert str(tmp_path / "cfg") == d._config_dir


# ---- SDK session id: CLI는 UUID만 받는다 ----
# 실측: `claude --session-id=pilot1 -p hi` → "Error: Invalid session ID. Must be
# a valid UUID.", exit 1. app.py:255는 session_id로 project_id를 그대로 쓰고
# 그것은 자유 입력이므로(routes/projects.py의 CreateProject가 검증하지 않는다)
# "pilot1" 같은 프로젝트는 매 턴 connect()에서 죽는다.

def test_a_non_uuid_session_id_becomes_a_stable_uuid():
    import uuid

    from pathfinder.agent.claude_driver import _sdk_session_id

    sid, resume = _sdk_session_id({"session_id": "pilot1", "resume": True})
    uuid.UUID(sid)                       # CLI가 받아들이는 형태
    assert sid != "pilot1"
    assert resume is True
    # 재시작 후에도 같아야 --resume이 트랜스크립트를 찾는다.
    again, _ = _sdk_session_id({"session_id": "pilot1", "resume": True})
    assert again == sid
    # 다른 프로젝트는 다른 id.
    other, _ = _sdk_session_id({"session_id": "pilot2", "resume": True})
    assert other != sid


def test_an_already_uuid_session_id_is_passed_through():
    import uuid

    from pathfinder.agent.claude_driver import _sdk_session_id

    given = str(uuid.uuid4())
    sid, resume = _sdk_session_id({"session_id": given, "resume": True})
    assert sid == given          # 제대로 하는 호출자를 덮어쓰지 않는다
    assert resume is True


def test_a_missing_session_id_does_not_claim_to_resume():
    import uuid

    from pathfinder.agent.claude_driver import _sdk_session_id

    sid, resume = _sdk_session_id({"resume": True})
    uuid.UUID(sid)
    assert resume is False       # 이어받을 트랜스크립트가 없다
