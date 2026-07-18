# backend/tests/test_microvm_recovery.py
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
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

async def test_restore_from_s3_rejects_unsafe_key():
    # A ".."-bearing key under the restore prefix (however it got into S3)
    # must never reach harness.write_file — reject_unsafe guards every path
    # at the last boundary before it becomes a live-VM filesystem path.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    s3.blobs["aiplc-docs/../escape.md"] = "payload"   # unsafe key, sneaked into S3
    with pytest.raises(ValueError):
        await sb._restore_workspace_from_s3(harness)
    assert harness.files == {}                         # never written

async def test_sync_to_s3_rejects_unsafe_key():
    # A ".."-bearing key listed from the VM FS (however it got written there)
    # must never reach S3 — this is exactly the vector by which an unsafe key
    # could land in S3 in the first place, so the sync direction is guarded too.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    harness.files["aiplc-docs/../escape.md"] = "payload"  # unsafe key, in VM FS
    with pytest.raises(ValueError):
        await sb._sync_workspace_to_s3(harness)
    assert s3.blobs == {}                              # never synced

async def test_expiry_midsession_recovers_with_full_restore():
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    # A turn produced durable state (synced to S3 by Task 4).
    _ = [e async for e in sb.send_message("boot")]
    await sb.write_file("aiplc-docs/aiplc-state.md", "stage: Solution Analysis")
    await sb.write_file("aiplc-docs/audit.md", "40 entries")
    first_vm = sb._handle.vm_id
    # The VM expires (8h cap) or crashes.
    ctrl.simulate_expiry(sb._handle)
    # Next turn: transparent recovery — fresh boot + full restore from S3.
    _ = [e async for e in sb.send_message("계속 진행")]
    assert ctrl.boot_calls == 2                     # a NEW VM was booted
    assert sb._handle.vm_id != first_vm             # genuinely fresh, not the dead one
    # The fresh VM's FS was fully restored from durable S3:
    assert harness.files["aiplc-docs/aiplc-state.md"] == "stage: Solution Analysis"
    assert harness.files["aiplc-docs/audit.md"] == "40 entries"

async def test_recovery_restores_state_file_for_the_rule_to_resume():
    # The backend restores aiplc-state.md verbatim; the session-continuity RULE
    # (running in the fresh VM) reads it and resumes. Backend does NOT parse it.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/aiplc-state.md", "stage: Envision\nnext: PR/FAQ")
    _ = [e async for e in sb.send_message("boot")]  # boot -> restore pushes state in
    assert harness.files["aiplc-docs/aiplc-state.md"] == "stage: Envision\nnext: PR/FAQ"

def test_backend_has_no_methodology_resume_logic():
    # Lock the "no resume logic in the backend" invariant: the sandbox exposes
    # no state-machine/resume entry points — recovery is a blind file copy.
    for forbidden in ("resume_from_state", "parse_state", "advance_stage", "_continue_session"):
        assert not hasattr(MicroVMSandbox, forbidden)

async def test_warm_ready_vm_reconciles_from_s3_before_each_turn():
    # C1: a VM that stays "ready" between turns (no auto-suspend) must still
    # get S3's writes pushed in before the next turn -- otherwise the agent
    # reads a stale VM-local file when a facilitator route wrote an answer
    # straight to S3 while the VM stayed warm.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("boot")]       # boots; VM stays ready
    assert ctrl.boot_calls == 1
    await sb.write_file("aiplc-docs/answer.md", "[Answer]: B")  # S3 only, VM stale
    assert "aiplc-docs/answer.md" not in harness.files   # confirm VM is stale
    _ = [e async for e in sb.send_message("continue")]   # warm reuse; must reconcile
    assert harness.files["aiplc-docs/answer.md"] == "[Answer]: B"
    assert ctrl.resume_calls == 0 and ctrl.boot_calls == 1  # reconcile != resume/reboot
