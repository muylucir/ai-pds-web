# backend/tests/test_deps_ensure_workspace.py
import asyncio
import pytest
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.routes.deps import ensure_workspace


class _FakeSandbox:
    pass


@pytest.mark.asyncio
async def test_unknown_project_404():
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-none")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_live_workspace_returned_without_boot(monkeypatch):
    app_module.registry.register("ew-live")
    ws = app_module.registry.attach("ew-live", _FakeSandbox())

    async def _no_boot(pid):
        raise AssertionError("must not boot")

    monkeypatch.setattr(app_module, "make_sandbox", _no_boot)
    assert await ensure_workspace("ew-live") is ws


@pytest.mark.asyncio
async def test_registered_project_lazy_boots_once_even_concurrently(monkeypatch):
    app_module.registry.register("ew-lazy", name="레이지")
    boots = {"n": 0}

    async def _slow_boot(pid):
        boots["n"] += 1
        await asyncio.sleep(0.02)  # 두 요청이 겹치도록
        return _FakeSandbox()

    monkeypatch.setattr(app_module, "make_sandbox", _slow_boot)
    a, b = await asyncio.gather(ensure_workspace("ew-lazy"), ensure_workspace("ew-lazy"))
    assert a is b            # 같은 Workspace
    assert boots["n"] == 1   # 이중 부팅 없음 (pid별 lock)
    assert app_module.registry.has_workspace("ew-lazy")


@pytest.mark.asyncio
async def test_boot_failure_503_keeps_registration(monkeypatch):
    app_module.registry.register("ew-fail")

    async def _boom(pid):
        raise RuntimeError("boot failed")

    monkeypatch.setattr(app_module, "make_sandbox", _boom)
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-fail")
    assert e.value.status_code == 503
    assert app_module.registry.is_registered("ew-fail")   # 다음 요청이 재시도
    assert not app_module.registry.has_workspace("ew-fail")
