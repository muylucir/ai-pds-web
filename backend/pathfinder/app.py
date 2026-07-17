# backend/pathfinder/app.py
from __future__ import annotations
import tempfile
from pathlib import Path
from fastapi import FastAPI
from pathfinder.workspace import ProjectRegistry
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import Sandbox

registry = ProjectRegistry()

async def make_sandbox(project_id: str) -> Sandbox:
    root = Path(tempfile.mkdtemp(prefix=f"pf-{project_id}-"))
    sb = LocalSandbox(root=root)
    await sb.start()
    return sb

app = FastAPI(title="Pathfinder")

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)
