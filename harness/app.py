# harness/app.py  (port 8080 inside the MicroVM)
from __future__ import annotations
import fnmatch
import json
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse


def build_app(driver, workspace: str) -> Starlette:
    ws = Path(workspace)
    state = {"turn_seen": False}

    async def message(request):
        body = await request.json()
        text = body["text"]
        continue_session = state["turn_seen"]
        state["turn_seen"] = True

        async def gen():
            async for ev in driver.run(text, continue_session=continue_session):
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    def _resolve(rel: str) -> Path:
        # Trust the caller for path-safety (MicroVMSandbox rejects unsafe paths
        # before it ever reaches the harness); still confine under workspace.
        return ws / rel

    async def get_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        if not p.is_file():
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(p.read_text("utf-8"))

    async def put_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(await request.body())
        return Response(status_code=204)

    async def list_files(request):
        glob = request.query_params.get("glob", "*")
        out = []
        for f in ws.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ws).as_posix()
                if fnmatch.fnmatch(rel, glob):
                    out.append(rel)
        return JSONResponse(sorted(out))

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/message", message, methods=["POST"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/files/{path:path}", get_file, methods=["GET"]),
        Route("/files/{path:path}", put_file, methods=["PUT"]),
        Route("/health", health, methods=["GET"]),
    ])
