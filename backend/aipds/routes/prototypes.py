# backend/aipds/routes/prototypes.py — prototype build sessions + hosting.
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

import asyncio
import io
import logging
import re
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.models import AgentEvent
from aipds.parsers.redaction import redact_credentials
from aipds.pathsafe import reject_unsafe_segment
from aipds.proto.design_sync import sync_design, theme_copies
from aipds.proto.session import has_build_output, purge_session_state
# Path assembly for the token gate belongs to the module that owns that route --
# rewriting it here as an f-string would put the browser-facing mount (`/api`) in two
# places, which is the class of bug that already went out of step once in this file
# (see start_host's public_base_path comment below). proto_public imports aipds.app
# only inside functions, so a top-level import here creates no cycle.
from aipds.routes.proto_public import access_url_path
from aipds.survey.store import survey_summary
from aipds.proto import layout as proto_layout

_log = logging.getLogger(__name__)


def _reject_traversal_params(request: Request) -> None:
    """Router-wide guard: every {pid}/{slug} in this file must be ONE ordinary
    path segment.

    Both values are interpolated into filesystem paths (`_prototype_dir`,
    `ProtoHost.start`/`purge`: `{proto_root}/{pid}/{slug}`) and into S3 key
    prefixes, and `pathlib` does NOT normalise -- `root / pid / ".."` really
    resolves to root's parent, and `root / pid / "."` really resolves to
    `root / pid`. Reset then `rmtree`s it, so an unvalidated slug of ".."
    deletes EVERY project's build tree and "." deletes every sibling
    prototype of one project, both answering 204.

    Starlette will not route a literal `..` segment (it normalises dot
    segments), but the percent-encoded forms `%2e%2e` / `%2E%2E` arrive
    already decoded in `path_params` and route fine -- verified directly. So
    the check has to sit here, after decoding, not in a URL-shape assumption.

    A router-level dependency rather than a call inside `reset_prototype`
    deliberately: reset is only the IRREVERSIBLE consumer, not the only one.
    `start_host` would `npm install` in the wrong tree, the archive route
    would zip a sibling, and the session routes key S3 state off the same
    value. One guard on the router covers every current and future route in
    this file, and cannot be forgotten by the next one added.

    404, matching how the rest of this file reports an input that names
    nothing addressable (`_require_registered`, `_require_session`) -- and
    matching what Starlette already returns for the un-encoded spelling, so
    the two spellings of the same attack stop looking different from outside.
    """
    for name in ("pid", "slug"):
        value = request.path_params.get(name)
        if value is None:
            continue
        try:
            reject_unsafe_segment(value)
        except ValueError:
            _log.warning("rejected unsafe %s in prototype route: %r", name, value)
            raise HTTPException(status_code=404, detail=f"invalid {name}")


router = APIRouter(dependencies=[Depends(_reject_traversal_params)])

# The layout convention is owned solely by proto/layout.py. This prefix and regex
# used to drive card discovery here, with f-strings assembling the same path scattered
# across three more places -- and because of that duplication, the defect of not
# recognising Path A.1's singular layout became a fix in four places at once (see that
# module's header).

# The frontend opens the first events stream with this sentinel; the route
# substitutes session.first_prompt() so the build kicks off as a normal
# SSE-relayed turn (spec §4: the first turn speaks automatically).
_FIRST_TURN_SENTINEL = "__first__"


class TurnBody(BaseModel):
    text: str


def _handle_scope(pid: str, slug: str) -> str:
    """The ownership key for a turn handle. The project alone is not enough: several
    prototypes in one project each have their own session, and without the slug a
    handle from another prototype could open a turn on this session."""
    return f"{pid}/{slug}"

#: Statuses that mean the agent has work in flight, for the LIST's display
#: state only. Deliberately excludes "ready": PrototypeSession sets that on the
#: turn's `done` event, and it means ready for ANOTHER turn -- the session stays
#: open so the user can ask for changes -- not still building. Including it
#: pinned the card at "building" forever, because the "building" branch sits ahead
#: of the `built` check and nothing evicts a finished session from
#: proto_sessions (only a retry or an explicit DELETE does), so every reload
#: re-derived the same answer and the "built"/"run" states were unreachable.
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
    import aipds.app as app_module
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")


#: A session in one of these terminal states is dead: it must NOT block a new
#: start (409) and must NOT be served as an active stream (404). Keeping
#: "failed" out of this set wedged the prototype permanently — POST said
#: "already active" while GET said "no active session", so the user could
#: neither restart nor stream.
#:
#: "complete" belongs here for the same reason and fixes four routes at once:
#: the agent declared the build finished and stopped touching the build tree,
#: so POST /host must no longer 409 (the card already says "built" --
#: "ready" is not in _WORKING_STATUSES), POST /session must be allowed so
#: "continue improving" can open a fresh session, and /answers + /interrupt must
#: 404 because the pending-question future they would resolve is gone.
#: The session may still be in `proto_sessions` at that moment — it closes
#: itself a few seconds later via the idle timer (proto/session.py's
#: _COMPLETION_GRACE_SECONDS) — so this set, not the dict, is what makes it
#: harmless. ProtoHost.start() does not wipe the build tree (proto/host.py's
#: "NOT rmtree" note), so hosting inside that grace window is safe.
_DEAD_STATUSES = ("closed", "failed", "complete")


def _live_session(pid: str, slug: str):
    import aipds.app as app_module
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
    that into a 502 while the card said the build was complete.
    """
    import aipds.app as app_module
    return app_module._proto_root() / pid / slug / "prototype"


def _local_build_exists(pid: str, slug: str) -> bool:
    """A finished build lives under prototype/ inside the LOCAL build
    directory now -- the in-process builder writes straight there and ProtoHost
    serves it in place (no more VM -> S3 bundle sync).

    The judgement itself is `proto/session.py`'s `has_build_output` -- the one
    definition of "is it built?", shared with the build_complete tool and with
    the opening prompt (which has to tell the agent to REBUILD when the tree is
    gone rather than go looking for code that no longer exists). This wrapper
    only adapts the input shape: pid/slug instead of a build dir."""
    return has_build_output(_prototype_dir(pid, slug).parent)


@router.get("/projects/{pid}/prototypes")
async def list_prototypes(pid: str):
    import aipds.app as app_module
    _require_registered(pid)
    s3 = app_module.s3_store_factory(pid)

    slugs = proto_layout.discover(await s3.list(proto_layout.DISCOVERY_PREFIX))

    host = app_module.proto_host()
    ordered = sorted(slugs.items())
    # **Walk the per-slug S3 round trips in parallel up front.** This used to await
    # `s3.list(bundle)` and the survey lookup sequentially inside the loop, one card at
    # a time, which is 2N round trips for N cards -- measured (2026-08-17) at 30ms per
    # round trip, so ten cards added 0.6s straight onto the list request. gather
    # returns results in input order, which is what makes the zip below safe.
    bundle_lists, surveys = await asyncio.gather(
        asyncio.gather(*(s3.list(f"prototypes/{slug}/bundle/")
                         for slug, _ in ordered)),
        asyncio.gather(*(survey_summary(s3, slug) for slug, _ in ordered)),
    )
    out = []
    for (slug, spec_path), bundle_keys, survey in zip(
            ordered, bundle_lists, surveys):
        state = "none"
        port: int | None = None

        session = app_module.proto_sessions.get((pid, slug))
        host_info = host.status(pid, slug)
        # The local build dir is the primary signal now -- hosting serves it
        # in place and nothing writes the S3 bundle/ prefix anymore (that was
        # the deleted MicroVM's job). Keep the S3 check too as a fallback: a
        # redeployed box could in principle have only a bundle backup and no
        # local dir.
        built = _local_build_exists(pid, slug) or bool(bundle_keys)

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
        # answers about to be destroyed without a second round trip, and so a
        # card can say "no survey" at all.
        #
        # **`has_survey` is a different question from `response_count > 0`.** It is
        # 0 both when there is no survey and when a survey exists with no responses
        # yet, so the card could not tell the two apart -- measured in test2222, only
        # 1 of 3 prototypes had a survey, and with the screen not saying so there was
        # no way to notice the other two were missing one. Both values come out of a
        # **single list** in `survey_summary`, so the extra field costs no extra round
        # trip.
        #
        # Delegated to survey/store.py rather than counted here from
        # `responses_prefix`: that prefix is the CURRENT round only, but
        # SurveyStore.purge() deletes the whole survey/ tree including
        # `archive/{closed_at}/responses/`, where archive_current() files each
        # previous round's answers. Counting the live prefix reported 0 for a
        # regenerated survey holding 12 real submissions -- so the dialog showed
        # no count and no irreversibility warning, then destroyed all 12. The
        # definition of "a response a reset destroys" belongs to the module that
        # owns those keys, so the two cannot drift apart again.
        # The shareable access URL. It rides along **only while running**: in any
        # other state the gate answers this link with a 502, so its absence is what
        # tells the frontend whether to surface the link at all (the same rule as the
        # existing shareUrl condition).
        #
        # `token_for`, not `ensure_token`: a GET must not have the side effect of
        # minting a credential. Issuing happens in exactly one place, when hosting
        # starts (start_host) -- if listing minted tokens, a token file would be laid
        # down even for a prototype that was never hosted.
        access_url = None
        if state == "running":
            token = host.token_for(pid, slug)
            if token:
                access_url = access_url_path(token)

        out.append({"slug": slug, "spec_path": spec_path,
                    "state": state, "port": port,
                    "access_url": access_url,
                    "response_count": survey.responses,
                    "has_survey": survey.exists})
    # Capacity travels with the list so a card can explain a 429 before the
    # user clicks (the cap is new -- MicroVM builds had no ceiling).
    return {"prototypes": out, **app_module.build_semaphore.snapshot()}


# ---- build session lifecycle ----

@router.post("/projects/{pid}/prototypes/{slug}/session", status_code=202)
async def start_session(pid: str, slug: str):
    import aipds.app as app_module
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
            detail=ec.BUILD_SLOTS_BUSY)

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


@router.post("/projects/{pid}/prototypes/{slug}/turns")
async def create_session_turn(pid: str, slug: str, body: TurnBody):
    """Take the build chat text in the **body** and return a short handle.

    The same reason as the workspace chat (create_turn in routes/turns.py):
    EventSource supports GET only and cannot carry a body, and long input in the URL
    makes a proxy return 431 (measured in aipds/turn_handles.py's header).

    Session existence is checked here so an absent one ends as a 404: if the client got
    a handle and then hit a 404 on the stream, all the user would see is "the
    connection dropped".
    """
    import aipds.app as app_module
    _require_registered(pid)
    _require_session(pid, slug)
    return {"turn_id": app_module.turn_handles.create(
        _handle_scope(pid, slug), {"text": body.text})}


@router.get("/projects/{pid}/prototypes/{slug}/events")
async def stream_session_events(pid: str, slug: str,
                                turn: str | None = None,
                                text: str | None = None):
    import aipds.app as app_module
    _require_registered(pid)
    session = _require_session(pid, slug)
    if turn is not None:
        payload = app_module.turn_handles.consume(_handle_scope(pid, slug), turn)
        if payload is None:
            # Expired, reused, or from another session -- which one is not
            # disclosed.
            raise HTTPException(status_code=400,
                                detail="turn handle is unknown or already used")
        text = payload["text"]
    elif text is None:
        # Quietly running an empty turn leaves the user looking at a bubble with no
        # response and no way to tell why.
        raise HTTPException(status_code=400,
                            detail="either `turn` or `text` is required")
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
    import aipds.app as app_module
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

    The session is evicted from `proto_sessions` only AFTER close() succeeds.
    Evicting first (the original order) made a failed close unretryable: the
    retry saw no session -- indistinguishable from the normal finished-build
    case -- and answered 204 while the build slot close() releases stayed held
    until the process restarted. The entry itself is the only handle a retry has
    on that slot.

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
    import aipds.app as app_module
    _require_registered(pid)

    failures: list[str] = []

    # Read, close, THEN evict -- the eviction is the last step, not the first.
    # Popping up front made a failed close() unretryable: the session was gone
    # from the registry, so the retry saw "no session" (the normal case) and
    # answered 204 while the build slot close() releases stayed held until the
    # process restarted. With AIPDS_PROTO_MAX_CONCURRENT capping a workshop
    # box at 2, one leaked slot is a real 429 for another team, and this route's
    # own contract is that no failure path may leave state a retry cannot fix.
    # Leaving the entry in place also keeps close()'s own idempotence guard
    # (session.py's `_closed`/`_slot_released`) as the thing that stops a second
    # attempt from double-releasing.
    session = app_module.proto_sessions.get((pid, slug))
    if session is not None:
        try:
            await session.close()
        except Exception:
            _log.exception("reset: session close failed: %s/%s", pid, slug)
            failures.append("session")
        else:
            del app_module.proto_sessions[(pid, slug)]

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
            detail=f"{ec.INIT_INCOMPLETE}:{','.join(failures)}")
    return Response(status_code=204)


# ---- handoff archive ----

# Never shipped to the dev team: build artifacts (reproducible, huge), our own
# host bookkeeping, and -- from the S3 fallback -- the survey and transcript
# subtrees, which share the prototypes/{slug}/ prefix with the bundle but are
# anonymous respondents' words and build chatter respectively.
#
# `.proto-token` is a CREDENTIAL, not bookkeeping: it is the access token that
# gates this prototype's public preview (proto/host.py's TOKEN_FILENAME). It
# sits in this exact directory -- a sibling of the .proto-host.* files that
# `_archive_entries` walks -- so leaving it out of this set would mail the live
# access token to everyone who clicks "download", which is precisely the
# audience the token exists to gate.
_ARCHIVE_EXCLUDED_DIRS = {"node_modules", ".next", ".git"}
_ARCHIVE_EXCLUDED_FILES = {".proto-host.log", ".proto-host.pid", ".proto-token"}


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
    import aipds.app as app_module

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
    import aipds.app as app_module
    _require_registered(pid)
    # Hosting serves the build directory IN PLACE now, so starting it under a
    # live build session would race the agent writing into that same tree.
    if _live_session(pid, slug) is not None:
        raise HTTPException(
            status_code=409,
            detail=ec.BUILD_SESSION_ACTIVE)
    # Pass cwd explicitly: ProtoHost's default is {root}/{pid}/{slug}, one level
    # ABOVE the served tree. That dir exists as soon as a session starts (it
    # holds the spec .md), so the host's own is_dir() guard passes and the miss
    # only surfaces as `npm error ENOENT ... package.json` -> 502.
    # `public_base_path`, NOT `proxy_prefix`: basePath is baked into asset URLs
    # that the BROWSER resolves, and the browser's path carries the `/api` mount
    # that Next's route handler strips before this app sees it. Imported rather
    # than re-formatted here -- two spellings of a build-time constant is the
    # same class of bug as the cwd/prototype mismatch above.
    from aipds.routes.proto_public import public_base_path
    # Refresh the brand theme just before the rebuild. Hosting runs `npm run build`
    # over the existing tree without an rmtree (proto/host.py), so rewriting only the
    # theme file here changes colours, type and corner radii without touching a line
    # of code -- the only path by which an already-finished prototype gets rebranded
    # without an improvement session.
    #
    # Why it lives at this call site rather than inside ProtoHost: that class is
    # general-purpose hosting and knows nothing about S3 or about brands.
    #
    # A session mid-build is not blocked separately here -- the `_live_session` guard
    # just above already turns starting/building/waiting_input/ready into a 409.
    # Reaching this point is itself the statement that nobody is writing to this tree
    # right now.
    build_dir = _prototype_dir(pid, slug).parent
    try:
        profile = await app_module.design_profile_store().load()
        sync_design(build_dir, profile, app_module.project_language(pid))
        # sync_design only ever *refreshes*: a prototype built **before** the
        # profile was uploaded has no theme copy under prototype/, so nothing is
        # replaced and it stays unbranded even after a rehost (the one case where
        # "rebrand by rehosting alone" does not hold). The screen
        # (admin.designSubtitle) now states that limit accurately, but an operator
        # also has to be able to tell at the time of this request why nothing
        # happened -- namely that it takes one improvement session to take effect.
        if profile is not None and not theme_copies(build_dir):
            _log.warning(
                "design profile present but %s/%s has no theme copy under "
                "prototype/ -- re-hosting cannot re-brand it; an improvement "
                "session must run once to import aipds-theme.css first",
                pid, slug)
    except Exception:
        # A failure to apply the brand does not block hosting itself -- the screen
        # opening matters more than its colours. The cause goes to the log.
        _log.exception("design sync before host failed: %s/%s", pid, slug)
    try:
        info = await app_module.proto_host().start(
            pid, slug, cwd=_prototype_dir(pid, slug),
            base_path=public_base_path(pid, slug),
            # Same source as the build agent and Discovery (app.project_model): the
            # prototype app's own runtime LLM calls should also run on the model the
            # project chose. If the three used different values, there would be no way
            # to say where the user's chosen model actually applies.
            #
            # The region is not injected: the backend does not pass a Bedrock region
            # explicitly either, leaving it to boto3/SDK's default resolution (the
            # instance region, AWS_REGION). The prototype inherits the backend env via
            # `{**os.environ, ...}` and so follows the same resolution -- inventing a
            # separate convention here would let the backend and the prototype see
            # different regions.
            model_id=app_module.project_model(pid))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype bundle not found")
    if info.state == "failed":
        raise HTTPException(status_code=502, detail=info.log_tail)
    # The access token is issued **here and nowhere else**. A preview link only
    # means anything once hosting has actually started, so minting it earlier would
    # leave a credential on disk that nothing uses. Because it is `ensure_token`, the
    # value survives repeated stop -> start -- a link already handed out must not die
    # because hosting was toggled during a workshop. The intended path for revoking a
    # link is a reset, and there purge() removes the token too.
    token = app_module.proto_host().ensure_token(pid, slug)
    return {"state": info.state, "port": info.port, "log_tail": info.log_tail,
            "access_url": access_url_path(token)}


@router.get("/projects/{pid}/prototypes/{slug}/host")
async def host_status(pid: str, slug: str):
    import aipds.app as app_module
    _require_registered(pid)
    info = app_module.proto_host().status(pid, slug)
    if info is None:
        raise HTTPException(status_code=404, detail="not hosted")
    # A GET does not mint a token (`token_for`): if a read had the side effect of
    # creating a credential, a token would be laid down even for a prototype that was
    # never hosted. None when there is none yet, and the frontend then does not
    # surface the link.
    token = app_module.proto_host().token_for(pid, slug)
    return {"state": info.state, "port": info.port,
            "access_url": access_url_path(token) if token else None,
            "log_tail": app_module.proto_host().log_tail(pid, slug)}


@router.delete("/projects/{pid}/prototypes/{slug}/host", status_code=204)
async def stop_host(pid: str, slug: str):
    import aipds.app as app_module
    _require_registered(pid)
    await app_module.proto_host().stop(pid, slug)
    return Response(status_code=204)
