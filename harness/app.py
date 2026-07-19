# harness/app.py  (port 8080 inside the MicroVM)
from __future__ import annotations
import json
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from globmatch import matches_glob


def build_app(driver, workspace: str) -> Starlette:
    ws = Path(workspace).resolve()
    state = {"turn_seen": False}

    async def message(request):
        body = await request.json()
        text = body["text"]
        session = body.get("session")
        if session is not None:
            gen_src = driver.run(text, session)
        else:
            # Legacy path (claude_driver rollback): per-process continue flag.
            continue_session = state["turn_seen"]
            state["turn_seen"] = True
            gen_src = driver.run(text, continue_session=continue_session)

        async def gen():
            async for ev in gen_src:
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    async def answers(request):
        body = await request.json()
        # Extract required keys BEFORE building the response: EventSourceResponse
        # flushes a 200 + headers as soon as it starts, so a KeyError raised
        # inside `gen()` (i.e. after streaming has begun) surfaces to the
        # client as a "successful" response with zero events -- an invisible
        # failure. Validate up front and return a normal 400 instead.
        try:
            interrupt_id = body["interrupt_id"]
            answers_map = body["answers"]
            session = body["session"]
        except KeyError as exc:
            return PlainTextResponse(f"missing key: {exc.args[0]}", status_code=400)
        gen_src = driver.run_answers(interrupt_id, answers_map, session)

        async def gen():
            async for ev in gen_src:
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    async def pending(request):
        body = await request.json()
        try:
            session = body["session"]
        except KeyError as exc:
            return PlainTextResponse(f"missing key: {exc.args[0]}", status_code=400)
        return JSONResponse({"pending": await driver.pending(session)})

    def _resolve(rel: str) -> Path | None:
        # MicroVMSandbox.reject_unsafe() rejects unsafe paths (absolute /
        # ".." segments) before a request ever leaves the backend, so in
        # the normal flow this never sees an escaping path. But drill
        # scripts and any other direct caller talk to the harness straight
        # over HTTP, bypassing that check entirely -- the proxy token in
        # front of this port authenticates WHO is calling, not WHAT path
        # they send. So this is defense-in-depth, not the only guard:
        # resolve `..`/symlinks and confirm the result is still under the
        # workspace root before touching the filesystem.
        resolved = (ws / rel).resolve()
        if not resolved.is_relative_to(ws):
            return None
        return resolved

    async def get_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        if p is None:
            return PlainTextResponse("unsafe path", status_code=400)
        if not p.is_file():
            return PlainTextResponse("not found", status_code=404)
        # Synced subtrees (aiplc-docs/**, prototype/**) can contain binary
        # prototype assets (images, fonts, etc.), not just text docs. A
        # strict UTF-8 decode raises UnicodeDecodeError on those and would
        # abort the whole post-turn sync loop for one bad file. Since the
        # backend's S3 store treats file content as `str` end-to-end, the
        # minimal protocol-compatible fix is a lossy decode (U+FFFD
        # replacement chars) rather than returning raw bytes. This is a
        # known lossy tradeoff for binary content; a real fix (binary-safe
        # storage/serving of prototype assets) is a Task-7 drill item.
        return PlainTextResponse(p.read_bytes().decode("utf-8", errors="replace"))

    async def put_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        if p is None:
            return PlainTextResponse("unsafe path", status_code=400)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(await request.body())
        return Response(status_code=204)

    async def list_files(request):
        glob = request.query_params.get("glob", "*")
        out = []
        for f in ws.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ws).as_posix()
                if matches_glob(rel, glob):
                    out.append(rel)
        return JSONResponse(sorted(out))

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/message", message, methods=["POST"]),
        Route("/answers", answers, methods=["POST"]),
        Route("/pending", pending, methods=["POST"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/files/{path:path}", get_file, methods=["GET"]),
        Route("/files/{path:path}", put_file, methods=["PUT"]),
        Route("/health", health, methods=["GET"]),
    ])
