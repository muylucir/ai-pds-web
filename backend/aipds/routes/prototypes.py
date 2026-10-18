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
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.models import AgentEvent
from aipds.parsers.proto_spec import spec_name
from aipds.parsers.redaction import redact_credentials
from aipds.launcher import LauncherUnavailable
from aipds.pathsafe import reject_unsafe_segment
from aipds.proto.session import has_build_output, purge_session_state
from aipds.proto.history import load_context, load_history, record_opening
from aipds.turn_job import TurnBusy, subscribe
from aipds.proto.hosting import host_prototype, prototype_store, stop_prototype
from aipds.proto.store import PrototypeStore
from aipds.proto.source import newest_source_mtime, source_entries
# The module that owns the token-gate route also assembles its path -- rewriting
# it here with an f-string would mean managing the browser-side mount (`/api`)
# in two places, which is exactly the kind of bug that already drifted once in
# this file (see the public_base_path comment in start_host below). proto_public
# imports aipds.app only inside functions, so a top-level import here does not
# create a cycle.
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

# proto/layout.py is the sole owner of the layout rules -- this prefix and
# regex used to drive card discovery here, and f-strings assembling the same
# path were scattered across three more places. Because of that duplication, the
# defect of not recognising Path A.1's singular layout had to be fixed in four
# places at once (see that module's header).

# The frontend opens the first events stream with this sentinel; the route
# substitutes session.first_prompt() so the build kicks off as a normal
# SSE-relayed turn (spec §4: the first turn is sent automatically).
_FIRST_TURN_SENTINEL = "__first__"


class TurnBody(BaseModel):
    text: str


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
#: so POST /host must no longer 409 (the card already says 빌드 완료 —
#: "ready" is not in _WORKING_STATUSES), POST /session must be allowed so
#: "개선 이어서 하기" can open a fresh session, and /answers + /interrupt must
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
    that into a 502 while the card said 빌드 완료.
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


async def _card_name(s3, spec_path: str) -> str | None:
    """Read the name to show on the card from the spec body. None if it cannot be read.

    **Swallowing the failure is right here.** The name is decoration on the
    card; state and buttons do not depend on it -- letting one name turn the
    whole Prototypes tab into a 500 would be choosing the worse outcome. When
    absent, the caller falls back to the slug. `discover` finds keys with
    `list` and this reads them with `get`, so a reset or delete landing in
    between is enough for this failure to happen in normal operation.

    Still, it is not swallowed **silently**: "the card shows no name" must be
    diagnosable from a single log line.
    """
    try:
        return spec_name(await s3.get(spec_path))
    except Exception:
        _log.warning("could not read prototype name from %s", spec_path,
                     exc_info=True)
        return None


async def _has_s3_source(store: PrototypeStore, slug: str) -> bool:
    """Does S3 hold a source generation? If it cannot be read, treat it as absent --
    the list does not break over one card's S3 error (same discipline as
    `_card_name`). If a local tree exists, the card becomes `built` through that
    anyway."""
    try:
        return await store.has_source(slug)
    except Exception:
        _log.warning("could not read prototype source generation for %s", slug,
                     exc_info=True)
        return False


@router.get("/projects/{pid}/prototypes")
async def list_prototypes(pid: str):
    import aipds.app as app_module
    _require_registered(pid)
    s3 = app_module.s3_store_factory(pid)

    slugs = proto_layout.discover(await s3.list(proto_layout.DISCOVERY_PREFIX))

    host = app_module.proto_host()
    ordered = sorted(slugs.items())
    # **Run the per-slug S3 round trips up front, in parallel.** The loop used to
    # await `s3.list(bundle)` and the survey lookup sequentially for each card,
    # so N cards cost 2N round trips -- measured (2026-08-17): one round trip is
    # 30ms, so 10 cards add 0.6s straight onto the list call. gather returns
    # results in input order, so the zip below is safe.
    #
    # The `get` that reads the name is a third round trip per card, but it is
    # **inside the same gather**, so wall-clock time is unchanged -- one card's
    # three round trips do not wait on each other. The alternative to carrying
    # the name in the list response (each card fetches and parses its own spec)
    # moves the round trips to the client and creates two copies of the parsing
    # rules.
    store = PrototypeStore(s3)
    s3_built, surveys, names = await asyncio.gather(
        asyncio.gather(*(_has_s3_source(store, slug) for slug, _ in ordered)),
        asyncio.gather(*(survey_summary(s3, slug) for slug, _ in ordered)),
        asyncio.gather(*(_card_name(s3, spec_path) for _, spec_path in ordered)),
    )
    out = []
    for (slug, spec_path), has_s3_source, survey, name in zip(
            ordered, s3_built, surveys, names):
        state = "none"
        port: int | None = None

        session = app_module.proto_sessions.get((pid, slug))
        host_info = host.status(pid, slug)
        # The local build tree is the primary signal (hosting serves it in place),
        # and the S3 source generation is the source of truth (proto/store.py) -- a
        # replaced instance has no local tree, but the build is still "done".
        # Hosting and session start restore the tree from that generation.
        built = _local_build_exists(pid, slug) or has_s3_source

        # Is a build session open? **Why this is a separate field from `state`**
        # is the point of this block: whether the server is up and whether a
        # session is open are independent facts, but a single enum carried both,
        # so the session masked the hosting. Starting to modify a running
        # prototype flipped the card to `building` and the preview and share
        # links disappeared -- the server stayed up the whole time, so only the
        # screen was lying. Right after viewing the preview is exactly when you
        # want to make changes, and that was the moment the screen took away what
        # you had just been looking at.
        session_open = session is not None and session.status in _WORKING_STATUSES

        # `running` comes before `building`. Opening a session does not touch
        # hosting (no route calls stop), so the server really is alive, and that
        # fact has to be stated first for the card to keep drawing the preview,
        # link, and stop button. On the first build there is no server up, so it
        # is still `building` then.
        if host_info is not None and host_info.state == "running":
            state = "running"
            port = host_info.port
        elif session_open:
            state = "building"
        elif built:
            state = "built"
        elif session is not None and session.status == "failed":
            state = "failed"

        # Rides the list so the reset confirmation can name the number of
        # answers about to be destroyed without a second round trip, and so a
        # card can say "no survey" at all.
        #
        # **`has_survey` is a different question from `response_count > 0`.** The
        # count is 0 when there is no survey and also 0 when a survey exists but
        # has no responses yet, so the card could not tell the two states apart
        # -- measured on ws-n: only 1 of 3 prototypes had a survey, but the
        # screen did not show that, so there was no way to notice the other two
        # were missing one. Both values come from **a single list** call in
        # `survey_summary`, so the extra field adds no round trip.
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
        # Access URL for sharing. Sent **only when running** -- in any other
        # state the gate answers this link with 502, so its absence is what the
        # frontend uses to decide whether to expose the link (same rule as the
        # existing shareUrl condition).
        #
        # `token_for`, not `ensure_token`: a GET must not have the side effect
        # of minting a credential. Issuance happens in exactly one place, hosting
        # start (start_host) -- if the list call minted tokens, token files would
        # be laid down even for prototypes that were never hosted.
        access_url = None
        if state == "running":
            token = host.token_for(pid, slug)
            if token:
                access_url = access_url_path(token)

        # Is the running server older than the source? This window appeared once
        # editing while running became possible -- the server stays up while only
        # the source changes. If the card then says only "실행 :4007" (Running
        # :4007), the user reads it as their edit being live, but the link sent to
        # participants shows the previous version and only changes after
        # re-hosting.
        #
        # **Do not substitute "is a session open".** Then the signal vanishes the
        # moment the session closes, which is precisely when the user is most
        # likely to misread it.
        #
        # Only asked when `running`: an unhosted prototype has no preview to go
        # stale, and it saves the cost of walking the tree (this route is polled
        # -- the same reason `newest_source_mtime` prunes excluded directories).
        preview_stale = False
        if state == "running" and host_info is not None and host_info.built_at:
            newest = newest_source_mtime(app_module._proto_root() / pid / slug)
            preview_stale = newest is not None and newest > host_info.built_at

        # Card title. **A separate field from `slug`** -- the slug is still the
        # identifier (reset, build, and session are keyed by it) and the name is
        # for display. Merging them would overwrite Path A.1's reserved id
        # `prototype` with a name and destabilise the identifier. null when
        # absent: the frontend's fallback-to-slug branch must key on presence of
        # the value.
        out.append({"slug": slug, "name": name, "spec_path": spec_path,
                    "state": state, "port": port,
                    "session_open": session_open,
                    "preview_stale": preview_stale,
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


def _opening_text(session, text: str) -> str:
    """Turn what the user sent into the turn text that goes to the agent.

    A session's **first message is its opening turn**. If it is the sentinel,
    this is an automatic opening where the user said nothing, so use the
    default opening prompt; otherwise the message is the user's request, so
    carry it inside the opening prompt.

    **Why carry the user's request in the opening turn.** Someone who pressed
    "수정하기" (Modify) already knows what they want to change (they saw it in
    the preview), but the opening prompt asked them again, and while that
    question was up the input box was disabled (BuildPanel), so they could not
    even type their request.

    **Raw text must not be sent as-is.** The handoff branch starts with a new
    session_id and has no transcript (proto/session's _resolve_session_id), so
    without the summary the opening prompt carries and its "look at prototype/
    first", the agent starts with no context.

    The point is that the criterion is `session.opened` -- not a UI flag. That
    is a fact the session knows; if the frontend guesses, it goes wrong on
    reload, races, and already-open sessions.
    """
    if text == _FIRST_TURN_SENTINEL:
        return session.first_prompt()
    if not session.opened:
        return session.first_prompt(request=text)
    return text


def _start_build_turn(pid: str, slug: str, session, text: str):
    """Start a build turn as a server-side job (aipds/turn_job.py). 409 if a turn is
    already running -- the body carries that turn's id (same shape as Discovery's
    routes/turns.start_turn)."""
    import aipds.app as app_module
    turn_text = _opening_text(session, text)
    shown = None if text == _FIRST_TURN_SENTINEL else text

    async def events():
        # In an opening turn, what the user said differs from what went to the
        # agent. Recording that pair is what lets history show the user's words
        # instead of the opening prompt (proto/history.py header, item 2).
        # Written **inside** the turn so a turn whose start is refused with 409
        # leaves no record either.
        if turn_text != text:
            try:
                s3 = app_module.s3_store_factory(pid)
            except Exception:
                _log.exception("project store unavailable for %s", pid)
            else:
                await record_opening(s3, slug, prompt=turn_text, shown=shown)
        # Close the inner generator **explicitly**. When a turn is cancelled,
        # TurnJobs acloses this generator, but merely wrapping it in `async for`
        # leaves the inner one open until GC collects it, delaying the session's
        # cleanup (finally) by that much.
        inner = session.send_message(turn_text, user_text=shown)
        try:
            async for ev in inner:
                yield ev
        finally:
            await inner.aclose()

    try:
        return session.turns.start("message", events, input_text=shown)
    except TurnBusy as busy:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": busy.job.id})


def _build_turn_response(job, after: int = 0) -> EventSourceResponse:
    """Stream the turn log from after `after`. Each frame's `id` is the seq. The turn
    keeps running even if the viewing screen disconnects -- on reattach, pass the
    last received seq as `after`."""
    async def gen():
        async for seq, event in subscribe(job, after):
            yield {"id": str(seq), "data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


@router.post("/projects/{pid}/prototypes/{slug}/turns")
async def create_session_turn(pid: str, slug: str, body: TurnBody):
    """**Start** a build turn and return its id. The stream is read via `GET /events?turn=<id>`.

    The text comes in the body for the same reason as workspace chat
    (routes/turns.py): EventSource supports only GET, and a long input carried
    in the URL makes the proxy answer 431 (the measurement is in createTurn in
    frontend lib/api/sse.ts). The automatic opening sends the sentinel
    (`__first__`).
    """
    _require_registered(pid)
    session = _require_session(pid, slug)
    return {"turn_id": _start_build_turn(pid, slug, session, body.text).id}


@router.get("/projects/{pid}/prototypes/{slug}/events")
async def stream_session_events(pid: str, slug: str, turn: str, after: int = 0):
    """Watch one build turn. `POST …/turns` starts turns -- this route only watches."""
    _require_registered(pid)
    session = _require_session(pid, slug)
    job = session.turns.get(turn)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown turn")
    return _build_turn_response(job, after)


@router.get("/projects/{pid}/prototypes/{slug}/session")
async def get_session(pid: str, slug: str):
    """The open build session and its turns. When the build screen reopens (reload,
    closing and reopening the panel), it uses this to replay the conversation
    from the start and attach to the running turn."""
    import aipds.app as app_module
    _require_registered(pid)
    session = app_module.proto_sessions.get((pid, slug))
    if session is None:
        raise HTTPException(status_code=404, detail="no build session")
    return {"status": session.status,
            "turns": [{"turn_id": j.id, "state": j.state,
                       "last_seq": j.log.last_seq, "input": j.input_text}
                      for j in session.turns.all()]}


@router.get("/projects/{pid}/prototypes/{slug}/history")
async def get_history(pid: str, slug: str):
    """Past build conversation (transcript), plus the open session's turns to replay after it.

    The point is returning both in **one response**. The screen replays the open
    session's turns from the turn log (the completion card and the in-progress
    question form exist only on that path), so history is cut just before the
    start of that session's first turn. Asking for the cut-off and the list of
    turns to replay separately would let a turn started in between appear on
    both sides or on neither.

    A closed session is still replayed while it remains in `proto_sessions` --
    that is how the completion card of a session closed by the grace period
    after a completion declaration comes back (same target as GET /session).

    `context` is the context usage of the last turn (only when it still fits
    the next conversation -- proto/history.load_context). If a replayed turn
    streams a `context` event again, the screen overwrites with that value.
    """
    import aipds.app as app_module
    _require_registered(pid)
    session = app_module.proto_sessions.get((pid, slug))
    jobs = session.turns.all() if session is not None else []
    before = min((j.started_wall for j in jobs), default=None)
    try:
        s3 = app_module.s3_store_factory(pid)
    except Exception:
        # Same degradation as Discovery's /history: no store just means empty history.
        _log.exception("project store unavailable for %s", pid)
        items, context = [], None
    else:
        items, context = await asyncio.gather(load_history(s3, slug, before=before),
                                              load_context(s3, slug))
    return {"items": [i.model_dump() for i in items],
            "context": context,
            "turns": [{"turn_id": j.id, "state": j.state,
                       "last_seq": j.log.last_seq, "input": j.input_text}
                      for j in jobs]}


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

    # The preview token's root index (proto/store.py) is in the same position: the
    # token is read from prototypes/{slug}/access-token, so it must be reclaimed
    # **before** session-state below wipes that tree. If left behind, the old link
    # still resolves to the new build after a reset.
    if not failures:
        store = prototype_store(pid)
        if store is not None:
            try:
                await store.forget_token(slug)
            except Exception:
                _log.exception("reset: preview token index purge failed: %s/%s",
                               pid, slug)
                failures.append("preview-token")

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
# gates this prototype's public preview (proto/host.py's TOKEN_FILENAME). Hosting
# keeps its copy in the state tree, outside the build directory, but a build
# tree can still carry a file under that name (written before hosting moved it,
# or by the agent) -- leaving it out of this set would mail a live access token
# to everyone who clicks "download", which is precisely the audience the token
# exists to gate.
#
# The set itself lives in project_bundle.py, and the walk that applies it lives
# in proto/source.py, because the project bundle asks this exact question too.
# This is the one exclusion list whose failure modes are asymmetric: forgetting a
# build artifact makes a zip bigger, forgetting the token leaks a live
# credential. Two copies is one chance for the copy that matters to drift.


def _archive_filename_header(slug: str) -> str:
    """RFC 6266/5987. A Korean slug raw-interpolated into a latin-1 header
    raises UnicodeEncodeError (500) -- same fix as artifacts.py."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "prototype"
    utf8 = quote(f"{slug}-prototype.zip", safe="")
    return (f'attachment; filename="{safe}-prototype.zip"; '
            f"filename*=UTF-8''{utf8}")


async def _archive_entries(pid: str, slug: str) -> list[tuple[str, bytes]]:
    """Source for this project/slug. proto/source.py owns the collection rules."""
    import aipds.app as app_module

    return await source_entries(
        build_dir=app_module._proto_root() / pid / slug,
        s3=app_module.s3_store_factory(pid), slug=slug)


def _zip_entries(entries: list[tuple[str, bytes]]) -> bytes:
    """Compression is CPU-bound -- running it on the event loop stalls every SSE
    stream meanwhile. So the caller invokes it via `asyncio.to_thread` (same
    reason as project_export)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries:
            zf.writestr(rel, content)
    return buf.getvalue()


@router.get("/projects/{pid}/prototypes/{slug}/archive")
async def download_prototype_archive(pid: str, slug: str):
    """The dev-team handoff: prototype source as a zip. Binary-safe (bytes
    straight into the zip), so images and fonts survive."""
    _require_registered(pid)
    entries = await _archive_entries(pid, slug)
    if not entries:
        raise HTTPException(status_code=404, detail="prototype bundle not found")

    return Response(
        content=await asyncio.to_thread(_zip_entries, entries),
        media_type="application/zip",
        headers={"Content-Disposition": _archive_filename_header(slug)},
    )


# ---- hosting ----

@router.post("/projects/{pid}/prototypes/{slug}/host")
async def start_host(pid: str, slug: str):
    _require_registered(pid)
    # Hosting serves the build directory IN PLACE now, so starting it under a
    # live build session would race the agent writing into that same tree.
    if _live_session(pid, slug) is not None:
        raise HTTPException(
            status_code=409,
            detail=ec.BUILD_SESSION_ACTIVE)
    # Restore -> brand sync -> npm -> snapshot -> token -> desired state. It must be
    # the same procedure as re-hosting after boot, so it is one function
    # (proto/hosting.py).
    try:
        info, token = await host_prototype(pid, slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype bundle not found")
    except LauncherUnavailable:
        raise HTTPException(status_code=503, detail=ec.SANDBOX_UNAVAILABLE)
    if info.state == "failed" or token is None:
        raise HTTPException(status_code=502, detail=info.log_tail)
    return {"state": info.state, "port": info.port, "log_tail": info.log_tail,
            "access_url": access_url_path(token)}


@router.get("/projects/{pid}/prototypes/{slug}/host")
async def host_status(pid: str, slug: str):
    import aipds.app as app_module
    _require_registered(pid)
    info = app_module.proto_host().status(pid, slug)
    if info is None:
        raise HTTPException(status_code=404, detail="not hosted")
    # GET does not mint a token (`token_for`) -- if a read had the side effect of
    # creating a credential, tokens would be laid down even for prototypes that
    # were never hosted. If none exists yet it is None, and the frontend then
    # does not expose the link.
    token = app_module.proto_host().token_for(pid, slug)
    return {"state": info.state, "port": info.port,
            "access_url": access_url_path(token) if token else None,
            "log_tail": app_module.proto_host().log_tail(pid, slug)}


@router.delete("/projects/{pid}/prototypes/{slug}/host", status_code=204)
async def stop_host(pid: str, slug: str):
    _require_registered(pid)
    # Also clear the desired state -- a prototype the user stopped must not come
    # back up after a restart.
    await stop_prototype(pid, slug)
    return Response(status_code=204)
