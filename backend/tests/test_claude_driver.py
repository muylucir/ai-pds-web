# 계약(driver_contract.py) 밖의 ClaudeDriver 고유 동작.
#
# 질문 대본은 tests/fakes/fake_sdk_asking.AskingSdkClient를 쓴다 — 실제 SDK처럼
# 드라이버의 can_use_tool 콜백을 별도 태스크에서 호출하고 그 동안
# receive_response()가 아무것도 내지 않는다. 그 콜백이 questions 이벤트를 만드는
# 유일한 경로이므로, 대본에 AskUserQuestion ToolUseBlock을 넣는 가짜로는 이
# 파일의 어떤 질문 테스트도 실제 경로를 타지 못한다(자세한 근거는 그 모듈 참고).
import asyncio
import json

import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from pathfinder.agent.pending_store import PENDING_KEY, save_pending
from pathfinder.models import AgentEvent
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


@pytest.fixture(autouse=True)
def _legacy_question_path(monkeypatch):
    """이 파일은 **AskUserQuestion 경로**를 검증한다 — 그 경로는 2026-08-17에
    기본값이 뒤집혀 탈출로가 됐다(claude_driver.FILE_QUESTIONS_ENV의 주석).

    탈출로는 살아 있어야 하므로 그 검증도 살아 있어야 한다. 기본값에 의존하지 않고
    여기서 명시적으로 끄는 이유: 기본값이 바뀌었을 때 이 파일이 "조용히 다른 것을
    검증하는" 상태가 되지 않게 한다 — 실제로 그렇게 됐고, 그래서 이 픽스처가 생겼다.
    """
    monkeypatch.setenv("PATHFINDER_FILE_QUESTIONS", "false")


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


def _runner(tmp_path, scripted, s3=None):
    """The driver behind the REAL runner.AgentRunner.

    The abandonment tests below go through it rather than calling the driver
    directly, because `runner.py:144-152` is what actually abandons this
    generator in production (SSE disconnect, proxy timeout, navigation) and it
    wraps the driver in one more generator layer — which is exactly what
    `GeneratorExit` has to propagate through. A driver-only test would not
    exercise that layer.
    """
    from pathfinder.runner import AgentRunner

    s3 = s3 or FakeS3Store()
    d, ws, cap = _driver(tmp_path, scripted, s3=s3)
    runner = AgentRunner(project_id="p1", driver=d, s3=s3, local_root=ws,
                         session={"session_id": "s-1"})
    return d, runner, cap


async def _reconnect_gap() -> None:
    """The event-loop ticks a real reconnect takes.

    `aclose()` runs only the OUTERMOST generator's `finally` synchronously, so
    `GeneratorExit` needs a few ticks to reach the driver's nested generators
    (round 1's IMPORTANT 3 measured the same thing). A browser reconnect is a
    network round trip, so this is not an artificial delay.
    """
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_places_the_rules_before_the_first_turn(tmp_path):
    # 룰이 없으면 에이전트가 워크플로우를 모르는 채로 돈다.
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # CLAUDE.md는 이제 조립물이다: 언어 지시 다음에 워크플로우. 지시는 픽스처가
    # 아니라 패키지(pathfinder/agent/language/)에서 온다.
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.index("언어 규약") < text.index("WORKFLOW")


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
async def test_persists_the_submitted_answers_as_a_record(tmp_path):
    """복원이 CLI 산문에 의존하지 않게 하는 근거.

    CLI가 트랜스크립트에 남기는 답변 결과는 자기가 만든 영어 문장이고
    (`Your questions have been answered: "질문"="라벨"`), 우리 문항 번호·보기
    letter가 거기에 없다. 그래서 답변이 도착한 순간의 정확한 값을 기록한다 —
    승인 게이트에서 이미 같은 결정을 했다(08aaa85). 키는 tool_use_id이고, 그
    값으로 복원이 트랜스크립트의 tool_result와 정확히 조인한다.
    """
    s3 = FakeS3Store()
    d, _, cap = _driver(tmp_path, {"questions": True}, s3=s3)
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    [ev async for ev in d.run_answers(iid, {"1": "A", "2": "B,C"},
                                      {"session_id": "s-1"})]

    key = f"answers/{cap['client'].tool_use_id}.json"
    assert key in s3.blobs, list(s3.blobs)
    saved = json.loads(s3.blobs[key])
    assert saved["answers"] == {"1": "A", "2": "B,C"}
    assert saved["interrupt_id"] == iid
    # 질문 payload를 함께 남긴다 — 답변 값이 letter이므로 보기 텍스트로 펼치려면
    # 그 순간의 payload가 필요하다.
    assert saved["questions"]["questions"], saved["questions"]


#: fake가 묻는 질문(fake_sdk_asking.DEFAULT_SDK_QUESTIONS)과 **같은 문장**을 담은
#: 질문 파일. 되기록은 질문 텍스트로 맞추므로 이 문장이 어긋나면 매칭되지 않는다.
_QUESTION_FILE_MD = """## Question 1
다음 단계는?

A) 진행
B) 종료
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""


@pytest.mark.asyncio
async def test_answers_are_recorded_in_the_question_file(tmp_path):
    """**ai-plc 워크플로우의 핵심 계약이다.**

    상류 룰은 질문 파일을 답안지로 다룬다 — question-format-guide.md가 "Extract
    answers after [Answer]: tags"를 지시하고, session-continuity.md:31-33은
    스테이지 재개 때 그 파일을 읽으라고 한다. 답이 심기지 않으면 재개한 세션이
    사용자의 결정을 잃는다. 배선이 끊기면 에러 없이 빈 칸만 남으므로 여기서 고정한다.
    """
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    qpath = tmp_path / "ws" / "aiplc-docs" / "strategy-questions.md"
    qpath.parent.mkdir(parents=True)
    qpath.write_text(_QUESTION_FILE_MD, encoding="utf-8")

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "B"},
                                              {"session_id": "s-1"})]

    assert "[Answer]: B" in qpath.read_text(encoding="utf-8")
    # **S3에도 올라가야 한다.** runner.read_file이 S3에서 읽으므로(runner.py:55)
    # 로컬만 쓰면 화면과 다음 스테이지는 빈 칸을 본다. 게다가 매 턴 시작의
    # _restore_workspace_from_s3가 "S3가 무조건 이긴다"이므로, 턴이 종결 없이
    # 버려지면 다음 턴이 로컬의 답변을 S3의 빈 파일로 덮는다.
    key = "aiplc-docs/strategy-questions.md"
    assert key in s3.blobs, list(s3.blobs)
    assert "[Answer]: B" in s3.blobs[key]
    # 화면의 산출물 패널이 갱신되도록 file_changed도 흘린다 — 파일만 바뀌고
    # 이벤트가 없으면 사용자는 새로고침해야 답이 들어간 것을 본다.
    assert ("aiplc-docs/strategy-questions.md"
            in [e.path for e in later if e.kind == "file_changed"])


@pytest.mark.asyncio
async def test_a_missing_question_file_does_not_fail_the_answers_turn(tmp_path):
    """질문 파일이 없는 것은 정상이다(프로토타입 경로 등). 턴이 죽으면 안 된다."""
    d, _, _ = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]

    assert later and later[-1].kind == "done", [(e.kind, e.text) for e in later]
    assert not [e for e in later if e.kind == "file_changed"]


@pytest.mark.asyncio
async def test_answer_record_failure_does_not_fail_the_turn(tmp_path):
    """레코드는 복원 편의다 — S3 딸꾹질이 방금 답한 턴을 죽이면 더 나쁘다."""
    class _PutBoom(FakeS3Store):
        async def put(self, key, content):
            if key.startswith("answers/"):
                raise RuntimeError("s3 down")
            return await super().put(key, content)

    s3 = _PutBoom()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]

    assert later and later[-1].kind == "done", [(e.kind, e.text) for e in later]


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
    # 유실되면 사용자는 설명 없는 질문 카드만 본다. 예전 구조에서는 메시지 수신이
    # 취소 가능한 future였고 anyio의 send_nowait는 파킹된 수신자에게 버퍼를 거치지
    # 않고 직접 건네므로(memory.py:220-231), 그 future를 취소하는 순간 메시지가
    # 영구 소멸했다. 지금은 별도 리더 태스크가 수신을 소유한다.
    d, _, _ = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds == ["message", "questions", "done"], kinds
    assert next(e.text for e in events if e.kind == "message") == PREFACE_TEXT


@pytest.mark.asyncio
async def test_every_message_buffered_before_a_question_survives(tmp_path):
    # 위 테스트가 커버하는 건 "1개"뿐이고, 그래서 1라운드에서 완료로 보였다.
    # 한 번에 한 개만 소비하고 나머지를 버리는 구조라면 여기서 깨진다. 실측:
    # 실제 CLI의 메시지 간 간격은 3-4ms로 드라이버의 50ms 폴 안이라 한 패스에
    # 여러 개가 들어오는 건 일상이다.
    texts = ["첫 문장", "둘째 문장", "셋째 문장"]
    d, _, _ = _driver(tmp_path, {"questions": True, "preface_texts": texts})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.text for e in events if e.kind == "message"] == texts
    assert [e.kind for e in events][-2:] == ["questions", "done"]


@pytest.mark.asyncio
async def test_a_message_arriving_while_the_question_is_yielded_survives(tmp_path):
    # questions를 yield한 지점에서 드라이버는 정확히 그 yield에 매달려 있다 —
    # 위치를 추측하지 않고 소비자 쪽에서 관찰한다. 이전 라운드의 가짜는 드라이버
    # 상태를 폴링하는 술어로 이 창을 찾았는데, 그 술어는 양방향 오탐이 있었다
    # (_on_can_use_tool의 S3 저장 중에도 True, 드레인이 끝난 뒤에도 True) —
    # 그래서 테스트가 공허해져도 "창을 맞췄다"고 보고할 수 있었다.
    #
    # 프로덕션에서 이 창은 SSE 프레임 하나를 쓰는 시간이다(runner.py는 프레임마다
    # await한다). 그 사이 CLI가 보낸 메시지는 유실되면 안 된다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "questions":
            cap["client"].deliver_late("질문 yield 중 도착")
            await asyncio.sleep(0)
    kinds = [e.kind for e in events]
    texts = [e.text for e in events if e.kind == "message"]
    assert "질문 yield 중 도착" in texts, kinds
    # questions 뒤에 온다 — 그 시점 이후에 건네졌으므로 종결 전 마지막 수확만이
    # 건질 수 있다. 순서 자체가 그 수확이 동작했다는 증거다.
    assert kinds.index("questions") < kinds.index("message", 1), kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1, kinds


@pytest.mark.asyncio
async def test_a_message_arriving_during_the_post_done_sync_is_not_destroyed(tmp_path):
    # `yield done`에 매달려 있는 동안 runner.py:134-140은 실제 S3 동기화
    # (수십~수백 ms)를 await한다. 그 사이 CLI가 보낸 메시지는 예전 구조에서
    # 파킹된 수신자에게 직접 건네졌고, 소비자가 돌아온 뒤 취소에 파괴됐다.
    #
    # 해법은 타이밍이 아니라 구조다: 메시지 수신을 별도 리더 태스크가 소유하므로
    # _pump가 어디서 몇 초를 매달려 있든 도착한 메시지는 리더의 inbox에 쌓이고,
    # 같은 리더를 이어받는 답변 턴이 그것을 relay한다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "done":
            # 프로덕션이 하는 일: 종결 이벤트를 보고 S3 동기화를 await한다.
            await asyncio.sleep(0.02)
            cap["client"].deliver_late("동기화 중 도착")
            await asyncio.sleep(0.02)
    assert [e.kind for e in events] == ["message", "questions", "done"]

    # 답변 턴이 그 메시지를 그대로 이어받아야 한다 — 새 이터레이터를 여는 것이
    # *아니라*, 질문 턴의 그 리더를 그대로 이어받아서다(_continue_after_answers).
    # 이 구분이 이 파일에서 가장 값비싼 교훈이다: "새 receive_response()를 열면
    # anyio가 버퍼해둔 것을 읽을 수 있다"는 모델은 라운드 4가 반증했고
    # (send_nowait은 파킹된 수신자에게 직접 건네고 버퍼하지 않는다 — 취소하면
    # 아이템이 파괴된다), 그 믿음이 세 라운드 연속으로 메시지 유실 결함을
    # 만들었다. 지금 코드는 턴당 이터레이터를 하나만 열고(claude_driver.py:824)
    # 질문 왕복 내내 그것을 유지한다.
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    assert "동기화 중 도착" in [e.text for e in later if e.kind == "message"], \
        [(e.kind, e.text) for e in later]


@pytest.mark.asyncio
async def test_a_question_turn_yields_exactly_one_terminal_event(tmp_path):
    # 마지막 훑기가 ResultMessage를 소비하면 _translate이 done을 만들고, 그 뒤
    # 종결 yield가 두 번째 done을 낸다. 그러면 runner.py:134가 워크스페이스
    # 전체를 S3로 두 번 올리고, POST /message(turns.py:29-31) 클라이언트는
    # 종결 이벤트를 두 개 받는다.
    d, _, _ = _driver(tmp_path, {"questions": True,
                                 "result_with_question": True})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds.count("done") == 1, kinds
    assert kinds[-1] == "done"


# ---- CLI가 보고한 턴 실패 (ResultMessage.is_error) ----
#
# CLI는 턴 실패를 예외로 던지지 않는다. 자기 에러 문구("API Error: The system
# encountered an unexpected error during processing")를 AssistantMessage에 담아
# 보내고, ResultMessage에 is_error=True를 실어 보낸다. 드라이버가 그 플래그를
# 읽지 않으면 실패가 정상 답변으로 렌더된다 — 사용자는 한국어 답변에 영어 에러
# 문구가 붙은 것을 보고, 에이전트는 같은 단계를 계속 재시도하고, 우리 로그에는
# 아무것도 남지 않는다(실패를 설명하는 모든 필드가 버려지므로).

def _failing_client(scripted, can_use_tool, *, status=500):
    """실패로 끝나는 턴: 본문 메시지 뒤에 is_error ResultMessage."""
    from tests.fakes.fake_sdk import (AssistantMessage, FakeSdkClient,
                                      ResultMessage, TextBlock)
    blocks = [TextBlock(text=t) for t in scripted.get("text", [])]
    msgs = ([AssistantMessage(content=blocks)] if blocks else []) + [
        ResultMessage(is_error=True, api_error_status=status,
                      terminal_reason="completed", errors=["boom"]),
    ]
    return FakeSdkClient(msgs)


def _driver_with_failing_turn(tmp_path, scripted, *, status=500):
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    d._client_factory = lambda session: _failing_client(  # type: ignore[assignment]
        scripted, d._on_can_use_tool, status=status)
    return d


@pytest.mark.asyncio
async def test_a_cli_reported_failure_ends_the_turn_with_error_not_done(tmp_path):
    # 이 결함이 실제로 만든 증상: PR/FAQ 단계가 반복 실패하는데 화면은 정상으로
    # 보였다. sse.ts:29가 done에서 EventSource를 닫고 useWorkspaceStream.ts:154의
    # error 분기를 타지 않으므로, 실패가 성공으로 위장된다.
    d = _driver_with_failing_turn(tmp_path, {"text": ["작성하겠습니다"]})
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds[-1] == "error", kinds
    assert "done" not in kinds, kinds


@pytest.mark.asyncio
async def test_a_failed_turn_still_relays_what_the_agent_produced(tmp_path):
    # 실패해도 그 전까지 나온 본문은 사용자에게 닿아야 한다 — 에이전트가 어디까지
    # 갔는지가 재시도 판단의 근거다.
    d = _driver_with_failing_turn(tmp_path, {"text": ["요구사항을 정리했습니다"]})
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    assert "요구사항을 정리했습니다" in [e.text for e in events if e.kind == "message"]


@pytest.mark.asyncio
async def test_a_failed_turn_yields_exactly_one_terminal_event(tmp_path):
    # _pump의 불변식 1은 실패 경로에서도 유지된다: 종결 이벤트는 정확히 하나,
    # 항상 마지막. 종류만 done에서 error로 바뀐다.
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds.count("error") == 1, kinds
    assert kinds.count("done") == 0, kinds


@pytest.mark.asyncio
async def test_the_failure_text_tells_the_user_to_retry(tmp_path):
    # 흔한 원인(Bedrock 429/529)은 일시적이라 재시도가 유효하다. HTTP 상태는
    # 로그로 가고 화면에는 나오지 않는다 — 워크숍 참가자가 할 수 있는 일이 아니다.
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]}, status=429)
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    text = next(e.text for e in events if e.kind == "error")
    assert "다시 시도" in text, text
    assert "429" not in text, text


@pytest.mark.asyncio
async def test_the_api_error_status_is_logged_for_diagnosis(tmp_path, caplog):
    # 이 값이 버려지던 것이 원인 파악을 막았다. 429/529(일시적)와 500(아님)을
    # 구분할 유일한 근거다.
    import logging
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]}, status=529)
    with caplog.at_level(logging.ERROR):
        [e async for e in d.run("hi", {"session_id": "s-1"})]
    assert "529" in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_a_successful_turn_is_unaffected(tmp_path):
    # 회귀 가드: is_error가 없거나 False면 종전대로 done이다.
    d, _, _ = _driver(tmp_path, {"text": ["본문"]})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds
    assert "error" not in kinds, kinds


# ---- 중단된 턴은 실패가 아니다 ----

@pytest.mark.asyncio
async def test_an_interrupted_turn_ends_with_done_not_error(tmp_path):
    """중단은 사용자가 한 일이지 턴의 실패가 아니다.

    CLI는 중단된 턴을 ResultMessage(is_error=True,
    terminal_reason="aborted_streaming")으로 보고한다 — SDK types.py:1249-1257이
    그 두 값("aborted_streaming"/"aborted_tools")을 interrupt()로 취소된 턴의
    신호로 규정한다. is_error만 보면 "이번 턴이 실패했습니다"가 사용자가 방금
    누른 중단 버튼의 결과로 뜬다.

    실 CLI 프로브가 찾은 결함이다. 가짜 SDK 테스트가 중단 후 이 조합을
    스크립트하지 않아서 유닛 테스트로는 드러나지 않았다.
    """
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "aborted_streaming"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds
    assert not any(k == "error" for k in kinds), kinds


@pytest.mark.asyncio
async def test_aborted_tools_is_also_not_a_failure(tmp_path):
    """도구 실행 중 중단도 같다 — SDK가 두 값을 나란히 규정한다. 한쪽만
    처리하면 도구가 돌던 중 누른 중단이 여전히 실패로 뜬다."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "aborted_tools"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds


@pytest.mark.asyncio
async def test_a_genuine_failure_still_reports_error(tmp_path):
    """회귀 가드. 이 분기는 실패를 삼켰던 버그를 고친 코드다
    (claude_driver.py:888-899의 이력) — Bedrock 429/500/529와 교착된 도구는
    계속 error로 가야 한다. 중단만 예외로 빼는 것이지 분기를 무력화하는 게
    아니다."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "completed"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "error", kinds


@pytest.mark.asyncio
async def test_a_failure_without_a_terminal_reason_still_reports_error(tmp_path):
    """오래된 CLI는 terminal_reason을 보내지 않는다(SDK 문서). 그때는 판단
    근거가 is_error뿐이므로 종전대로 실패로 다룬다 — None을 "중단일 수도
    있다"로 읽으면 진짜 실패가 조용히 done으로 나간다."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "error", kinds


@pytest.mark.asyncio
async def test_the_final_message_of_a_turn_is_translated_only_once(tmp_path):
    # 종결 경로는 두 소스를 소진할 때까지 반복 수확한다. 한 메시지가 inbox에서
    # 정확히 한 번만 pop되지 않으면 _translate에 두 번 들어가고, 메시지가 실어 온
    # 부수효과(stage/document/file_changed)가 두 번 돈다. 출력이 아니라 _translate
    # 호출 횟수를 세는 이유가 그것이다 — 부수효과 클래스 전체를 잡는다.
    d, _, _ = _driver(tmp_path, {"text": ["본문"]})
    seen: list[str] = []
    original = d._translate

    def counting(msg, reader=None):
        seen.append(type(msg).__name__)
        return original(msg, reader)

    d._translate = counting  # type: ignore[method-assign]
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done"
    assert seen.count("ResultMessage") == 1, seen


@pytest.mark.asyncio
async def test_queued_tool_events_are_emitted_before_the_terminal_event(tmp_path):
    # sse.ts:29가 done 프레임에서 EventSource를 close하므로 done 뒤의 프레임은
    # onEvent에 닿지 않는다 — stage는 조용히 사라지고(useWorkspaceStream.ts:
    # 134-137이 stages에 넣지 못한다), document면 문서 패널이 낡은 채로 남는다.
    #
    # 도구는 SDK 자기 태스크에서 돌기 때문에 드라이버가 yield에 매달린 어느
    # 순간에도 emit할 수 있다. 여기서는 종결 수확이 메시지를 yield하는 그 순간에
    # emit한다 — 그 패스의 중간 드레인은 이미 지나갔으므로 종결 경로가 큐를
    # 소진할 때까지 반복하지 않으면 이 이벤트는 done 뒤로 밀리거나 사라진다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    kinds = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        kinds.append(ev.kind)
        if ev.kind == "questions":
            cap["client"].deliver_late("종결 수확이 실어 낼 메시지")
            await asyncio.sleep(0)
        elif ev.kind == "message" and len(kinds) > 1:
            # 드라이버는 지금 종결 수확의 yield에 매달려 있다.
            d._emit(AgentEvent(kind="stage", payload='{"name": "stage-1"}'))
    assert "stage" in kinds, kinds
    assert kinds.index("stage") < kinds.index("done"), kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1, kinds


@pytest.mark.asyncio
async def test_a_message_arriving_while_a_queued_tool_event_is_yielded_survives(
        tmp_path):
    # 라운드 3의 순서 수정이 만든 창: 질문과 같은 버스트에 도구 이벤트가 들어오면
    # (Write/Edit → file_changed, report_stage → stage — Discovery 턴의 정상
    # 모양이다) 드라이버는 종결 직전 드레인 안에서 yield하게 된다. 예전 구조에서는
    # 바로 앞의 훑기가 수신을 파킹해 둔 상태였으므로, 그 yield 동안 도착한 메시지가
    # 곧 취소될 수신자에게 건네져 영구 유실됐다 — SSE 프레임 하나 쓰는 시간
    # (sleep(0))만으로 재현됐고 S3 지연은 필요하지도 않았다.
    #
    # 이제 메시지 수신은 취소 가능한 future가 아니라 별도 리더 태스크가 소유하므로
    # _pump의 어떤 중단도 메시지를 파괴할 수 없다. 그 성질을 여기서 고정한다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "questions":
            d._emit(AgentEvent(kind="file_changed", path="doc.md"))
        elif ev.kind == "file_changed":
            cap["client"].deliver_late("중단 직전 도착")
            await asyncio.sleep(0)
    kinds = [e.kind for e in events]
    assert "file_changed" in kinds, kinds
    assert kinds.index("file_changed") < kinds.index("done"), kinds
    assert kinds[-1] == "done"
    # 이 턴에서 나가든 답변 턴에서 나가든 유실되지만 않으면 된다 — 유실이 곧
    # 설명 없는 질문 카드다.
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    texts = [e.text for e in list(events) + later if e.kind == "message"]
    assert "중단 직전 도착" in texts, [(e.kind, e.text) for e in events + later]


@pytest.mark.asyncio
async def test_a_message_arriving_as_the_turn_is_abandoned_survives(tmp_path):
    # runner.py:144-152는 이 경로를 일상적으로 탄다(SSE 끊김, 프록시 타임아웃,
    # 사용자 이탈). 예전 구조에서는 취소가 중단 시점에 걸리므로 마지막 장전 이후
    # 도착한 것은 이미 future에 들어와 있고 그대로 죽었다 — finally의 retire()는
    # 이미 건네진 것을 되돌릴 수 없다. 사용자가 재접속하면 GET /pending이 살아 있는
    # 질문을 주고, run_answers는 그 설명 없는 질문 카드만 보게 된다.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    async for ev in agen:
        events.append(ev)
        if ev.kind == "questions":
            cap["client"].deliver_late("중단 직전 도착")
            await asyncio.sleep(0)      # 리더가 실제로 받아 들이게 한다
            break
    await agen.aclose()                 # 소비자가 사라진다
    # 소비자가 없는 동안에도 CLI는 계속 보낸다.
    cap["client"].deliver_late("중단 이후 도착")
    await asyncio.sleep(0)

    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    texts = [e.text for e in later if e.kind == "message"]
    assert "중단 직전 도착" in texts, [(e.kind, e.text) for e in later]
    assert "중단 이후 도착" in texts, [(e.kind, e.text) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_messages_not_yet_yielded_survive_abandonment(tmp_path):
    # 라운드 4가 만든 회귀. 소유권을 anyio 버퍼에서 드라이버로 옮긴 것은 맞지만,
    # inbox 전체를 로컬 리스트로 batch pop한 뒤 yield했기 때문에 아직 yield하지
    # 않은 나머지가 제너레이터 프레임에 살았고 GeneratorExit이 그것을 파괴했다.
    # 즉 _pump이 스스로 선언한 불변식("생성기가 끝날 때 inbox에 남은 것은 다음
    # pump이 relay한다")을 batch pop이 거짓으로 만들었다.
    #
    # 실제 runner.AgentRunner를 통과시킨다 — runner.py:144-152가 이 경로를
    # 일상적으로 타고, 제너레이터가 한 겹 더 있는 것이 실제 모양이다.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "preface_texts": ["문장 1", "문장 2",
                                                          "문장 3"]})
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append(ev.text)
        break                      # 첫 프레임 직후 SSE가 끊긴다
    await agen.aclose()
    assert seen == ["문장 1"]
    # 나머지는 사라지지 않고 소유된 채로 남아 있어야 한다.
    assert [e.text for e in d._reader.outbox] == ["문장 1", "문장 2", "문장 3"]

    await _reconnect_gap()
    await runner.pending()         # 재접속: GET /pending이 질문을 준다
    later = [ev async for ev in runner.send_answers({"1": "A"})]
    relayed = [e.text for e in later if e.kind == "message"]
    for text in ("문장 2", "문장 3"):
        assert text in relayed, [(e.kind, e.text) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_queued_tool_events_not_yet_yielded_survive_abandonment(tmp_path):
    # 같은 batch-pop 형태가 drain_queue()에도 있었다(이쪽은 라운드 4 이전부터).
    # 한 yield 동안 도구가 세 개를 emit하고 소비자가 첫 개 뒤에 사라지면 두 개가
    # 죽었다. 두 루프가 같은 형태이므로 같은 규칙(배달 후에 pop)으로 함께 고친다.
    d, runner, cap = _runner(tmp_path, {"questions": True})
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        if ev.kind == "questions":
            # 한 버스트에 세 개 — MultiEdit 한 번, 또는 report_stage 연속 호출.
            for i in (1, 2, 3):
                d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))
        if ev.kind == "file_changed":
            break                  # yield 시퀀스 중간에 이탈
    await agen.aclose()
    assert ("file_changed", "doc1.md") in seen
    assert [e.path for e in d._queue] == ["doc1.md", "doc2.md", "doc3.md"]

    await _reconnect_gap()
    await runner.pending()
    later = [ev async for ev in runner.send_answers({"1": "A"})]
    paths = [e.path for e in later if e.kind == "file_changed"]
    for i in (2, 3):
        assert f"doc{i}.md" in paths, [(e.kind, e.path) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_the_error_path_does_not_strand_queued_events_either(tmp_path):
    # 실패 경로도 같은 batch-pop 형태였다. 도달 모양: 앞선 턴이 relay 중간에
    # 버려져 _queue에 항목이 소유된 채 남고(라운드 5의 수정으로 이제 가능하다),
    # 다음 턴이 query()에서 죽는다 — 그 항목들을 relay할 _pump이 아직 없으므로
    # 실패 경로의 드레인이 유일한 출구다. 여기서 batch pop하면 소비자가 중간에
    # 사라진 순간 나머지가 죽는다(실측: doc2/doc3 소멸).
    from pathfinder.runner import AgentRunner
    from tests.fakes.fake_sdk import FakeSdkClient

    class _QueryFails(FakeSdkClient):
        async def query(self, text):
            raise RuntimeError("transport died")

    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    d._client_factory = lambda session: _QueryFails([])  # type: ignore[assignment]
    runner = AgentRunner(project_id="p1", driver=d, s3=d._s3, local_root=ws,
                         session={"session_id": "s-1"})
    for i in (1, 2, 3):
        d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))

    # 세 가지를 각각 단정한다. _queue 내용만 보면 공허해진다 — relay 호출을 아예
    # 지워도 "이탈"이 일어나지 않아 루프가 끝까지 돌고, pop이 없었으니 _queue
    # 단정이 자동으로 참이 된다(실측: 두 뮤테이션 모두 37 passed).
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        if ev.kind == "file_changed":
            break                      # 실패 경로의 relay 중간에 이탈
    await agen.aclose()
    await _reconnect_gap()

    # (1) relay가 실제로 돌았다 — 소비자가 첫 항목을 받았다. 이것이 없으면
    #     "이탈이 일어났다"는 전제 자체가 무너지고 아래 단정들이 무의미해진다.
    assert seen[-1] == ("file_changed", "doc1.md"), seen
    # (2) batch pop이 아니다 — 나머지는 소유된 채로 남아 있다.
    assert [e.path for e in d._queue] == ["doc1.md", "doc2.md", "doc3.md"]
    # (3) error는 큐 뒤에 온다. sse.ts:29는 done뿐 아니라 error에서도
    #     EventSource를 close하므로, error가 먼저 나가면 뒤따르는 stage/document
    #     프레임은 클라이언트에 닿지 못하고 조용히 사라진다.
    assert "error" not in [k for k, _ in seen], seen

    # 이탈하지 않는 소비자로 한 번 더 — 순서를 끝까지 본다.
    d2, ws2, _ = _driver(tmp_path / "second", {"text": ["ok"]})
    d2._client_factory = lambda session: _QueryFails([])  # type: ignore[assignment]
    runner2 = AgentRunner(project_id="p1", driver=d2, s3=d2._s3, local_root=ws2,
                          session={"session_id": "s-1"})
    for i in (1, 2, 3):
        d2._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))
    kinds = [ev.kind async for ev in runner2.send_message("hi")]
    assert kinds == ["file_changed"] * 3 + ["error"], kinds


@pytest.mark.asyncio
async def test_the_answers_error_path_relays_the_queue_before_the_error(tmp_path):
    # _continue_after_answers의 실패 경로도 같은 모양이다(_stream 쪽과 형제).
    # 여기도 sse.ts:29가 error에서 스트림을 닫으므로 큐가 먼저 나가야 한다.
    # 별도 테스트인 이유: 위 테스트는 query() 실패라 _stream 쪽만 태우고,
    # 형제 경로에 같은 뮤테이션을 걸면 살아남았다.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "turn_continues_after_answer": True})
    ev = [e async for e in runner.send_message("hi")]
    assert any(e.kind == "questions" for e in ev)
    # 답변 턴에 들어가는 시점에 소유된 도구 이벤트들.
    for i in (1, 2, 3):
        d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))

    # 큐를 건드리지 않은 채로 예외를 내야 이 분기를 태운다. reader.error는 안 된다
    # — 그건 relay가 두 큐를 소진한 뒤에 올라오므로 큐가 이미 비어 있고, 실제로
    # 그렇게 쓴 첫 버전은 _pump의 relay로 통과해 뮤테이션이 살아남았다.
    # translate_into_outbox()는 매 패스에서 relay보다 먼저 돌므로 여기서 터지면
    # 큐가 그대로 남은 상태로 실패 경로에 들어간다(망가진 메시지 shape).
    def _boom(msg):
        raise RuntimeError("bad message shape")

    d._translate = _boom  # type: ignore[method-assign]
    cap["client"].deliver_late("아무 메시지")

    got = [(e.kind, e.path) async for e in runner.send_answers({"1": "A"})]
    kinds = [k for k, _ in got]
    assert kinds == ["file_changed"] * 3 + ["error"], got
    assert d._queue == []


@pytest.mark.asyncio
async def test_an_already_answered_question_card_is_not_re_shown(tmp_path):
    # 배달 후 pop의 대가: 이탈 시점에 배달 중이던 항목은 소유된 채로 남아 다시
    # 나간다(at-least-once). questions는 그게 문제가 된다 — 이미 답한 카드가
    # 다시 뜨고, 거기에 답하면 future가 없으니 "no pending questions"로 거절된다.
    # 그 라운드의 questions 이벤트는 이 호출이 실어 온 답변이 존재 목적이므로
    # 이행된 것이고, 유실이 아니다.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "turn_continues_after_answer": True,
                                        "preface_texts": ["문장 1"]})
    agen = runner.send_message("hi").__aiter__()
    async for _ in agen:
        break                      # questions가 아직 배달되지 않은 상태로 이탈
    await agen.aclose()
    await _reconnect_gap()
    assert any(e.kind == "questions" for e in d._queue)   # 소유된 채로 남았다

    await runner.pending()
    got = []

    async def answers():
        async for ev in runner.send_answers({"1": "A"}):
            got.append((ev.kind, ev.text))

    task = asyncio.ensure_future(answers())
    for _ in range(8):
        await asyncio.sleep(0)
    cap["client"].finish_turn()
    await asyncio.wait_for(task, 3)

    kinds = [k for k, _ in got]
    assert "questions" not in kinds, got     # 답한 카드를 다시 띄우지 않는다
    assert runner._pending_interrupt_id is None
    assert kinds[-1] == "done"
    # 그러면서도 그 턴의 본문은 여전히 나온다.
    assert "문장 1" in [t for _, t in got], got


@pytest.mark.asyncio
async def test_a_new_turn_does_not_lose_messages_to_the_previous_reader(tmp_path):
    # 리더가 턴을 넘어 살아남는다는 것은 새 턴이 시작될 때 반드시 걷어내야 한다는
    # 뜻이다. 재현 경로: 질문 턴 → 답변 턴을 중간에 버림(SSE 끊김) → future는
    # 이미 풀렸으므로 사용자의 새 메시지가 query()까지 간다. 그 시점에 옛 리더가
    # 아직 살아 있으면 두 리더가 같은 anyio 스트림에서 경쟁하고, anyio는 먼저
    # 파킹된 수신자(=옛 리더)에게 아이템을 건넨다 — 아무도 relay하지 않는 inbox다.
    #
    # 실측(걷어내지 않을 때): 새 턴은 문장 하나만 받고 ResultMessage까지 도둑맞아
    # 영원히 끝나지 않는다 — runner.py의 루프와 SSE 클라이언트가 함께 매달린다.
    d, _, cap = _driver(tmp_path, {"questions": True,
                                   "turn_continues_after_answer": True})
    ev1 = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in ev1 if e.kind == "questions"))[
        "interrupt_id"]
    reader1 = d._reader

    # 답변 턴을 relay 중간에 버린다.
    agen = d.run_answers(iid, {"1": "A"}, {"session_id": "s-1"}).__aiter__()
    cap["client"].deliver_late("답변 턴 첫 문장")
    async for _ in agen:
        break
    await agen.aclose()
    assert not reader1.task.done()          # 옛 리더가 살아 있다
    assert d._pending_question is None or d._pending_question.done()

    # 새 턴. 메시지는 query()가 나간 뒤에 도착시킨다 — 그래야 이 턴의 것이다.
    got = []

    async def turn2():
        async for ev in d.run("새 메시지", {"session_id": "s-1"}):
            got.append((ev.kind, ev.text))

    task = asyncio.ensure_future(turn2())
    for _ in range(4):
        await asyncio.sleep(0)
    cap["client"].deliver_late("새 턴 문장 1")
    cap["client"].deliver_late("새 턴 문장 2")
    cap["client"].finish_turn()
    await asyncio.wait_for(task, 3)         # 걷어내지 않으면 여기서 매달린다

    texts = [t for k, t in got if k == "message"]
    assert texts == ["새 턴 문장 1", "새 턴 문장 2"], got
    assert got[-1][0] == "done", got
    assert reader1.inbox == []              # 훔쳐간 것이 없다


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
    # 재시작 후에도 이 프로젝트의 트랜스크립트는 디스크에 남아 있다 — 그것이 이
    # 경로가 이어받을 대상이다. 그 파일을 CLI가 쓰는 자리에 놓아 재시작 직후의
    # 실제 상태를 만든다.
    from pathfinder.agent.claude_driver import _sdk_session_id, _transcript_path
    sid, _ = _sdk_session_id({"session_id": "s-1"})
    t = _transcript_path(d._config_dir, d._workspace, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("{}\n", encoding="utf-8")

    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    sent = " ".join(captured["client"].queries)
    assert "다음 단계는?" in sent
    assert "진행" in sent
    # resume 경로여야 --session-id/--resume 충돌 없이 트랜스크립트를 잇는다.
    # (resume=True를 *무조건* 넣는 것이 답이 아니라는 점이 C1의 절반이다:
    #  트랜스크립트가 없을 때의 --resume은 "No conversation found"로 exit 1이다.
    #  그 반대 방향은
    #  test_the_restart_answers_path_does_not_resume_a_missing_transcript가 잡는다.)
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
    # CLAUDE.md는 이제 조립물이다: 언어 지시 다음에 워크플로우. 지시는 픽스처가
    # 아니라 패키지(pathfinder/agent/language/)에서 온다.
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.index("언어 규약") < text.index("WORKFLOW")


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
async def test_disconnect_clears_the_s3_pending_record_too(tmp_path):
    # Task 8 carry-forward defect: disconnect() only cleared the in-memory
    # _pending_payload. pending() checks that FIRST but falls back to the S3
    # record (load_pending) when it's None -- so leaving the S3 record behind
    # made pending() advertise the dead question anyway, just via the other
    # path. The subprocess is gone with disconnect(), so no future will ever
    # resolve it.
    d, _, cap = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert await d.pending({"session_id": "s-1"}) is not None  # sanity: live

    await d.disconnect()

    assert await d.pending({"session_id": "s-1"}) is None


@pytest.mark.asyncio
async def test_a_fresh_turn_after_disconnect_does_not_re_yield_a_dead_question(
        tmp_path):
    # Task 8 carry-forward defect: if a turn was abandoned before its
    # `questions` event was delivered, that event sits UNPOPPED at the head of
    # self._queue (by design -- see `_pump`'s ownership rule). disconnect()
    # tore down the subprocess and future but never dropped it, so the next
    # turn's _pump still relayed it -- a card for a question nobody can ever
    # answer (its future died with the subprocess).
    d, _, cap = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    assert (await agen.__anext__()).kind == "message"
    assert (await agen.__anext__()).kind == "questions"
    await agen.aclose()  # abandoned before the questions event is popped
    assert any(e.kind == "questions" for e in d._queue)  # sanity: still owned

    await d.disconnect()
    assert not any(e.kind == "questions" for e in d._queue)  # dead card dropped

    # A fresh turn (new subprocess, no question in its own script) must not
    # surface the old one either.
    d._client_factory = lambda session: sdk_client_for(
        {"text": ["ok"]}, d._on_can_use_tool)
    kinds = [e.kind async for e in d.run("new message", {"session_id": "s-1"})]
    assert "questions" not in kinds, kinds
    assert kinds == ["message", "done"], kinds


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


# ---- C1: 같은 session id를 두 프로세스가 쓰면 CLI가 거절한다 ----
#
# 실측(번들 바이너리 2.1.220):
#   claude --session-id=<이미 쓴 uuid> -p hi  -> exit 1, "Session ID ... is
#                                                already in use."
#   claude --resume=<없는 uuid>        -p hi  -> exit 1, "No conversation found"
# 두 에러는 서로의 여집합이므로 어느 플래그도 무조건 쓸 수 없다. 판단 근거는
# 디스크의 트랜스크립트 파일 하나이고(그 파일을 옮기자 방금 거절당한
# --session-id가 다시 성공했다), 그래서 드라이버는 그 파일을 직접 본다.
#
# 아래 테스트들이 SessionIdCheckingSdkClient를 쓰는 이유: FakeSdkClient.connect()
# 는 no-op이라 이 결함 전체가 624개 그린 테스트에 안 보였다. 중복 session id를
# 거절할 수 없는 가짜는 아무것도 증명하지 못한다.


def _checking_driver(tmp_path, script=None, config_dir=None, workspace=None):
    """SessionIdCheckingSdkClient를 물린 드라이버 + 그 팩토리가 만든 클라이언트 목록.

    config_dir/workspace를 인자로 받는 이유: 재시작은 "새 드라이버 인스턴스,
    같은 config dir + 같은 워크스페이스"이므로 두 드라이버가 같은 디스크 상태를
    봐야 한다.
    """
    from tests.fakes.fake_sdk_asking import SessionIdCheckingSdkClient

    rules = tmp_path / "rules" / "aws-aiplc-rules"
    if not rules.exists():
        rules.mkdir(parents=True)
        (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
        # 언어 지시는 픽스처가 만들지 않는다 — `rules_dir`가 아니라 패키지
        # (pathfinder/agent/language/)에서 온다.
    ws = workspace or (tmp_path / "ws")
    ws.mkdir(parents=True, exist_ok=True)
    cfg = config_dir or (tmp_path / "cfg")
    cfg.mkdir(parents=True, exist_ok=True)

    made: list = []
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(cfg), s3=FakeS3Store(),
                     client_factory=lambda session: None)

    def factory(session):
        c = SessionIdCheckingSdkClient(session, str(cfg), str(ws),
                                       script=script)
        made.append(c)
        return c

    d._client_factory = factory  # type: ignore[assignment]
    return d, made, cfg, ws


@pytest.mark.asyncio
async def test_an_ordinary_turn_survives_a_backend_restart(tmp_path):
    # C1 그 자체. 프로세스 1이 턴을 돌리고 트랜스크립트를 남긴다. 백엔드가
    # 재시작하면(= 새 드라이버 인스턴스, 같은 config dir/워크스페이스) 평범한
    # 메시지 턴은 resume=False 기본값으로 들어오는데, session id는 project id에서
    # uuid5로 파생돼 *안정적*이므로 --session-id가 그 트랜스크립트와 충돌한다.
    # 실측: 프로세스 2/3의 모든 턴이 "agent turn failed"로 죽고, 트랜스크립트
    # 파일이 영구적이라 스스로 낫지도 않는다.
    session = {"session_id": "acme"}          # 자유 형식 project id (uuid 아님)
    d1, made1, cfg, ws = _checking_driver(tmp_path)
    first = [e.kind async for e in d1.run("hi", session)]
    assert first == ["done"], first
    assert made1[0].connected

    # 재시작: 같은 프로젝트, 같은 디스크, 새 드라이버.
    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    second = [e.kind async for e in d2.run("다시 안녕", session)]
    assert second == ["done"], second        # 예전엔 ["error"]
    # 그리고 이어받았다는 근거: --resume 쪽으로 붙었다.
    assert made2[0]._session["resume"] is True
    # 세 번째 프로세스도 같다(한 번 우연히 통과하는 게 아님).
    d3, made3, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    assert [e.kind async for e in d3.run("또", session)] == ["done"]
    assert made3[0]._session["resume"] is True


@pytest.mark.asyncio
async def test_a_deleted_and_recreated_project_still_gets_a_working_turn(tmp_path):
    # C1의 두 번째 방아쇠 — 재시작이 전혀 필요 없다. 체크리스트 §7의 마지막
    # 항목이 정확히 이 경로다: DELETE /projects/acme -> runner.stop() ->
    # disconnect(), 그리고 같은 process에서 같은 project_id로 다시 만든다.
    # 실측(수정 전): 턴 1 정상, 턴 2 "agent turn failed".
    session = {"session_id": "acme"}
    d1, _, cfg, ws = _checking_driver(tmp_path)
    assert [e.kind async for e in d1.run("hi", session)] == ["done"]
    await d1.disconnect()                     # 프로젝트 삭제

    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    assert [e.kind async for e in d2.run("다시", session)] == ["done"]
    assert made2[0]._session["resume"] is True


@pytest.mark.asyncio
async def test_the_restart_answers_path_does_not_resume_a_missing_transcript(tmp_path):
    # 반대 방향. _resume_with_answers는 resume=True를 요청하지만, 트랜스크립트가
    # 없으면(재배포가 config dir까지 재활용한 경우 — 인스턴스 교체, /opt 초기화)
    # --resume은 "No conversation found"로 exit 1이다. 호출자의 의도를 그대로
    # 믿으면 재시작 후 첫 답변이 죽는다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"}]}],
                       session_id="acme")
    d, made, cfg, ws = _checking_driver(tmp_path)
    d._s3 = s3
    events = [e.kind async for e in d.run_answers("i-1", {"1": "A"},
                                                  {"session_id": "acme"})]
    assert events[-1] == "done", events
    assert "error" not in events, events
    # 트랜스크립트가 없었으므로 fresh로 내려갔어야 한다.
    assert made[0]._session["resume"] is False


@pytest.mark.asyncio
async def test_the_answers_path_resumes_when_the_transcript_is_there(tmp_path):
    # 그리고 트랜스크립트가 있으면 반드시 이어받아야 한다 — 안 그러면 재시작 후
    # 답변이 맥락 없는 새 대화로 들어가고(§6이 검증하는 모델 이해가 무의미해진다),
    # --session-id 충돌로 죽는다.
    session = {"session_id": "acme"}
    d1, _, cfg, ws = _checking_driver(tmp_path)
    assert [e.kind async for e in d1.run("hi", session)] == ["done"]

    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"}]}],
                       session_id="acme")
    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    d2._s3 = s3
    events = [e.kind async for e in d2.run_answers("i-1", {"1": "A"}, session)]
    assert events[-1] == "done", events
    assert made2[0]._session["resume"] is True


def test_the_transcript_path_matches_the_cli_layout(tmp_path):
    # 경로 규칙은 추측이 아니라 실측이다(번들 2.1.220). 여덟 개 cwd를 넣어
    # 확인했고 CLI가 만든 디렉터리명은 전부
    # re.sub(r"[^A-Za-z0-9-]", "-", cwd)와 일치했다:
    #   /tmp/pf-probe/acme_1.2-x -> -tmp-pf-probe-acme-1-2-x
    #   /tmp/pf-e2/Acme Corp     -> -tmp-pf-e2-Acme-Corp
    #   /tmp/pf-k/한글프로젝트     -> -tmp-pf-k-------
    from pathfinder.agent.claude_driver import _transcript_path

    p = _transcript_path("/opt/pathfinder/discovery-config",
                         "/tmp/pathfinder-workspaces/acme_1.2-x",
                         "bde34f1e-bdb0-5f78-8ca2-07822c3609a0")
    assert p.parent.name == "-tmp-pathfinder-workspaces-acme-1-2-x"
    assert p.name == "bde34f1e-bdb0-5f78-8ca2-07822c3609a0.jsonl"
    assert p.parent.parent.name == "projects"

    # 한글 project id도 CLI와 같은 자리를 봐야 한다(Discovery의 project id는
    # 자유 입력이므로 흔한 경우다). 아래는 실제 CLI가 만든 디렉터리명 그대로다 —
    # `/tmp/pf-k/한글프로젝트`(6자) -> `-tmp-pf-k-------`. 인코딩이 비가역이라
    # 한글은 전부 "-"로 접힌다.
    k = _transcript_path("/cfg", "/tmp/pf-k/한글프로젝트",
                         "aaaaaaaa-1111-4222-8333-444455556666")
    assert k.parent.name == "-tmp-pf-k-------"

    # cwd가 심볼릭 링크면 CLI는 실제 경로로 인코딩한다(실측: 링크명이 아니라
    # 대상 경로 디렉터리에 트랜스크립트가 생겼다).
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _transcript_path("/cfg", str(link), "x").parent.name == \
        _transcript_path("/cfg", str(real), "x").parent.name


def test_the_transcript_probe_reads_the_file_the_cli_writes(tmp_path):
    # _transcript_exists가 "그 파일이 있느냐"만 보는지. CLI의 "already in use"
    # 검사가 정확히 이것이다 — 파일을 옮기자 같은 --session-id가 다시 성공했다.
    from pathfinder.agent.claude_driver import (
        _transcript_exists, _transcript_path,
    )

    cfg = tmp_path / "cfg"
    ws = tmp_path / "ws"
    ws.mkdir()
    sid = "bde34f1e-bdb0-5f78-8ca2-07822c3609a0"
    assert _transcript_exists(str(cfg), str(ws), sid) is False
    p = _transcript_path(str(cfg), str(ws), sid)
    p.parent.mkdir(parents=True)
    p.write_text("{}\n", encoding="utf-8")
    assert _transcript_exists(str(cfg), str(ws), sid) is True
    # 다른 세션 id는 영향받지 않는다.
    assert _transcript_exists(str(cfg), str(ws),
                              "cccccccc-1111-4222-8333-444455556666") is False
    # 디렉터리는 파일이 아니다 — is_file()이어야 하는 이유.
    p.unlink()
    p.mkdir()
    assert _transcript_exists(str(cfg), str(ws), sid) is False


# ---- I3: 실패한 connect()가 캐시를 영구히 오염시키면 안 된다 ----


@pytest.mark.asyncio
async def test_a_failed_connect_does_not_poison_the_client_cache(tmp_path):
    # 실측(수정 전): _client가 connect() await *앞에서* 대입되므로 한 번 실패하면
    # 캐시에 깨진 클라이언트가 남고, 이후 세 턴이 전부 "agent turn failed"
    # (CLIConnectionError: Not connected), 팩토리는 딱 한 번만 호출됐다. 이것이
    # C1을 "한 번의 실패"에서 "죽은 프로젝트"로 증폭시킨 장치다.
    from tests.fakes.fake_sdk import FakeSdkClient

    class _FailsOnce(FakeSdkClient):
        fail = True

        async def connect(self):
            if type(self).fail:
                raise RuntimeError("connect boom")
            await super().connect()

    made: list = []
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda s: None)

    def factory(session):
        c = _FailsOnce([__import__("tests.fakes.fake_sdk", fromlist=["x"])
                        .ResultMessage()])
        made.append(c)
        return c

    d._client_factory = factory  # type: ignore[assignment]

    first = [e.kind async for e in d.run("hi", {"session_id": "acme"})]
    assert first == ["error"]
    # 캐시가 비어 있어야 다음 턴이 새 클라이언트를 만든다.
    assert d._client is None

    _FailsOnce.fail = False
    second = [e.kind async for e in d.run("다시", {"session_id": "acme"})]
    assert second == ["done"], second        # 예전엔 ["error"]
    assert len(made) == 2, made              # 새 클라이언트를 진짜로 만들었다
    assert made[1].connected


# ---- I2: pending()이 "새로고침 후 답변"을 실제로 가능하게 하는지 ----


@pytest.mark.asyncio
async def test_a_refresh_mid_question_can_still_submit_the_answer(tmp_path):
    # 공유 계약(driver_contract.py)은 "질문이 떠 있으면 pending()이 그 라운드를
    # 돌려준다"까지만 요구한다 — 두 드라이버가 그 값을 서로 다른 곳에 보관하기
    # 때문이다. 여기서는 ClaudeDriver 쪽에서 그 값이 *무엇을 가능하게 하는지*를
    # 실제 AgentRunner로 끝까지 태운다: GET /pending -> POST /answers.
    #
    # 실패 모양: pending()이 None이면 runner._pending_interrupt_id가 심어지지
    # 않고, send_answers가 드라이버를 부르지도 않은 채 "no pending questions"로
    # 거절한다(runner.py:158-160). 즉 질문 도중 새로고침한 사용자는 답변할 방법이
    # 영구히 없다 — 40개 테스트가 전부 그린인 채로.
    d, runner, cap = _runner(tmp_path, {"questions": True})
    first = [ev.kind async for ev in runner.send_message("hi")]
    assert "questions" in first and first[-1] == "done", first

    # 새로고침: 새 SSE 연결 전에 프론트가 GET /pending을 부른다.
    runner._pending_interrupt_id = None      # 새 페이지 로드 = 인메모리 상태 없음
    payload = await runner.pending()
    assert payload is not None, "새로고침 시 질문 폼을 복원할 수 없다"
    assert runner._pending_interrupt_id is not None, \
        "pending()이 라운드를 심지 않아 send_answers가 거절할 것이다"

    later = [ev async for ev in runner.send_answers({"1": "A"})]
    kinds = [e.kind for e in later]
    assert kinds[-1] == "done", kinds
    assert not any(e.kind == "error" and e.text == "no pending questions"
                   for e in later), [(e.kind, e.text) for e in later]


# ---- 트랜스크립트 미러링: 질문에서 파킹된 턴도 S3에 남아야 한다 ----

def _captured_options(tmp_path, monkeypatch, session):
    """실제 _default_client_factory가 조립한 ClaudeAgentOptions를 붙잡는다.

    이 파일의 다른 테스트는 전부 client_factory를 주입하므로 이 경로를 타지
    않는다 — 배선이 빠져도 전부 통과한다. 실제로 그렇게 놓쳤다: session_store가
    붙어 있었는데도 워크스페이스 히스토리가 비어 있었고, 그 조합을 검사하는
    테스트가 없어서 원인이 프로덕션 로그에서만 드러났다.
    """
    from pathfinder.agent.claude_driver import _default_client_factory

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)

    driver = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store())
    _default_client_factory(driver)(session)
    return captured["options"]


def test_transcript_mirroring_does_not_wait_for_the_end_of_a_turn(tmp_path, monkeypatch):
    """미러링은 `eager`여야 한다 — Discovery의 턴은 `result`에 도달하지 않는다.

    SDK 기본값 `batched`는 `result` 메시지나 `close()`에서만 flush한다
    (claude_agent_sdk/_internal/query.py). Discovery는 질문이 뜨면 그 자리에서
    `questions` -> `done`으로 run()을 끝내고(이 모듈 상단 주석) 클라이언트를
    캐시로 살려두므로 둘 중 어느 것에도 닿지 않는다. 그러면 그 턴의 대화가 SDK
    메모리에만 남고 프로세스와 함께 사라진다 -- 실측: 로컬 트랜스크립트 47줄,
    S3 0건.
    """
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    assert options.session_store is not None, "미러링 자체가 꺼져 있다"
    assert options.session_store_flush == "eager", (
        "batched면 질문에서 끝나는 턴의 트랜스크립트가 flush되지 않는다")


async def test_parking_on_a_question_flushes_the_transcript(tmp_path):
    """질문으로 턴을 마감할 때 미러 배처를 직접 flush해야 한다.

    `eager`가 프레임마다 백그라운드 flush를 걸지만 그것은 fire-and-forget이다 --
    턴을 끝내고 SSE 응답이 닫히는 시점에 마지막 프레임이 아직 안 나갔을 수 있다.
    프로덕션에서 정확히 그 모양이었다: HTTP는 00:34:31에 200으로 끝났는데 CLI는
    00:35:50까지 계속 썼고, S3에는 아무것도 남지 않았다.
    """
    flushed = []

    class FakeBatcher:
        async def flush(self):
            flushed.append(True)

    d, _, captured = _driver(tmp_path, {"questions": True})

    kinds = []
    async for ev in d.run("hi", {"session_id": "p1"}):
        # 실제 SDK가 배처를 두는 자리와 같은 곳에, 클라이언트가 만들어진 뒤에
        # 심는다(팩토리가 부르는 가짜에는 _query가 없다).
        client = captured.get("client")
        if client is not None and not hasattr(client, "_query"):
            client._query = type(
                "Q", (), {"_transcript_mirror_batcher": FakeBatcher()})()
        kinds.append(ev.kind)
    assert "questions" in kinds and kinds[-1] == "done", kinds
    assert flushed, "질문에서 파킹된 턴의 트랜스크립트가 flush되지 않았다"


async def test_a_mirror_error_is_logged(tmp_path, caplog):
    """SDK가 미러링 실패를 알려주면 로그에 남아야 한다.

    실패한 배치는 재시도되지 않는다(at-most-once) — 이 system 메시지가
    소비자에게 오는 유일한 신호이고, 우리는 그것을 통째로 버리고 있었다. 그래서
    S3에 트랜스크립트가 없을 때 "쓰기가 실패했다"와 "쓰기가 시도되지 않았다"를
    구별할 방법이 없었다. 사용자에게는 보이지 않는다: 히스토리 내구성은 보조
    데이터이고, 진행 중인 턴을 깨뜨릴 이유가 없다.
    """
    import logging

    class FakeSystemMessage:
        subtype = "mirror_error"
        error = "S3 PutObject denied"

    FakeSystemMessage.__name__ = "SystemMessage"

    d, _, _ = _driver(tmp_path, {})
    with caplog.at_level(logging.WARNING, logger="pathfinder.agent"):
        events = d._translate(FakeSystemMessage())

    assert events == [], "미러링 실패를 사용자 이벤트로 만들면 안 된다"
    assert "mirror" in caplog.text.lower()
    assert "S3 PutObject denied" in caplog.text


# ---- 턴 중단 ----

async def test_interrupt_clears_the_pending_question_from_s3(tmp_path):
    """중단은 S3의 pending 레코드까지 지워야 한다.

    Discovery의 pending은 인메모리와 S3 양쪽에 있다(agent/pending_store.py).
    인메모리만 지우면 `GET /pending`이 답할 수 없는 질문을 복원한다 — 사용자가
    폼을 채우고 제출했는데 아무 일도 일어나지 않는다. 그 future는 중단과 함께
    버려졌기 때문이다. 프로토타입 빌더가 같은 정리를 하는 이유이고
    (proto/builder.py의 interrupt), Discovery는 durable 사본이 하나 더 있다.
    """
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert "questions" in kinds, kinds
    assert PENDING_KEY in s3.blobs, "전제: 질문이 S3에 저장돼 있다"

    await d.interrupt()

    assert PENDING_KEY not in s3.blobs
    assert d._pending_payload is None
    assert d._pending_iid is None


async def test_interrupt_without_a_live_turn_is_a_no_op(tmp_path):
    """멱등이어야 한다. 이미 끝난 턴에 대한 중단 요청은 에러가 아니고, 라우트가
    세션 유무만 보고 이 메서드를 부른다."""
    d, _, _ = _driver(tmp_path, {})
    await d.interrupt()   # 아무 턴도 돌지 않은 상태
    await d.interrupt()   # 두 번 불러도 같다


async def test_interrupt_records_that_the_turn_was_stopped(tmp_path):
    """중단 사실이 이벤트로 흘러야 화면과 트랜스크립트에 남는다.

    표시가 없으면 스크롤백을 나중에 볼 때 에이전트가 말을 마치지 못한 이유를
    알 수 없다.
    """
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]

    await d.interrupt()

    from pathfinder.agent.claude_driver import INTERRUPTED_MARKER
    assert any(e.kind == "status" and e.text == INTERRUPTED_MARKER
               for e in d._queue), d._queue


def test_interrupt_marker_is_language_neutral():
    """프론트가 이 문자열을 비교해 interrupted를 세운다
    (frontend/lib/useWorkspaceStream.ts). 한국어로 두면 UI를 번역할 때
    프론트가 중단을 인지하지 못한다 — 화면에 '중단됨' 한 줄이 안 뜨고 턴이
    성공한 것처럼 보인다.

    proto/builder.py가 이미 같은 값을 쓴다 — 두 드라이버가 어긋나면
    프론트가 경로에 따라 다르게 동작한다."""
    from pathfinder.agent.claude_driver import INTERRUPTED_MARKER
    assert INTERRUPTED_MARKER == "interrupted"
    # 사람이 읽는 문구가 아니므로 비ASCII가 없어야 한다.
    assert INTERRUPTED_MARKER.isascii()


def test_driver_places_the_project_language_directive(tmp_path):
    """드라이버가 프로젝트 언어를 place_rules에 전달한다.

    이 배선이 빠지면 모든 프로젝트가 한국어 지시로 돌고, 영어를 고른 사용자는
    영어 UI로 한국어 문서를 받는다 — 에러는 없다.
    """
    from pathfinder.agent.claude_driver import ClaudeDriver
    seen = {}

    def fake_place_rules(workspace, rules_dir, language="ko"):
        seen["language"] = language

    import pathfinder.agent.claude_driver as mod
    original = mod.place_rules
    mod.place_rules = fake_place_rules
    try:
        d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                         config_dir=str(tmp_path), s3=None,
                         language="en", session_store=None)
        assert d._place_rules() is True
        assert seen["language"] == "en"
    finally:
        mod.place_rules = original


def test_driver_defaults_to_korean(tmp_path):
    from pathfinder.agent.claude_driver import ClaudeDriver
    d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                     config_dir=str(tmp_path), s3=None, session_store=None)
    assert d._language == "ko"


# ---- Discovery 쓰기 범위 게이트 (PreToolUse) ----
# 2026-08-16: 에이전트가 워크스페이스에 `prototype/index.html`을 만들어 버렸다.
# 규칙은 discovery-config/CLAUDE.md에 산문으로만 있었고, 그 산문이 금지한 것은
# 빌드 *명령*이라 자기완결 HTML 한 장은 모든 조항을 만족하며 통과했다.
# 판정 표는 tests/test_discovery_guard.py가 덮고, 여기서는 **배선**을 고정한다.


def test_the_gate_is_wired_and_excludes_askuserquestion(tmp_path, monkeypatch):
    """**이 테스트가 질문 기능을 지킨다.**

    SDK types.py의 can_use_tool 설명: PreToolUse 훅이 *allow*를 돌려주면
    can_use_tool도 건너뛴다. 우리 AskUserQuestion 가로채기가 그 콜백에 있으므로
    matcher에 AskUserQuestion이 들어가는 순간 질문 왕복 전체가 죽는다.
    """
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    pre = (options.hooks or {}).get("PreToolUse")
    assert pre, "PreToolUse 훅이 없으면 bypassPermissions에서 아무것도 막지 못한다"
    matchers = [m.matcher for m in pre]
    assert any("Write" in (m or "") and "Bash" in (m or "") for m in matchers), matchers
    assert not any("AskUserQuestion" in (m or "") for m in matchers), matchers
    # can_use_tool은 그대로 살아 있어야 한다 — 질문이 SSE 이벤트가 되는 유일한 경로다.
    assert options.can_use_tool is not None


def _pre(driver, tool_name, tool_input):
    return driver._on_pre_tool_use(
        {"tool_name": tool_name, "tool_input": tool_input}, "t1", None)


async def test_the_gate_denies_the_html_that_caused_it(tmp_path):
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    out = await _pre(d, "Write", {"file_path": "prototype/index.html"})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["hookEventName"] == "PreToolUse"
    # 거부 이유는 모델이 읽는다 — 무엇이 걸렸는지와 어디에 쓰라는지가 있어야
    # 경로만 바꿔 재시도하는 루프에 빠지지 않는다.
    assert "prototype/index.html" in decision["permissionDecisionReason"]
    # 대안을 주되 **레이아웃을 못박지 않는다** — Path B는 슬러그 경로,
    # Path A.1은 단수 `prototype/`이 맞다(proto/layout.py). 한쪽을 못박으면
    # 다른 경로의 에이전트에게 틀린 지시가 된다.
    assert "aiplc-docs/discovery/" in decision["permissionDecisionReason"]
    assert "PROTOTYPE-" not in decision["permissionDecisionReason"]


async def test_the_gate_denies_build_and_serve_commands(tmp_path):
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    for command in ("npm run dev", "cd prototype && python3 -m http.server 8000"):
        out = await _pre(d, "Bash", {"command": command})
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command


async def test_the_gate_passes_by_returning_an_empty_dict(tmp_path):
    """통과는 빈 dict여야 한다 — "allow"를 돌려주면 can_use_tool이 건너뛰어지고
    그 콜백에 있는 AskUserQuestion 가로채기가 죽는다(위 테스트의 근거와 같다)."""
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    docs = await _pre(d, "Write",
                      {"file_path": "aiplc-docs/discovery/discovery-document.md"})
    assert docs == {}
    spec = await _pre(d, "Write", {"file_path":
        "aiplc-docs/discovery/prototypes/maint/PROTOTYPE-maint.md"})
    assert spec == {}, "슬러그 산출물이 막히면 문제가 뒤바뀐다"
    assert await _pre(d, "Bash", {"command": "ls aiplc-docs"}) == {}
    # matcher 밖의 도구도 조용히 통과한다.
    assert await _pre(d, "Read", {"file_path": "/etc/hosts"}) == {}


async def test_the_gate_speaks_the_project_language(tmp_path):
    """거부 이유는 모델 컨텍스트에 들어가는 텍스트다 — agent/prompts.py 헤더의
    규약대로 프로젝트 언어를 따라야 한다."""
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    d._language = "en"
    reason = (await _pre(d, "Write", {"file_path": "prototype/index.html"})
              )["hookSpecificOutput"]["permissionDecisionReason"]
    assert not {c for c in reason if "가" <= c <= "힣"}, reason
