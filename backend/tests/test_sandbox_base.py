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

def test_agent_event_structured_kinds_and_payload():
    from pathfinder.sandbox.base import AgentEvent
    ev = AgentEvent(kind="questions", payload='{"interrupt_id":"i-1","questions":[]}')
    assert ev.payload == '{"interrupt_id":"i-1","questions":[]}'
    assert AgentEvent(kind="stage").payload is None
    AgentEvent(kind="document")  # must not raise

def test_sandbox_abc_requires_answers_and_pending():
    from pathfinder.sandbox.base import Sandbox
    assert "send_answers" in Sandbox.__abstractmethods__
    assert "pending" in Sandbox.__abstractmethods__
