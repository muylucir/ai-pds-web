from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from sandbox_contract import run_sandbox_contract

async def test_local_sandbox_satisfies_contract(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await run_sandbox_contract(sb)

# append to backend/tests/test_sandbox_contract.py
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store
from sandbox_contract import run_sandbox_contract

async def test_microvm_sandbox_satisfies_same_contract():
    sb = MicroVMSandbox(
        project_id="p1",
        controller=FakeMicroVMController(base_url="http://fake-vm"),
        spec=BootSpec(),
        harness_factory=lambda handle: FakeHarness(),
        s3=FakeS3Store(),
    )
    await sb.start()
    await run_sandbox_contract(sb)       # SAME assertions LocalSandbox passes
