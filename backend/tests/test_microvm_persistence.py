import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store
from pathfinder.sandbox.base import AgentEvent

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_write_then_read_uses_s3_without_booting():
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "entry")
    assert await sb.read_file("aiplc-docs/audit.md") == "entry"
    assert ctrl.boot_calls == 0            # true laziness: NO VM for file ops
    assert s3.blobs["aiplc-docs/audit.md"] == "entry"   # landed in durable S3
    assert harness.files == {}             # harness NOT touched by file ops

async def test_list_files_globs_over_s3_without_booting():
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")   # must not match
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]
    assert ctrl.boot_calls == 0

async def test_read_missing_from_s3_raises_filenotfound():
    sb, _, _, _ = _sandbox()
    await sb.start()
    with pytest.raises(FileNotFoundError):
        await sb.read_file("aiplc-docs/missing.md")

async def test_path_safety_runs_before_s3():
    sb, ctrl, _, s3 = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.read_file("/etc/passwd")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert s3.blobs == {}                  # nothing written past the guard
    assert ctrl.boot_calls == 0

def _sandbox_with_agent_writes(files_written: dict[str, str]):
    """A FakeHarness whose 'turn' writes files into the VM FS (like Claude Code
    does), so we can assert the post-turn sync pulls them into S3."""
    harness = FakeHarness()

    async def _turn(text: str):
        for k, v in files_written.items():
            harness.files[k] = v            # agent writes to the VM FS
        yield AgentEvent(kind="message", text="worked")
        yield AgentEvent(kind="done")

    harness._events_for = None
    harness.send_message = _turn            # override the canned echo turn
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1", controller=ctrl, spec=BootSpec(),
        harness_factory=lambda handle: harness, s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_turn_syncs_agent_written_files_to_s3():
    sb, _, _, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/aiplc-state.md": "stage: Discovery",
        "aiplc-docs/audit.md": "entry 1",
        "prototype/app.py": "print('hi')",
    })
    await sb.start()
    _ = [e async for e in sb.send_message("start ai-plc")]
    # After the turn, S3 (durable) holds what the agent wrote in the VM.
    assert s3.blobs["aiplc-docs/aiplc-state.md"] == "stage: Discovery"
    assert s3.blobs["aiplc-docs/audit.md"] == "entry 1"
    assert s3.blobs["prototype/app.py"] == "print('hi')"

async def test_route_read_after_turn_sees_synced_state():
    sb, _, _, _ = _sandbox_with_agent_writes({"aiplc-docs/aiplc-state.md": "stage: Envision"})
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    # read_file goes to S3 (Task 3); it must reflect the just-synced turn output.
    assert await sb.read_file("aiplc-docs/aiplc-state.md") == "stage: Envision"

async def test_only_sync_subtrees_are_pushed():
    sb, _, harness, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/audit.md": "keep",
        "node_modules/pkg/index.js": "DROP",   # outside the sync globs
    })
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert "aiplc-docs/audit.md" in s3.blobs
    assert "node_modules/pkg/index.js" not in s3.blobs
