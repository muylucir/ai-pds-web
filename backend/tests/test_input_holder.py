# backend/tests/test_input_holder.py
from pathlib import Path
import pytest
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _microvm():
    harness = FakeHarness()
    return MicroVMSandbox(
        project_id="p1",
        controller=FakeMicroVMController(base_url="http://fake-vm"),
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=FakeS3Store(),
    )

async def test_local_sandbox_inherits_input_holder_default(tmp_path: Path):
    # The Finding-B fix: LocalSandbox must NOT raise AttributeError when a
    # route touches the hint polymorphically.
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    assert sb.input_holder is None          # concrete default from the ABC
    sb.set_input_holder("facilitator-1")
    assert sb.input_holder == "facilitator-1"

@pytest.mark.skip(
    reason="gated behind Task 3 (brief Step 2 note): MicroVMSandbox.__init__ "
    "does not yet accept s3= (that constructor change lands with Task 3's "
    "not-booted-ops S3 reroute). Re-enable once Task 3 lands."
)
async def test_microvm_sandbox_still_supports_input_holder():
    sb = _microvm()
    await sb.start()
    assert sb.input_holder is None
    sb.set_input_holder("customer-pm")
    assert sb.input_holder == "customer-pm"

def test_both_share_one_definition():
    # The hint is defined once on the ABC; subclasses do not shadow it.
    from pathfinder.sandbox.base import Sandbox
    assert "set_input_holder" in vars(Sandbox)
    assert "set_input_holder" not in vars(MicroVMSandbox)   # inherited, not duplicated
