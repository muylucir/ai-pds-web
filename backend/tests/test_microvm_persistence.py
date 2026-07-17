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
