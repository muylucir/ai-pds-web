import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _sandbox():
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

async def test_start_does_not_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    assert ctrl.boot_calls == 0
    assert sb._handle is None

async def test_file_ops_do_not_boot():          # was test_first_file_op_boots_once_and_reuses
    sb, ctrl, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert await sb.read_file("aiplc-docs/x.md") == "hi"
    assert ctrl.boot_calls == 0                  # file ops are pure S3 now

async def test_path_safety_rejected_before_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert ctrl.boot_calls == 0

async def test_send_message_relays_ordered_events():
    sb, _, _ = _sandbox()
    await sb.start()
    events = [e async for e in sb.send_message("승인")]
    assert [e.kind for e in events] == ["message", "done"]
    assert "승인" in events[0].text

async def test_send_message_boots_the_vm():      # NEW: a turn IS what boots
    sb, ctrl, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert ctrl.boot_calls == 1

async def test_concurrent_turn_gets_busy_signal():
    sb, _, _ = _sandbox()
    await sb.start()
    sb._turn_active = True
    events = [e async for e in sb.send_message("second")]
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "in progress" in events[0].text

async def test_input_holder_hint_is_settable():
    sb, _, _ = _sandbox()
    await sb.start()
    assert sb.input_holder is None
    sb.set_input_holder("facilitator-42")
    assert sb.input_holder == "facilitator-42"

async def test_stop_resets_to_not_booted():
    sb, ctrl, _ = _sandbox()
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
    sb, ctrl, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None
