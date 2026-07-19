import json
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

Q_PAYLOAD = json.dumps({"interrupt_id": "i-7", "questions": {"name": "q", "questions": []}})

def _sandbox_trio():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=FakeS3Store(),
    )
    return sb, ctrl, harness

def _sandbox(harness):
    """Brief-style helper: build a MicroVMSandbox around a caller-supplied
    FakeHarness, for the send_answers/pending tests below."""
    ctrl = FakeMicroVMController(base_url="http://fake")
    return MicroVMSandbox(project_id="p1", controller=ctrl, spec=BootSpec(),
                          harness_factory=lambda h: harness, s3=FakeS3Store())

async def test_start_does_not_boot():
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    assert ctrl.boot_calls == 0
    assert sb._handle is None

async def test_file_ops_do_not_boot():          # was test_first_file_op_boots_once_and_reuses
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert await sb.read_file("aiplc-docs/x.md") == "hi"
    assert ctrl.boot_calls == 0                  # file ops are pure S3 now

async def test_path_safety_rejected_before_boot():
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert ctrl.boot_calls == 0

async def test_send_message_relays_ordered_events():
    sb, _, _ = _sandbox_trio()
    await sb.start()
    events = [e async for e in sb.send_message("승인")]
    assert [e.kind for e in events] == ["message", "done"]
    assert "승인" in events[0].text

async def test_send_message_boots_the_vm():      # NEW: a turn IS what boots
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert ctrl.boot_calls == 1

async def test_concurrent_turn_gets_busy_signal():
    sb, _, _ = _sandbox_trio()
    await sb.start()
    sb._turn_active = True
    events = [e async for e in sb.send_message("second")]
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "in progress" in events[0].text

async def test_input_holder_hint_is_settable():
    sb, _, _ = _sandbox_trio()
    await sb.start()
    assert sb.input_holder is None
    sb.set_input_holder("facilitator-42")
    assert sb.input_holder == "facilitator-42"

async def test_stop_resets_to_not_booted():
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]   # boot via a turn (file ops no longer boot)
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None

async def test_stop_awaits_on_stop_callback():
    # I2: MicroVMSandbox.stop() must await an injected on_stop callback so
    # callers (app.py) can close resources they own (e.g. a shared
    # httpx.AsyncClient captured in the harness_factory closure) without
    # coupling the sandbox itself to httpx.
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    called = False
    async def _on_stop():
        nonlocal called
        called = True
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=FakeS3Store(),
        on_stop=_on_stop,
    )
    await sb.start()
    _ = [e async for e in sb.send_message("go")]   # boot, so stop() has a handle to stop
    await sb.stop()
    assert called
    assert ctrl.stop_calls == 1

async def test_stop_without_on_stop_callback_still_works():
    # Default None must not break existing constructions/tests.
    sb, ctrl, _ = _sandbox_trio()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None

# ---- send_answers/pending: sandbox owns the interrupt id (Task 7) ----

async def test_questions_event_records_interrupt_id_and_answers_resume():
    harness = FakeHarness(events_for=lambda t: [
        AgentEvent(kind="questions", payload=Q_PAYLOAD), AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("시작")]
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert harness.answer_calls == [("i-7", {"1": "A"})]
    assert evs[-1].kind == "done"

async def test_send_answers_without_pending_interrupt_errors():
    sb = _sandbox(FakeHarness())
    await sb.start()
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert evs[0].kind == "error"

async def test_send_answers_while_turn_active_yields_busy_error():
    # Mirrors test_concurrent_turn_gets_busy_signal for send_message: the
    # turn guard must also cover send_answers.
    sb = _sandbox(FakeHarness())
    await sb.start()
    sb._turn_active = True
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert len(evs) == 1
    assert evs[0].kind == "error"
    assert "in progress" in evs[0].text

async def test_send_answers_syncs_workspace_on_done():
    harness = FakeHarness(
        events_for=lambda t: [AgentEvent(kind="questions", payload=Q_PAYLOAD),
                              AgentEvent(kind="done")],
        answers_events=lambda i, a: [AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("시작")]
    harness.files["aiplc-docs/audit.md"] = "# audit"   # written "during" the resumed turn
    [e async for e in sb.send_answers({"1": "A"})]
    assert "aiplc-docs/audit.md" in sb._s3.blobs  # post-turn sync ran

async def test_pending_returns_none_when_no_live_vm():
    sb = _sandbox(FakeHarness(pending_payload=Q_PAYLOAD))
    await sb.start()
    assert await sb.pending() is None  # never boots just to ask

async def test_pending_queries_live_harness():
    harness = FakeHarness(events_for=lambda t: [AgentEvent(kind="done")],
                          pending_payload=Q_PAYLOAD)
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("부팅 유발")]
    assert await sb.pending() == Q_PAYLOAD

# ---- malformed/contract-drifted payload must degrade, not raise (hardening) ----

async def test_send_message_with_malformed_questions_payload_does_not_raise():
    harness = FakeHarness(events_for=lambda t: [
        AgentEvent(kind="questions", payload="not-json{"), AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    evs = [e async for e in sb.send_message("시작")]
    assert evs[-1].kind == "done"          # stream completes, no exception
    assert sb._pending_interrupt_id is None  # not armed on unparseable payload
    # a following send_answers has nothing to resume:
    follow = [e async for e in sb.send_answers({"1": "A"})]
    assert follow[0].kind == "error"
    assert "no pending questions" in follow[0].text

async def test_pending_with_malformed_payload_returns_it_without_raising():
    harness = FakeHarness(events_for=lambda t: [AgentEvent(kind="done")],
                          pending_payload="not-json{")
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("부팅 유발")]
    result = await sb.pending()
    assert result == "not-json{"           # returned as-is, no exception
    assert sb._pending_interrupt_id is None  # not armed
