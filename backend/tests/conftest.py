# backend/tests/conftest.py
import asyncio
import pytest

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
