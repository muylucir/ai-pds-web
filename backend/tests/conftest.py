# backend/tests/conftest.py
import asyncio
import os

# Auth bypass is turned on only by an explicit switch (app.cognito_config). Tests run in the same
# state as local development — this must come before importing app (it is evaluated once at import time).
os.environ.setdefault("AIPDS_AUTH_DISABLED", "1")

import pytest  # noqa: E402
from aipds import app as app_module  # noqa: E402
from aipds.routes import deps as deps_module  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_registry_and_boot_locks():
    """app_module.registry and deps._boot_locks are process-global singletons, so
    tests can contaminate each other (e.g., a leftover "restored-1" from a lifespan
    test leaking into a later list_projects test). Before and after each test, only
    the keys newly created since that point are removed — restoring a whole snapshot
    would break existing suites where several tests in the same module depend on
    registry accumulation (e.g., test_routes_projects_list's
    test_list_projects_includes_created_projects_with_names).

    _created_at/_model_id are cleaned with the same discipline — get_created_at/get_model_id
    (unlike get_name) read directly with dict.get() without checking _names
    membership, so if these two dicts are not cleaned, the values of a pid registered by
    one test survive even after only _names/_workspaces are cleared, and leak into the
    next test that reuses the same pid string."""
    names_before = set(app_module.registry._names)
    workspaces_before = set(app_module.registry._workspaces)
    created_at_before = set(app_module.registry._created_at)
    model_id_before = set(app_module.registry._model_id)
    owner_before = set(app_module.registry._owner)
    members_before = set(app_module.registry._members)
    yield
    for pid in set(app_module.registry._names) - names_before:
        app_module.registry._names.pop(pid, None)
    for pid in set(app_module.registry._workspaces) - workspaces_before:
        app_module.registry._workspaces.pop(pid, None)
    for pid in set(app_module.registry._created_at) - created_at_before:
        app_module.registry._created_at.pop(pid, None)
    for pid in set(app_module.registry._model_id) - model_id_before:
        app_module.registry._model_id.pop(pid, None)
    for pid in set(app_module.registry._owner) - owner_before:
        app_module.registry._owner.pop(pid, None)
    for pid in set(app_module.registry._members) - members_before:
        app_module.registry._members.pop(pid, None)
    deps_module._boot_locks.clear()


@pytest.fixture(autouse=True)
def _isolated_revocations(monkeypatch, tmp_path):
    """Session revocation records (auth/revocation.py) persist to a file — keep one per test. If shared,
    a user cut off by one test gets a 401 in the next, and the default path is the developer's home."""
    from aipds.auth.revocation import Revocations
    store = Revocations(tmp_path / "revoked.json")
    monkeypatch.setattr(app_module, "revocations", lambda: store)
    return store


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


@pytest.fixture(autouse=True)
def _agent_home_in_tmp(tmp_path_factory, monkeypatch):
    """The agent home (aipds/agent_home.py) defaults to `~/aipds-agent-home`. The driver and builder
    factories create directories under it, so relocate it to keep tests from touching the real home. The
    launch wrapper starts disabled in every test (tests that enable it call set_current themselves)."""
    from aipds import launcher
    monkeypatch.setenv("AIPDS_AGENT_HOME_DIR", str(tmp_path_factory.mktemp("agent-home")))
    launcher.set_current(None)
    yield
    launcher.set_current(None)
