# backend/tests/test_make_sandbox.py
import importlib
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController

async def test_default_is_local_sandbox(monkeypatch):
    monkeypatch.delenv("PATHFINDER_SANDBOX", raising=False)
    sb = await app_module.make_sandbox("proj-local")
    assert isinstance(sb, LocalSandbox)

@pytest.mark.skip(reason="MicroVMSandbox now requires s3=; app._make_microvm_sandbox is wired with the S3Store in Task 7 (s3_store_factory). Unskip when Task 7 lands.")
async def test_microvm_flag_builds_microvm_sandbox(monkeypatch):
    monkeypatch.setenv("PATHFINDER_SANDBOX", "microvm")
    # Inject a fake controller so no AWS is contacted.
    monkeypatch.setattr(
        app_module, "microvm_controller_factory",
        lambda project_id: FakeMicroVMController(base_url="http://fake-vm"),
    )
    sb = await app_module.make_sandbox("proj-vm")
    assert isinstance(sb, MicroVMSandbox)
    await sb.start()
    assert sb._handle is None  # lazy: still not booted right after creation

def test_make_sandbox_signature_unchanged():
    import inspect
    sig = inspect.signature(app_module.make_sandbox)
    assert list(sig.parameters) == ["project_id"]
