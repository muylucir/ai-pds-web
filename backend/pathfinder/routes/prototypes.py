# backend/pathfinder/routes/prototypes.py — prototype build sessions + hosting.
#
# REST + SSE for the prototype tab: session lifecycle against an in-process
# build agent (PrototypeSession) and local hosting (ProtoHost) -- start/stop a
# build, stream its events, and start/stop/status the local server that serves
# the built output. The public-facing piece that actually exposes a hosted
# prototype to survey respondents -- the streaming reverse proxy under
# /proto/{pid}/{slug} -- lives in proto_public.py, split out so this file's
# routes can be gated behind a login requirement wholesale while that one
# stays open (the gating itself lands in a later task, not here).
from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from pathfinder.models import AgentEvent
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.proto.session import purge_session_state
from pathfinder.survey.store import responses_prefix

_log = logging.getLogger(__name__)

router = APIRouter()

_SPEC_PREFIX = "aiplc-docs/discovery/prototypes/"
_SPEC_RE = re.compile(r"^aiplc-docs/discovery/prototypes/([^/]+)/PROTOTYPE-\1\.md$")

# The frontend opens the first events stream with this sentinel; the route
# substitutes session.first_prompt() so the build kicks off as a normal
# SSE-relayed turn (spec §4: 첫 턴 자동 발화).
_FIRST_TURN_SENTINEL = "__first__"

#: Statuses that mean the agent has work in flight, for the LIST's display
#: state only. Deliberately excludes "ready": PrototypeSession sets that on the
#: turn's `done` event, and it means ready for ANOTHER turn -- the session stays
#: open so the user can ask for changes -- not still building. Including it
#: pinned the card at 빌드 중 forever, because the "building" branch sits ahead
#: of the `built` check and nothing evicts a finished session from
#: proto_sessions (only a retry or an explicit DELETE does), so every reload
#: re-derived the same answer and 빌드 완료/실행 were unreachable.
#:
#: NOT the same question as "is a session live" -- a "ready" session IS live and
#: must still block a second start (409) and serve its event stream. That is
#: `_live_session`/`_DEAD_STATUSES` below, kept separate precisely because one
#: set answering both questions is what let this ship.
_WORKING_STATUSES = {"starting", "building", "waiting_input"}


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

def _prototype_dir(pid: str, slug: str) -> Path:
    """The served tree: {proto_root}/{pid}/{slug}/prototype.

    One function for both readers -- `_local_build_exists` (is it built?) and
    `start_host` (what do we run?). They used to spell this path separately and
    drifted: hosting ran the build dir one level up, where the only file is the
    spec .md, so `npm` died with ENOENT on package.json and the route turned
    that into a 502 while the card said 빌드 완료.
    """
    import pathfinder.app as app_module
    return app_module._proto_root() / pid / slug / "prototype"


def _local_build_exists(pid: str, slug: str) -> bool:
    """A finished build lives under prototype/ inside the LOCAL build
    directory now -- the in-process builder writes straight there and ProtoHost
    serves it in place (no more VM -> S3 bundle sync). We deliberately check
    prototype/, not just the build dir's own existence: PrototypeSession.start()
    seeds the build dir with the spec .md file (and possibly .proto-host.log/
    .pid from a prior hosting attempt) before the agent does anything, so a
    build dir that exists but has no prototype/ subtree just means a session
    STARTED, not that anything was BUILT. Only checking the immediate children
    of prototype/ (not a full recursive scan) keeps this cheap on every list
    call even once node_modules/.next show up in there."""
    proto_dir = _prototype_dir(pid, slug)
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


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
        # The local build dir is the primary signal now -- hosting serves it
        # in place and nothing writes the S3 bundle/ prefix anymore (that was
        # the deleted MicroVM's job). Keep the S3 check too as a fallback: a
        # redeployed box could in principle have only a bundle backup and no
        # local dir.
        built = (_local_build_exists(pid, slug)
                 or bool(await s3.list(f"prototypes/{slug}/bundle/")))

        if session is not None and session.status in _WORKING_STATUSES:
            state = "building"
        elif host_info is not None and host_info.state == "running":
            state = "running"
            port = host_info.port
        elif built:
            state = "built"
        elif session is not None and session.status == "failed":
            state = "failed"

        # Rides the list so the reset confirmation can name the number of
        # answers about to be destroyed without a second round trip.
        response_count = len(await s3.list(responses_prefix(slug)))

        out.append({"slug": slug, "spec_path": spec_path,
                    "state": state, "port": port,
                    "response_count": response_count})
    # Capacity travels with the list so a card can explain a 429 before the
    # user clicks (the cap is new -- MicroVM builds had no ceiling).
    return {"prototypes": out, **app_module.build_semaphore.snapshot()}


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

    # In-process builds share one box: each session holds a claude subprocess
    # that may spawn a peak-2GB `next build`. Refuse rather than queue, and
    # name the situation -- a bare 429 reads as a bug to an attendee.
    if not app_module.build_semaphore.try_acquire():
        raise HTTPException(
            status_code=429,
            detail="다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요")

    session = app_module.proto_session_factory(pid, slug)
    try:
        await session.start()
    except FileNotFoundError:
        app_module.build_semaphore.release()
        raise HTTPException(status_code=404, detail="prototype spec not found")
    except Exception:
        # A failed start must not burn a slot permanently.
        app_module.build_semaphore.release()
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


@router.delete("/projects/{pid}/prototypes/{slug}", status_code=204)
async def reset_prototype(pid: str, slug: str):
    """Wipe everything this prototype has accumulated EXCEPT its spec.

    Keeping the spec is what makes this a reset rather than a deletion: the
    list is built by scanning specs, so the card comes back as a fresh
    buildable prototype instead of disappearing.

    Live session and hosting are cleaned up rather than refused -- the point of
    one button is that the user does not have to close things first. Unlike
    `close_session`, a missing session is the normal case (a finished build has
    already been evicted), so absence is not a 404.

    Every gate below protects the same property: no failure path may leave
    state a retry cannot fix.

    survey MUST run first AND succeed before session-state runs at all -- not
    because the two are independent and ordering is merely tidy, but because
    they are NOT independent. SurveyStore.purge() can only discover this
    prototype's tokens by READING the questionnaires under
    prototypes/{slug}/survey/ (that is the only place `surveys/by-token/` --
    a one-way, root-scoped index -- can be reverse-looked-up from).
    purge_session_state() deletes prototypes/{slug}/ wholesale, a SUPERSET of
    that tree. Running session-state after a failed survey purge destroys the
    very questionnaires the next retry would need to reclaim the token index,
    stranding it permanently: a later retry's `_collect_tokens()` finds
    nothing, deletes nothing, and reports success (204) over a token that
    still resolves to this prototype. A rebuild that reuses the slug then
    hands that stale token a live credential into the NEW survey -- the exact
    reversal this whole endpoint exists to prevent, arriving through a side
    door.

    The local purge is gated the same way, one step further down: it is
    IRREVERSIBLE and is the one thing keeping the card at "built" (visibly
    incomplete) rather than "none" (looks finished, even though S3 may still
    hold the survey or session state the button promised to clear). It only
    runs once every S3 step above -- survey AND session-state -- has actually
    succeeded.

    Skipping a later step costs nothing: every purge here is idempotent and,
    if skipped, simply untouched, so a retry converges. That is also why
    failures are collected rather than raised on the spot -- a partial
    failure must still leave a state the next call can finish cleanly.
    """
    import pathfinder.app as app_module
    _require_registered(pid)

    failures: list[str] = []

    session = app_module.proto_sessions.pop((pid, slug), None)
    if session is not None:
        try:
            await session.close()
        except Exception:
            _log.exception("reset: session close failed: %s/%s", pid, slug)
            failures.append("session")

    try:
        await app_module.survey_store_factory(pid, slug).purge()
    except Exception:
        _log.exception("reset: survey purge failed: %s/%s", pid, slug)
        failures.append("survey")

    # session-state deletes prototypes/{slug}/ wholesale -- a superset of the
    # survey tree survey.purge() just read from. Running it after a failed
    # survey purge would destroy the questionnaires a retry needs to reclaim
    # the token index, stranding it permanently (see docstring).
    if not failures:
        try:
            await purge_session_state(
                app_module.s3_store_factory(pid), slug)
        except Exception:
            _log.exception("reset: session-state purge failed: %s/%s", pid, slug)
            failures.append("session-state")

    if not failures:
        try:
            await app_module.proto_host().purge(pid, slug)
        except Exception:
            _log.exception("reset: build-tree purge failed: %s/%s", pid, slug)
            failures.append("build-tree")

    if failures:
        raise HTTPException(
            status_code=502,
            detail=f"초기화가 완료되지 않았습니다({', '.join(failures)}) — 다시 시도해 주세요")
    return Response(status_code=204)


# ---- handoff archive ----

# Never shipped to the dev team: build artifacts (reproducible, huge), our own
# host bookkeeping, and -- from the S3 fallback -- the survey and transcript
# subtrees, which share the prototypes/{slug}/ prefix with the bundle but are
# anonymous respondents' words and build chatter respectively.
_ARCHIVE_EXCLUDED_DIRS = {"node_modules", ".next", ".git"}
_ARCHIVE_EXCLUDED_FILES = {".proto-host.log", ".proto-host.pid"}


def _archive_excluded(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if any(p in _ARCHIVE_EXCLUDED_DIRS for p in parts):
        return True
    return parts[-1] in _ARCHIVE_EXCLUDED_FILES if parts else True


def _archive_filename_header(slug: str) -> str:
    """RFC 6266/5987. A Korean slug raw-interpolated into a latin-1 header
    raises UnicodeEncodeError (500) -- same fix as artifacts.py."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "prototype"
    utf8 = quote(f"{slug}-prototype.zip", safe="")
    return (f'attachment; filename="{safe}-prototype.zip"; '
            f"filename*=UTF-8''{utf8}")


async def _archive_entries(pid: str, slug: str) -> list[tuple[str, bytes]]:
    """Prefer the local build directory -- it is the authoritative copy the
    agent wrote and hosting serves. The S3 bundle is the fallback for a box
    whose disk was wiped by a redeploy."""
    import pathfinder.app as app_module

    build_dir = app_module._proto_root() / pid / slug
    if build_dir.is_dir():
        entries = []
        for path in sorted(build_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(build_dir).as_posix()
            if _archive_excluded(rel):
                continue
            entries.append((rel, path.read_bytes()))
        if entries:
            return entries

    s3 = app_module.s3_store_factory(pid)
    bundle_prefix = f"prototypes/{slug}/bundle/"
    entries = []
    for key in await s3.list(bundle_prefix):
        rel = key[len(bundle_prefix):]
        if _archive_excluded(rel):
            continue
        entries.append((rel, await s3.get_bytes(key)))
    return entries


@router.get("/projects/{pid}/prototypes/{slug}/archive")
async def download_prototype_archive(pid: str, slug: str):
    """The dev-team handoff: prototype source as a zip. Binary-safe (bytes
    straight into the zip), so images and fonts survive."""
    _require_registered(pid)
    entries = await _archive_entries(pid, slug)
    if not entries:
        raise HTTPException(status_code=404, detail="prototype bundle not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries:
            zf.writestr(rel, content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _archive_filename_header(slug)},
    )


# ---- hosting ----

@router.post("/projects/{pid}/prototypes/{slug}/host")
async def start_host(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    # Hosting serves the build directory IN PLACE now, so starting it under a
    # live build session would race the agent writing into that same tree.
    if _live_session(pid, slug) is not None:
        raise HTTPException(
            status_code=409,
            detail="빌드 세션이 진행 중입니다 — 세션을 먼저 종료해 주세요")
    # Pass cwd explicitly: ProtoHost's default is {root}/{pid}/{slug}, one level
    # ABOVE the served tree. That dir exists as soon as a session starts (it
    # holds the spec .md), so the host's own is_dir() guard passes and the miss
    # only surfaces as `npm error ENOENT ... package.json` -> 502.
    # `public_base_path`, NOT `proxy_prefix`: basePath is baked into asset URLs
    # that the BROWSER resolves, and the browser's path carries the `/api` mount
    # that Next's route handler strips before this app sees it. Imported rather
    # than re-formatted here -- two spellings of a build-time constant is the
    # same class of bug as the cwd/prototype mismatch above.
    from pathfinder.routes.proto_public import public_base_path
    try:
        info = await app_module.proto_host().start(
            pid, slug, cwd=_prototype_dir(pid, slug),
            base_path=public_base_path(pid, slug))
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
