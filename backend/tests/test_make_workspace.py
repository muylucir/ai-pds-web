import inspect
import aipds.app as app_module
from aipds.workspace import Workspace
from aipds.runner import AgentRunner


async def test_make_workspace_builds_runner_backed_workspace(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    ws = await app_module.make_workspace("proj-x")
    assert isinstance(ws, Workspace)
    assert isinstance(ws.runner, AgentRunner)
    assert ws.runner.project_id == "proj-x"


def test_make_workspace_signature():
    sig = inspect.signature(app_module.make_workspace)
    assert list(sig.parameters) == ["project_id"]

