# backend/tests/test_sandbox_base.py
import inspect
import re
from pathlib import Path
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


def _extract_kind_literal(text: str) -> set[str]:
    """Extract the AgentEvent `kind: Literal[...]` string set from a source
    file's text, tolerant of the multi-line/quote formatting either side
    uses (kept simple/robust per the finding -- this is a mirror GUARD, not
    a full parser)."""
    m = re.search(r"kind:\s*Literal\[(.*?)\]", text, re.DOTALL)
    assert m, "could not find `kind: Literal[...]` in source"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_backend_and_harness_agent_event_kinds_match():
    # Mirror guard (E1): harness/events.py's AgentEvent.kind Literal MUST list
    # exactly the same kinds as backend/pathfinder/sandbox/base.py's -- a
    # drift here would silently break the SSE contract between the harness
    # (inside the MicroVM) and the backend.
    repo_root = Path(__file__).resolve().parents[2]
    backend_src = (repo_root / "backend" / "pathfinder" / "sandbox" / "base.py").read_text()
    harness_src = (repo_root / "harness" / "events.py").read_text()
    backend_kinds = _extract_kind_literal(backend_src)
    harness_kinds = _extract_kind_literal(harness_src)
    assert backend_kinds == harness_kinds
    assert len(backend_kinds) == 8
