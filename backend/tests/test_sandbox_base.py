# backend/tests/test_sandbox_base.py
import inspect
from pathfinder.sandbox.base import Sandbox, AgentEvent, TurnResult

def test_agent_event_shape():
    e = AgentEvent(kind="message", text="hi", path=None)
    assert e.kind == "message"

def test_sandbox_is_abstract():
    assert inspect.isabstract(Sandbox)
    for m in ("start", "read_file", "write_file", "list_files", "send_message", "stop"):
        assert hasattr(Sandbox, m)
