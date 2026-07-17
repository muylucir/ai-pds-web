# backend/tests/test_golden_path_replay.py
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app_module.app)

# The pilot1 stage sequence, in completion order (from aiplc-state.md).
STAGES = [
    "Workspace Detection", "Discovery Mode Selection", "Envision",
    "Solution Analysis", "Prototype & Validation", "Product Strategy",
    "Go-to-Market", "Discovery Document",
]

def _state_md(completed_count):
    lines = ["# AI-PLC State Tracking",
             "- **Project Type**: Greenfield",
             f"- **Current Stage**: {STAGES[min(completed_count, len(STAGES)-1)]}",
             "## Stage Progress"]
    for i, name in enumerate(STAGES):
        mark = "x" if i < completed_count else " "
        lines.append(f"- [{mark}] {name}")
    return "\n".join(lines) + "\n"

def test_replay_advances_state_like_pilot1():
    # Agent script: each user message advances the workspace by one completed stage.
    counter = {"n": 1}
    def script(text, sb):
        counter["n"] += 1
        # write synchronously via the sandbox's resolve (LocalSandbox is on disk)
        p = sb._resolve("aiplc-docs/aiplc-state.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_state_md(counter["n"]), encoding="utf-8")
        return [AgentEvent(kind="file_changed", path="aiplc-docs/aiplc-state.md"),
                AgentEvent(kind="done")]

    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)
        await sb.start()
        sb._resolve("aiplc-docs").mkdir(parents=True, exist_ok=True)
        sb._resolve("aiplc-docs/aiplc-state.md").write_text(_state_md(1), encoding="utf-8")
        return sb
    app_module.make_sandbox = make

    client.post("/projects", json={"project_id": "replay"})
    # advance through all remaining stages
    for _ in range(len(STAGES) - 1):
        assert client.post("/projects/replay/message", json={"text": "승인"}).status_code == 200

    state = client.get("/projects/replay/state").json()
    names = [s["name"] for s in state["stages"]]
    assert names == STAGES
    assert all(s["status"] == "completed" for s in state["stages"])
