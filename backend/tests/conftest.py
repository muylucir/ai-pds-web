# backend/tests/conftest.py
import asyncio
import pytest
from pathfinder import app as app_module
from pathfinder.routes import deps as deps_module


@pytest.fixture(autouse=True)
def _reset_registry_and_boot_locks():
    """app_module.registry와 deps._boot_locks는 프로세스 전역 싱글턴이라
    테스트 간 오염이 가능하다(예: lifespan 테스트의 "restored-1" 잔재가 이후
    list_projects 테스트에 새어나가는 것). 각 테스트 전후로 그 시점 기준
    새로 생긴 키만 걷어낸다 — 스냅샷을 통째로 복원하면 같은 모듈 안에서 여러
    테스트가 registry 축적에 의존하는 기존 스위트(예: test_routes_projects_list
    의 test_list_projects_includes_created_projects_with_names)가 깨진다."""
    names_before = set(app_module.registry._names)
    workspaces_before = set(app_module.registry._workspaces)
    yield
    for pid in set(app_module.registry._names) - names_before:
        app_module.registry._names.pop(pid, None)
    for pid in set(app_module.registry._workspaces) - workspaces_before:
        app_module.registry._workspaces.pop(pid, None)
    deps_module._boot_locks.clear()


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """pytest-asyncio's auto mode calls asyncio.set_event_loop(None) during
    teardown of async tests, which leaves the thread's event-loop policy with
    _set_called=True and _loop=None. A subsequent asyncio.get_event_loop()
    call (used by tests/test_routes_artifacts.py to drive seed helpers) then
    raises RuntimeError instead of lazily creating a loop. Ensure a usable
    loop is set before every test so standalone and full-suite runs behave
    the same regardless of test order.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
