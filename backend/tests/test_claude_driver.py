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

    # 답변 턴의 새 이터레이터가 그 메시지를 그대로 이어받아야 한다.
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


@pytest.mark.asyncio
async def test_the_final_message_of_a_turn_is_translated_only_once(tmp_path):
    # 종결 경로는 두 소스를 소진할 때까지 반복 수확한다. 한 메시지가 inbox에서
    # 정확히 한 번만 pop되지 않으면 _translate에 두 번 들어가고, 메시지가 실어 온
    # 부수효과(stage/document/file_changed)가 두 번 돈다. 출력이 아니라 _translate
    # 호출 횟수를 세는 이유가 그것이다 — 부수효과 클래스 전체를 잡는다.
    d, _, _ = _driver(tmp_path, {"text": ["본문"]})
    seen: list[str] = []
    original = d._translate

    def counting(msg):
        seen.append(type(msg).__name__)
        return original(msg)

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

    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        if ev.kind == "file_changed":
            break                      # 실패 경로의 relay 중간에 이탈
    await agen.aclose()
    await _reconnect_gap()
    assert [e.path for e in d._queue] == ["doc1.md", "doc2.md", "doc3.md"]


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
