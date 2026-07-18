import httpx
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController, VMHandle
from fakes.in_memory_s3 import FakeS3Store


async def test_harness_factory_attaches_minted_header(monkeypatch):
    calls = {}

    def fake_provider(vm_id, region):
        calls["vm_id"] = vm_id
        calls["region"] = region
        return {"X-aws-proxy-auth": f"tok-for-{vm_id}"}

    monkeypatch.setattr(app_module, "_harness_token_provider", fake_provider)

    hc = app_module._build_harness_for_test(
        VMHandle(vm_id="vm-77", base_url="https://vm", status="ready"),
        httpx.AsyncClient(),
        region="ap-northeast-1",
    )
    assert hc._headers == {"X-aws-proxy-auth": "tok-for-vm-77"}
    assert calls == {"vm_id": "vm-77", "region": "ap-northeast-1"}


async def test_token_provider_none_leaves_headers_unset(monkeypatch):
    monkeypatch.setattr(app_module, "_harness_token_provider", lambda vm_id, region: None)
    hc = app_module._build_harness_for_test(
        VMHandle(vm_id="fake-x", base_url="https://vm", status="ready"),
        httpx.AsyncClient(),
        region="ap-northeast-1",
    )
    assert hc._headers is None


def test_default_provider_delegates_to_mint(monkeypatch):
    import pathfinder.sandbox.microvm_control_aws as aws
    monkeypatch.setattr(aws, "mint_harness_token",
                        lambda vm_id, region=None, **k: {"X-aws-proxy-auth": "z"})
    # app imports the symbol; patch where it is looked up.
    monkeypatch.setattr(app_module, "mint_harness_token",
                        lambda vm_id, region: {"X-aws-proxy-auth": "z"})
    assert app_module._harness_token_provider("vm-1", "ap-northeast-1") == {"X-aws-proxy-auth": "z"}


def test_fake_handle_guard_skips_mint_unpatched(monkeypatch):
    """Pin the safety-critical branch EXECUTABLY, not just by inspection: the
    real _harness_token_provider (not a monkeypatched stand-in) must never
    call mint_harness_token for a fake- vm_id, and MUST call it for a real
    one. A future typo in the "fake-" prefix check would otherwise only
    surface as a live AWS cost, never a test failure."""
    calls = []
    monkeypatch.setattr(
        app_module, "mint_harness_token",
        lambda vm_id, region: calls.append((vm_id, region)) or {"X-aws-proxy-auth": "z"},
    )

    result = app_module._harness_token_provider("fake-abc-1", "ap-northeast-1")

    assert result is None
    assert calls == []          # NOT called for a fake- id -- no AWS/network.

    result = app_module._harness_token_provider("vm-1", "ap-northeast-1")

    assert result == {"X-aws-proxy-auth": "z"}
    assert calls == [("vm-1", "ap-northeast-1")]   # IS called for a real id.


async def test_harness_factory_closure_attaches_header_for_real_and_none_for_fake(monkeypatch):
    """End-to-end through the actual harness_factory closure built by
    _make_microvm_sandbox (not just the extracted _build_harness_for_test
    helper): a real vm_id gets the minted header, a fake- vm_id (as produced
    by FakeMicroVMController) gets none -- with no AWS call either way."""
    monkeypatch.setattr(
        app_module, "_harness_token_provider",
        lambda vm_id, region: None if vm_id.startswith("fake-") else {"X-aws-proxy-auth": f"tok-{vm_id}"},
    )
    monkeypatch.setenv("PATHFINDER_SANDBOX", "microvm")
    monkeypatch.setattr(
        app_module, "microvm_controller_factory",
        lambda project_id: FakeMicroVMController(base_url="http://fake-vm"),
    )
    monkeypatch.setattr(app_module, "s3_store_factory", lambda project_id: FakeS3Store())

    sb = await app_module.make_sandbox("proj-harness-factory")
    assert isinstance(sb, MicroVMSandbox)

    # Fake handle (as FakeMicroVMController.boot() produces): no header.
    fake_handle = VMHandle(vm_id="fake-proj-1", base_url="http://fake-vm", status="ready")
    fake_hc = sb._harness_factory(fake_handle)
    assert fake_hc._headers is None

    # Real handle: header attached.
    real_handle = VMHandle(vm_id="vm-real-1", base_url="http://fake-vm", status="ready")
    real_hc = sb._harness_factory(real_handle)
    assert real_hc._headers == {"X-aws-proxy-auth": "tok-vm-real-1"}

    await sb.stop()
