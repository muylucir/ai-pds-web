# backend/tests/test_microvm_recovery.py
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1", controller=ctrl, spec=BootSpec(),
        harness_factory=lambda handle: harness, s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_ensure_ready_refreshes_status_not_trusting_stale_ready():
    # FINDING A: cached handle says "ready" but AWS auto-suspended it.
    sb, ctrl, harness, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("boot")]     # boots; caches handle="ready"
    assert ctrl.boot_calls == 1
    ctrl.simulate_auto_suspend(sb._handle)             # AWS suspends; cache stale
    assert sb._handle.status == "ready"                # cache is INDEED stale
    _ = [e async for e in sb.send_message("continue")] # must refresh -> resume
    assert ctrl.resume_calls == 1                      # resumed, not treated as ready
    assert ctrl.boot_calls == 1                        # NOT re-booted (only suspended)

async def test_resume_reconciles_s3_newer_writes_into_vm():
    # A write that landed in S3 while suspended must be pushed into the resumed VM.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("boot")]
    ctrl.simulate_auto_suspend(sb._handle)
    await sb.write_file("aiplc-docs/answer.md", "[Answer]: B")  # S3 only, VM stale
    assert "aiplc-docs/answer.md" not in harness.files
    _ = [e async for e in sb.send_message("continue")]          # resume -> reconcile
    assert harness.files["aiplc-docs/answer.md"] == "[Answer]: B"

async def test_ready_vm_is_reused_without_resume_or_reboot():
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("one")]
    _ = [e async for e in sb.send_message("two")]      # still ready between turns
    assert ctrl.boot_calls == 1
    assert ctrl.resume_calls == 0
