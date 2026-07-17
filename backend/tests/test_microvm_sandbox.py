import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness

def _sandbox():
    # One shared FakeHarness so writes-then-reads roundtrip across (re)boots.
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
    )
    return sb, ctrl, harness

async def test_start_does_not_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    assert ctrl.boot_calls == 0          # lazy: no VM until first use
    assert sb._handle is None            # "not yet booted" represented cleanly

async def test_first_file_op_boots_once_and_reuses():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert ctrl.boot_calls == 1
    assert await sb.read_file("aiplc-docs/x.md") == "hi"
    assert ctrl.boot_calls == 1          # reused, not re-booted

async def test_path_safety_rejected_before_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert ctrl.boot_calls == 0          # guard runs before any control-plane call

async def test_send_message_relays_ordered_events():
    sb, _, _ = _sandbox()
    await sb.start()
    events = [e async for e in sb.send_message("승인")]
    assert [e.kind for e in events] == ["message", "done"]
    assert "승인" in events[0].text

async def test_concurrent_turn_gets_busy_signal():
    sb, _, _ = _sandbox()
    await sb.start()
    sb._turn_active = True               # simulate an in-flight turn
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
    await sb.write_file("aiplc-docs/x.md", "hi")
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None
