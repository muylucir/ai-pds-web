import httpx
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.microvm_control import VMHandle


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
