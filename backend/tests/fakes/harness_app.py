# backend/tests/fakes/harness_app.py
from __future__ import annotations
import json
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

def build_fake_harness_app(scripted_events: list[dict] | None = None) -> Starlette:
    """A Starlette app emulating the MicroVM harness for HarnessClient tests.

    In-memory file store + a scripted /message SSE stream. `scripted_events`
    is a list of AgentEvent-shaped dicts; defaults to an echo turn.
    """
    files: dict[str, str] = {}

    async def message(request):
        body = await request.json()
        events = scripted_events or [
            {"kind": "message", "text": f"echo: {body['text']}", "path": None},
            {"kind": "done", "text": None, "path": None},
        ]
        async def gen():
            for ev in events:
                yield {"data": json.dumps(ev)}
        return EventSourceResponse(gen())

    async def get_file(request):
        path = request.path_params["path"]
        if path not in files:
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(files[path])

    async def put_file(request):
        path = request.path_params["path"]
        files[path] = (await request.body()).decode("utf-8")
        return Response(status_code=204)

    async def list_files(request):
        import fnmatch
        glob = request.query_params.get("glob", "*")
        return JSONResponse(sorted(p for p in files if fnmatch.fnmatch(p, glob)))

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/message", message, methods=["POST"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/files/{path:path}", get_file, methods=["GET"]),
        Route("/files/{path:path}", put_file, methods=["PUT"]),
        Route("/health", health, methods=["GET"]),
    ])
