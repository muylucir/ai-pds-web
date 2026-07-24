# backend/pathfinder/routes/prototypes.py — prototype build sessions + hosting.
#
# REST + SSE for the prototype tab: session lifecycle against the Tokyo
# MicroVM (PrototypeSession), local hosting (ProtoHost), and a streaming
# reverse proxy that exposes a hosted prototype under the existing
# /api -> :8000 nginx/CloudFront routing (no hosting-stack changes).
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, Response, StreamingResponse

from pathfinder.models import AgentEvent
from pathfinder.parsers.redaction import redact_credentials

_log = logging.getLogger(__name__)

router = APIRouter()

_SPEC_PREFIX = "aiplc-docs/discovery/prototypes/"
_SPEC_RE = re.compile(r"^aiplc-docs/discovery/prototypes/([^/]+)/PROTOTYPE-\1\.md$")

# The frontend opens the first events stream with this sentinel; the route
# substitutes session.first_prompt() so the build kicks off as a normal
# SSE-relayed turn (spec §4: 첫 턴 자동 발화).
_FIRST_TURN_SENTINEL = "__first__"

# Statuses that count as "a live session exists" for 409/list purposes.
_LIVE_STATUSES = {"starting", "ready", "building", "waiting_input"}


def _redacted(event: AgentEvent) -> AgentEvent:
    """Copy of turns.py's redaction seam: text AND payload are agent-authored
    content; kind/path stay structural."""
    updates = {}
    if event.text is not None:
        updates["text"] = redact_credentials(event.text)
    if event.payload is not None:
        updates["payload"] = redact_credentials(event.payload)
    return event.model_copy(update=updates) if updates else event


def _require_registered(pid: str) -> None:
    import pathfinder.app as app_module
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")


#: A session in one of these terminal states is dead: it must NOT block a new
#: start (409) and must NOT be served as an active stream (404). Keeping
#: "failed" out of this set wedged the prototype permanently — POST said
#: "already active" while GET said "no active session", so the user could
#: neither restart nor stream.
_DEAD_STATUSES = ("closed", "failed")


def _live_session(pid: str, slug: str):
    import pathfinder.app as app_module
    session = app_module.proto_sessions.get((pid, slug))
    if session is None or session.status in _DEAD_STATUSES:
        return None
    return session


def _require_session(pid: str, slug: str):
    session = _live_session(pid, slug)
    if session is None:
        raise HTTPException(status_code=404, detail="no active build session")
    return session


# ---- listing ----

@router.get("/projects/{pid}/prototypes")
async def list_prototypes(pid: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    s3 = app_module.s3_store_factory(pid)

    slugs: dict[str, str] = {}
    for key in await s3.list(_SPEC_PREFIX):
        m = _SPEC_RE.match(key)
        if m:
            slugs[m.group(1)] = key

    host = app_module.proto_host()
    out = []
    for slug, spec_path in sorted(slugs.items()):
        state = "none"
        port: int | None = None

        session = app_module.proto_sessions.get((pid, slug))
        host_info = host.status(pid, slug)
        bundle_exists = bool(await s3.list(f"prototypes/{slug}/bundle/"))

        if session is not None and session.status in _LIVE_STATUSES:
            state = "building"
        elif host_info is not None and host_info.state == "running":
            state = "running"
            port = host_info.port
        elif bundle_exists:
            state = "built"
        elif session is not None and session.status == "failed":
            state = "failed"

        out.append({"slug": slug, "spec_path": spec_path,
                    "state": state, "port": port})
    return out


# ---- build session lifecycle ----

@router.post("/projects/{pid}/prototypes/{slug}/session", status_code=202)
async def start_session(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    if _live_session(pid, slug) is not None:
        raise HTTPException(status_code=409, detail="build session already active")
    # Evict any dead (closed/failed) session so a retry starts clean instead of
    # tripping over the corpse of the previous attempt.
    app_module.proto_sessions.pop((pid, slug), None)

    # Misconfiguration is not a bad gateway: a missing or malformed ARN fails
    # deep inside boto3 (ParamValidationError / ValidationException), which
    # surfaces as an opaque 502 the moment the user clicks 빌드 시작 and sends
    # them log-diving. Check the shape up front and name the bad variable.
    for var, prefix in (("PATHFINDER_VM_IMAGE_ID", "arn:aws:lambda:"),
                        ("PATHFINDER_VM_ROLE_ARN", "arn:aws:iam:")):
        value = os.environ.get(var, "")
        if not value:
            raise HTTPException(
                status_code=503,
                detail=f"prototype build is not configured on this server "
                       f"({var} unset — deploy PathfinderVmStack and inject "
                       f"its outputs)")
        if not value.startswith(prefix):
            raise HTTPException(
                status_code=503,
                detail=f"prototype build is misconfigured: {var} is not a "
                       f"valid ARN (expected it to start with {prefix!r})")

    session = app_module.proto_session_factory(pid, slug)
    try:
        await session.start()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype spec not found")
    except Exception:
        # Boot/push failures carry AWS details -- log them server-side only,
        # surface a sanitized reason (spec §5: 자격증명 노출 방지).
        _log.exception("prototype session start failed: %s/%s", pid, slug)
        raise HTTPException(status_code=502, detail="session start failed")
    app_module.proto_sessions[(pid, slug)] = session
    return {"status": session.status}


@router.get("/projects/{pid}/prototypes/{slug}/events")
async def stream_session_events(pid: str, slug: str, text: str):
    _require_registered(pid)
    session = _require_session(pid, slug)
    if text == _FIRST_TURN_SENTINEL:
        text = session.first_prompt()

    async def gen():
        async for event in session.send_message(text):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


class AnswersBody(BaseModel):
    answers: dict[str, str]


@router.post("/projects/{pid}/prototypes/{slug}/answers", status_code=204)
async def submit_answers(pid: str, slug: str, body: AnswersBody):
    """Resolve the pending question. interrupt_id is session-owned (captured
    from the questions event as it passed through the open events stream) --
    the client never sees or sends it. Events continue on that open stream;
    this endpoint only unblocks it, hence 204 not SSE."""
    _require_registered(pid)
    session = _require_session(pid, slug)
    ok = await session.send_answers(body.answers)
    if not ok:
        raise HTTPException(status_code=409, detail="no pending questions")
    return Response(status_code=204)


@router.post("/projects/{pid}/prototypes/{slug}/interrupt", status_code=202)
async def interrupt_session(pid: str, slug: str):
    _require_registered(pid)
    session = _require_session(pid, slug)
    await session.interrupt()
    return {"status": "interrupting"}


@router.delete("/projects/{pid}/prototypes/{slug}/session", status_code=204)
async def close_session(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    session = app_module.proto_sessions.get((pid, slug))
    if session is None:
        raise HTTPException(status_code=404, detail="no build session")
    await session.close()
    del app_module.proto_sessions[(pid, slug)]
    return Response(status_code=204)


# ---- hosting ----

@router.post("/projects/{pid}/prototypes/{slug}/host")
async def start_host(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    try:
        info = await app_module.proto_host().start(pid, slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype bundle not found")
    if info.state == "failed":
        raise HTTPException(status_code=502, detail=info.log_tail)
    return {"state": info.state, "port": info.port, "log_tail": info.log_tail}


@router.get("/projects/{pid}/prototypes/{slug}/host")
async def host_status(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    info = app_module.proto_host().status(pid, slug)
    if info is None:
        raise HTTPException(status_code=404, detail="not hosted")
    return {"state": info.state, "port": info.port,
            "log_tail": app_module.proto_host().log_tail(pid, slug)}


@router.delete("/projects/{pid}/prototypes/{slug}/host", status_code=204)
async def stop_host(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    await app_module.proto_host().stop(pid, slug)
    return Response(status_code=204)


# ---- streaming reverse proxy ----

# Hop-by-hop request headers never forwarded upstream; x-origin-verify is the
# CloudFront->nginx shared secret and must not leak into prototype processes.
_STRIP_REQUEST_HEADERS = {"host", "x-origin-verify", "connection",
                          "keep-alive", "transfer-encoding"}
# Hop-by-hop response headers: the proxy re-frames the body itself.
_STRIP_RESPONSE_HEADERS = {"transfer-encoding", "connection", "keep-alive"}


def _rewritten_location(value: str, pid: str, slug: str) -> str:
    """Rewrite an upstream redirect target into a proxy-relative path.

    The prototype process only knows its own origin (127.0.0.1:<port>), so a
    redirect it issues — or one Starlette issues for a missing trailing slash
    — would send the browser straight at an internal address it cannot reach
    ("localhost:8000/proto/..." and then a hang). Reduce any absolute URL that
    points at the upstream to its path, and express every path under this
    prototype's proxy prefix. An off-site absolute redirect is left alone.
    """
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        # Only rewrite self-references; a redirect to a genuinely external
        # host must survive untouched.
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            return value
        path, query = parsed.path, parsed.query
    else:
        path, query = parsed.path, parsed.query
    prefix = f"/proto/{quote(pid)}/{quote(slug)}"
    if not path.startswith("/"):
        # Relative target: the browser resolves it against the current URL,
        # which is already inside the prefix — leave it as-is.
        return value
    out = f"{prefix}{path}"
    return f"{out}?{query}" if query else out


# Both shapes are registered: without the second route, a request for
# `/proto/{pid}/{slug}` (no trailing slash) misses the `{path:path}` pattern and
# Starlette answers with an ABSOLUTE 307 to its own origin — the browser then
# leaves the public host for localhost:8000 and hangs.
@router.api_route("/proto/{pid}/{slug}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_prototype_root(pid: str, slug: str, request: Request):
    return await proxy_prototype(pid, slug, "", request)


@router.api_route("/proto/{pid}/{slug}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_prototype(pid: str, slug: str, path: str, request: Request):
    import pathfinder.app as app_module
    info = app_module.proto_host().status(pid, slug)
    if info is None or info.state != "running" or info.port is None:
        return PlainTextResponse(
            "prototype not running — start hosting first", status_code=502)

    url = f"http://127.0.0.1:{info.port}/{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _STRIP_REQUEST_HEADERS}
    req = client.build_request(request.method, url,
                               params=request.query_params,
                               content=request.stream(),
                               headers=headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return PlainTextResponse(
            "prototype not responding — check hosting logs", status_code=502)

    async def _close() -> None:
        await upstream.aclose()
        await client.aclose()

    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in _STRIP_RESPONSE_HEADERS}
    # A prototype that redirects (SPA route normalization, auth bounce, its own
    # trailing-slash handling) names its own internal origin — rewrite it so the
    # browser stays on the public proxy path.
    if "location" in resp_headers:
        resp_headers["location"] = _rewritten_location(
            resp_headers["location"], pid, slug)
    return StreamingResponse(upstream.aiter_raw(),
                             status_code=upstream.status_code,
                             headers=resp_headers,
                             background=BackgroundTask(_close))
