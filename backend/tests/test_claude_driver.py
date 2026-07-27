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
from tests.fakes.fake_sdk_asking import cancel_pending_callbacks, sdk_client_for
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
async def test_uses_the_discovery_config_dir_not_the_prototype_one(tmp_path):
    # 공유하면 Discovery가 shadcn-design 스킬을 켠 채로 돈다.
    d, _, captured = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # client_factory에 넘어간 옵션에서 config dir을 확인한다.
    assert str(tmp_path / "cfg") == d._config_dir
