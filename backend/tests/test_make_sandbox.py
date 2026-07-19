import inspect
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController
from fakes.in_memory_s3 import FakeS3Store

async def test_default_is_local_sandbox(monkeypatch):
    monkeypatch.delenv("PATHFINDER_SANDBOX", raising=False)
    sb = await app_module.make_sandbox("proj-local")
    assert isinstance(sb, LocalSandbox)

async def test_microvm_flag_builds_microvm_sandbox_with_s3(monkeypatch):
    monkeypatch.setenv("PATHFINDER_SANDBOX", "microvm")
    monkeypatch.setattr(
        app_module, "microvm_controller_factory",
        lambda project_id: FakeMicroVMController(base_url="http://fake-vm"),
    )
    monkeypatch.setattr(
        app_module, "s3_store_factory",
        lambda project_id: FakeS3Store(),
    )
    sb = await app_module.make_sandbox("proj-vm")
    assert isinstance(sb, MicroVMSandbox)
    await sb.start()
    assert sb._handle is None            # lazy: no boot at creation
    # File ops work against injected S3 with no AWS and no boot (Task 3):
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert await sb.read_file("aiplc-docs/x.md") == "hi"

def test_make_sandbox_signature_unchanged():
    sig = inspect.signature(app_module.make_sandbox)
    assert list(sig.parameters) == ["project_id"]

def test_microvm_harness_carries_session(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
    monkeypatch.setenv("PATHFINDER_S3_REGION", "ap-northeast-2")
    from pathfinder.app import _build_harness_for_test
    from pathfinder.sandbox.microvm_control import VMHandle
    import httpx
    hc = _build_harness_for_test(
        VMHandle(vm_id="fake-x", base_url="http://h", status="ready"),
        httpx.AsyncClient(), "ap-northeast-1",
        session={"session_id": "p9", "bucket": "bkt",
                 "region": "ap-northeast-2", "prefix": "sessions"})
    assert hc._session["session_id"] == "p9"
