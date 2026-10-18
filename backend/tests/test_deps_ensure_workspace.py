# backend/tests/test_deps_ensure_workspace.py
import asyncio
import pytest
from fastapi import HTTPException
from aipds import app as app_module
from aipds.workspace import Workspace
from aipds.routes.deps import ensure_workspace


class _FakeRunner:
    pass


@pytest.mark.asyncio
async def test_unknown_project_404():
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-none")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_live_workspace_returned_without_boot(monkeypatch):
    app_module.registry.register("ew-live")
    ws = app_module.registry.attach("ew-live", Workspace(_FakeRunner()))

    async def _no_boot(pid):
        raise AssertionError("must not boot")

    monkeypatch.setattr(app_module, "make_workspace", _no_boot)
    assert await ensure_workspace("ew-live") is ws


@pytest.mark.asyncio
async def test_registered_project_lazy_boots_once_even_concurrently(monkeypatch):
    app_module.registry.register("ew-lazy", name="레이지")
    boots = {"n": 0}

    async def _slow_boot(pid):
        boots["n"] += 1
        await asyncio.sleep(0.02)  # so the two requests overlap
        return Workspace(_FakeRunner())

    monkeypatch.setattr(app_module, "make_workspace", _slow_boot)
    a, b = await asyncio.gather(ensure_workspace("ew-lazy"), ensure_workspace("ew-lazy"))
    assert a is b            # the same Workspace
    assert boots["n"] == 1   # no double boot (per-pid lock)
    assert app_module.registry.has_workspace("ew-lazy")


@pytest.mark.asyncio
async def test_boot_failure_503_keeps_registration(monkeypatch):
    app_module.registry.register("ew-fail")

    async def _boom(pid):
        raise RuntimeError("boot failed")

    monkeypatch.setattr(app_module, "make_workspace", _boom)
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-fail")
    assert e.value.status_code == 503
    assert app_module.registry.is_registered("ew-fail")   # the next request retries
    assert not app_module.registry.has_workspace("ew-fail")


@pytest.mark.asyncio
async def test_delete_during_boot_races_404_and_stops_runner(monkeypatch):
    """When DELETE /projects/{pid} cuts in while initialization is pending: make_workspace
    succeeds, but registry.remove runs in the meantime, so it is unregistered at attach time.
    In that case the runner of the workspace just created must be stopped instead of leaking, and the response
    must be 'project deleted during initialization' → 404 (same as the route's usual unregistered semantics)."""
    pid = "ew-race-del"
    app_module.registry.register(pid)
    stopped = {"n": 0}

    class _StoppableRunner:
        async def stop(self):
            stopped["n"] += 1

    async def _boot_then_delete(pid):
        app_module.registry.remove(pid)  # simulate a concurrent DELETE
        return Workspace(_StoppableRunner())

    monkeypatch.setattr(app_module, "make_workspace", _boot_then_delete)
    with pytest.raises(HTTPException) as e:
        await ensure_workspace(pid)
    assert e.value.status_code == 404
    assert stopped["n"] == 1
    assert not app_module.registry.has_workspace(pid)
