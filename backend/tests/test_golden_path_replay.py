# backend/tests/test_golden_path_replay.py
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent
from pathfinder.parsers.state import parse_state_file

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

def test_replay_advances_state_like_pilot1(monkeypatch):
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
    monkeypatch.setattr(app_module, "make_sandbox", make)

    client.post("/projects", json={"project_id": "replay"})
    # advance through all remaining stages
    for _ in range(len(STAGES) - 1):
        assert client.post("/projects/replay/message", json={"text": "승인"}).status_code == 200

    state = client.get("/projects/replay/state").json()
    names = [s["name"] for s in state["stages"]]
    assert names == STAGES
    assert all(s["status"] == "completed" for s in state["stages"])

def test_stages_match_real_pilot1_fixture():
    # Guard against STAGES drifting from the real pilot1 artifact — the §7
    # reproducibility guarantee is meant to track the actual fixture, not a copy.
    md = (FIX / "aiplc-state.md").read_text(encoding="utf-8")
    real_names = [s.name for s in parse_state_file(md).stages]
    assert real_names == STAGES

def test_replay_via_answers_stream_advances_stages(monkeypatch):
    """Spec §7: the pilot1 stage sequence driven through the EVENT contract —
    each send_answers round completes one stage via a stage event."""
    import json as _json
    round_n = {"i": 0}

    def script(text, sb):
        payload = _json.dumps({"interrupt_id": f"i-{round_n['i']}",
                               "questions": {"name": "q", "preamble": None,
                                             "parse_ok": True, "raw_markdown": None,
                                             "questions": []}})
        return [AgentEvent(kind="questions", payload=payload), AgentEvent(kind="done")]

    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)

        async def send_answers(answers):
            i = round_n["i"] = round_n["i"] + 1
            stage = STAGES[min(i, len(STAGES) - 1)]
            yield AgentEvent(kind="stage", payload=_json.dumps(
                {"stage": stage, "status": "completed", "summary": ""}))
            nxt = _json.dumps({"interrupt_id": f"i-{i}", "questions":
                               {"name": "q", "preamble": None, "parse_ok": True,
                                "raw_markdown": None, "questions": []}})
            yield AgentEvent(kind="questions", payload=nxt)
            yield AgentEvent(kind="done")
        sb.send_answers = send_answers  # scripted structured rounds
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)

    client.post("/projects", json={"project_id": "replay-ev"})
    with client.stream("GET", "/projects/replay-ev/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    completed = []
    for _ in range(len(STAGES) - 1):
        with client.stream("GET", "/projects/replay-ev/answers/stream",
                           params={"answers": _json.dumps({"1": "A"})}) as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    ev = _json.loads(line[5:].strip())
                    if ev["kind"] == "stage":
                        completed.append(_json.loads(ev["payload"])["stage"])
    assert completed == STAGES[1:]
