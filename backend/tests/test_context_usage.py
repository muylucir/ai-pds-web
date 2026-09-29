# backend/tests/test_context_usage.py — 컨텍스트 창 사용량(aipds/context_usage.py)과
# 그것을 내는 두 드라이버, 남기는 러너·빌드 세션, 되읽는 경로.
from __future__ import annotations

import json
import logging

from aipds.context_usage import (DISCOVERY_KEY, ContextMeter, ContextRecord,
                                 load_record, summarize, wants_sample)
from aipds.models import AgentEvent
from aipds.proto.history import context_key, load_context
from aipds.proto.limits import BuildSemaphore
from aipds.proto.session import PrototypeSession

from fakes.fake_sdk import (AssistantMessage, FakeSdkClient, ResultMessage,
                            TextBlock)
from fakes.in_memory_s3 import FakeS3Store

SLUG = "demo"


def _usage(total, maximum=200_000, buffer=None):
    """실측한 get_context_usage() 응답의 모양(2026-09-29, SDK 0.2.157)."""
    cats = [{"name": "System tools", "tokens": 10475, "color": "x"},
            {"name": "Messages", "tokens": total - 10475, "color": "x"}]
    if buffer is not None:
        cats.append({"name": "Autocompact buffer", "tokens": buffer, "color": "x"})
    return {"totalTokens": total, "maxTokens": maximum, "rawMaxTokens": maximum,
            "percentage": total * 100 // maximum if maximum else 0, "categories": cats,
            "model": "m", "isAutoCompactEnabled": True}


class _MeteredClient(FakeSdkClient):
    """get_context_usage()가 호출마다 다음 응답을 돌려준다."""

    def __init__(self, script, usages):
        super().__init__(script)
        self._usages = list(usages)
        self.probes = 0

    async def get_context_usage(self):
        self.probes += 1
        return self._usages.pop(0) if len(self._usages) > 1 else self._usages[0]


# ---- summarize: "남은 %"는 압축까지 남은 몫이다 ----

def test_left_is_measured_up_to_the_compaction_point_not_the_window_end():
    # 실측: 자동 압축 창 100000 → maxTokens 100000, 버퍼 33000. 창 끝으로 재면 압축
    # 직전에도 33%가 남은 것으로 보인다.
    got = summarize(_usage(14210, maximum=100_000, buffer=33_000))
    assert got == {"total_tokens": 14210, "max_tokens": 100_000,
                   "compact_at_tokens": 67_000, "left_pct": 78}


def test_without_a_buffer_the_whole_window_is_usable():
    assert summarize(_usage(50_000))["left_pct"] == 75


def test_past_the_compaction_point_is_zero_not_negative():
    assert summarize(_usage(90_000, maximum=100_000, buffer=33_000))["left_pct"] == 0


def test_an_unexpected_shape_is_not_a_number():
    assert summarize({"percentage": 5}) is None
    assert summarize(_usage(10, maximum=0)) is None


# ---- ContextMeter ----

async def test_only_a_changed_value_becomes_an_event():
    # 한 API 응답의 블록마다 AssistantMessage가 온다 — 같은 값을 수십 번 흘리지 않는다.
    client = _MeteredClient([], [_usage(21_000), _usage(22_000), _usage(40_000)])
    meter = ContextMeter()
    first = await meter.sample(client)
    same = await meter.sample(client)     # 89.5% → 89.0%: 화면에서는 같은 89%
    later = await meter.sample(client)

    assert json.loads(first.payload)["left_pct"] == 89
    assert same is None
    assert json.loads(later.payload)["left_pct"] == 80


async def test_a_client_without_the_api_shows_nothing():
    assert await ContextMeter().sample(FakeSdkClient()) is None


async def test_a_failing_probe_is_logged_once_and_never_raises(caplog):
    class _Boom(FakeSdkClient):
        async def get_context_usage(self):
            raise RuntimeError("control request failed")

    meter = ContextMeter()
    with caplog.at_level(logging.WARNING, logger="aipds.context_usage"):
        assert await meter.sample(_Boom()) is None
        assert await meter.sample(_Boom()) is None
    assert len(caplog.records) == 1


def test_subagent_messages_are_not_sampled():
    # 서브에이전트는 자기 컨텍스트에서 돈다. 압축이 일어나는 곳은 메인이다.
    assert wants_sample(AssistantMessage(content=[]))
    assert not wants_sample(AssistantMessage(content=[], parent_tool_use_id="toolu_1"))
    assert wants_sample(ResultMessage(subtype="success"))


# ---- ContextRecord ----

def _context_event(left):
    return AgentEvent(kind="context", payload=json.dumps({"left_pct": left}))


async def test_the_record_keeps_the_last_value_of_the_turn():
    s3 = FakeS3Store()
    rec = ContextRecord(s3, "k.json")
    rec.observe(_context_event(90))
    rec.observe(AgentEvent(kind="message", text="x"))
    rec.observe(_context_event(80))
    await rec.flush(session_id="s1")

    assert json.loads(s3.blobs["k.json"]) == {"left_pct": 80, "session_id": "s1"}


async def test_a_turn_without_a_value_does_not_rewrite_the_record():
    s3 = FakeS3Store()
    rec = ContextRecord(s3, "k.json")
    rec.observe(_context_event(90))
    await rec.flush()
    s3.blobs["k.json"] = "sentinel"
    await rec.flush()                      # 이번 턴에는 새 값이 없었다
    assert s3.blobs["k.json"] == "sentinel"


async def test_a_failed_record_write_does_not_raise():
    class _Boom(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    rec = ContextRecord(_Boom(), "k.json")
    rec.observe(_context_event(90))
    await rec.flush()


async def test_a_missing_or_broken_record_reads_as_none():
    s3 = FakeS3Store()
    assert await load_record(s3, "k.json") is None
    s3.blobs["k.json"] = "{not json"
    assert await load_record(s3, "k.json") is None


# ---- 드라이버: 메인 응답마다 재고, 턴 끝의 값은 done보다 앞선다 ----

async def test_the_build_agent_reports_usage_before_the_turn_ends(tmp_path):
    from aipds.proto.builder import PrototypeBuilder

    client = _MeteredClient(
        [AssistantMessage(content=[TextBlock(text="만드는 중")]),
         AssistantMessage(content=[TextBlock(text="하위 작업")], parent_tool_use_id="t1"),
         ResultMessage(subtype="success")],
        [_usage(20_000), _usage(40_000)])
    b = PrototypeBuilder(workspace=str(tmp_path), config_dir=str(tmp_path / "cfg"),
                         session_id="11111111-2222-3333-4444-555555555555",
                         resume=False, client_factory=lambda: client)

    events = [ev async for ev in b.run("go")]

    kinds = [e.kind for e in events]
    assert kinds[-1] == "done"
    lefts = [json.loads(e.payload)["left_pct"] for e in events if e.kind == "context"]
    assert lefts == [90, 80]
    assert client.probes == 2              # 서브에이전트 메시지 뒤에는 재지 않았다


async def test_discovery_reports_usage_before_the_turn_ends(tmp_path):
    from aipds.agent.claude_driver import ClaudeDriver

    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    (tmp_path / "ws").mkdir()
    client = _MeteredClient(
        [AssistantMessage(content=[TextBlock(text="질문을 정리합니다")]),
         ResultMessage(subtype="success")],
        [_usage(20_000), _usage(40_000)])
    d = ClaudeDriver(workspace=str(tmp_path / "ws"), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: client)

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]

    # 한 번의 대기에서 도착한 메시지들은 함께 번역되고 그 뒤에 **한 번** 잰다 —
    # 여기서는 응답과 턴 끝이 같은 묶음으로 왔다.
    kinds = [e.kind for e in events]
    assert kinds[-1] == "done"
    assert "context" in kinds and kinds.index("context") < kinds.index("done")
    assert client.probes >= 1


# ---- 남기기: Discovery 러너 ----

async def test_the_discovery_runner_records_the_turns_last_value(tmp_path):
    from aipds.runner import AgentRunner

    class _Driver:
        async def run(self, text, session):
            yield _context_event(90)
            yield _context_event(70)
            yield AgentEvent(kind="done")

    s3 = FakeS3Store()
    runner = AgentRunner("p1", _Driver(), s3, tmp_path / "ws", {"session_id": "p1"})
    [ev async for ev in runner.send_message("hi")]

    assert json.loads(s3.blobs[DISCOVERY_KEY]) == {"left_pct": 70}


# ---- 남기기와 되읽기: 프로토타입 ----

class _Builder:
    def __init__(self, events):
        self._events = events

    async def run(self, text):
        for ev in self._events:
            yield ev

    async def disconnect(self):
        pass


async def _started_session(s3, tmp_path, events):
    s3.blobs[f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"] = "# spec"
    session = PrototypeSession(project_id="p1", slug=SLUG, s3=s3,
                               build_root=tmp_path / "protos",
                               builder_factory=lambda sid, resume: _Builder(events),
                               semaphore=BuildSemaphore(max_concurrent=2))
    await session.start()
    return session


async def test_the_build_session_records_usage_with_its_sdk_session(tmp_path):
    s3 = FakeS3Store()
    session = await _started_session(
        s3, tmp_path, [_context_event(60), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    record = json.loads(s3.blobs[context_key(SLUG)])
    current = json.loads(s3.blobs[f"prototypes/{SLUG}/session.json"])["session_id"]
    assert record == {"left_pct": 60, "session_id": current}
    assert await load_context(s3, SLUG) == record
    await session.close()


async def test_a_pending_handoff_hides_the_old_sessions_usage(tmp_path):
    # 완료된 빌드를 개선하면 새 대화로 시작한다 — 닫힌 세션의 값은 틀린 값이다.
    s3 = FakeS3Store()
    session = await _started_session(
        s3, tmp_path, [_context_event(15), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]
    await session.close()
    s3.blobs[f"prototypes/{SLUG}/handoff.json"] = json.dumps({"summary": "완료"})

    assert await load_context(s3, SLUG) is None


async def test_a_record_from_an_earlier_session_is_not_shown(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[context_key(SLUG)] = json.dumps({"left_pct": 15, "session_id": "old"})
    s3.blobs[f"prototypes/{SLUG}/session.json"] = json.dumps({"session_id": "new"})

    assert await load_context(s3, SLUG) is None
