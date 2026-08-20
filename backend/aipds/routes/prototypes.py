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
# 토큰 게이트의 경로 조립은 그 라우트를 소유한 모듈이 한다 — 여기서 f-string으로
# 다시 쓰면 브라우저 관점 마운트(`/api`)를 두 곳에서 관리하게 되고, 그것이 이
# 파일에서 이미 한 번 어긋났던 종류의 버그다(아래 start_host의 public_base_path
# 주석 참고). proto_public은 aipds.app을 함수 안에서만 import하므로
# 최상위 import가 순환을 만들지 않는다.
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

# 레이아웃 규칙은 proto/layout.py가 단독 소유한다 — 예전에는 이 접두사와
# 정규식이 카드 탐색을, 그리고 같은 경로를 조립하는 f-string이 세 곳에 더
# 흩어져 있었다. Path A.1의 단수 레이아웃을 인식하지 못한 결함이 그 복제 때문에
# 네 곳을 동시에 고쳐야 하는 일이 됐다(그 모듈 헤더 참조).

# The frontend opens the first events stream with this sentinel; the route
# substitutes session.first_prompt() so the build kicks off as a normal
# SSE-relayed turn (spec §4: 첫 턴 자동 발화).
_FIRST_TURN_SENTINEL = "__first__"


class TurnBody(BaseModel):
    text: str


def _handle_scope(pid: str, slug: str) -> str:
    """턴 핸들의 소유자 키. 프로젝트만으로는 부족하다 — 한 프로젝트의 여러
    프로토타입이 각자 세션을 갖고, slug를 빼면 다른 프로토타입의 핸들로 이
    세션의 턴을 열 수 있다."""
    return f"{pid}/{slug}"

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


@router.get("/projects/{pid}/prototypes")
async def list_prototypes(pid: str):
    import aipds.app as app_module
    _require_registered(pid)
    s3 = app_module.s3_store_factory(pid)

    slugs = proto_layout.discover(await s3.list(proto_layout.DISCOVERY_PREFIX))

    host = app_module.proto_host()
    ordered = sorted(slugs.items())
    # **슬러그별 S3 왕복을 미리 병렬로 걷는다.** 예전에는 루프 안에서 카드마다
    # `s3.list(bundle)`과 설문 조회를 순차로 await 해서 카드 N개에
    # 2N번 왕복이었다 — 실측(2026-08-17): 왕복 1회 30ms이므로 카드 10개면 0.6초가
    # 목록 조회에 그대로 붙는다. gather는 입력 순서대로 돌려주므로 아래 zip이
    # 안전하다.
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
        # **`has_survey`가 `response_count > 0`과 다른 질문이다.** 설문이 없을
        # 때도 0이고 설문이 있는데 응답이 아직 없을 때도 0이라, 카드는 두 상태를
        # 구별할 수 없었다 — 실측 test2222에서 프로토타입 3개 중 1개에만 설문이
        # 있었는데 화면에 그 사실이 없어 나머지 둘이 빠진 것을 알아차릴 방법이
        # 없었다. 두 값이 `survey_summary`의 **한 번의 list**에서 함께 나오므로
        # 필드가 늘어도 왕복은 그대로다.
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
        # 공유용 접근 URL. **running일 때만** 실어 보낸다 — 그 밖의 상태에서
        # 이 링크는 게이트가 502를 주므로, 존재하지 않는 것이 프론트가 링크를
        # 노출할지 판단하는 기준이 된다(기존 shareUrl 조건과 같은 규칙).
        #
        # `token_for`이지 `ensure_token`이 아니다: GET이 자격증명을 만드는
        # 부수효과를 가져서는 안 된다. 발급은 호스팅 시작(start_host) 한 곳에서만
        # 일어난다 — 목록 조회가 토큰을 만들면, 한 번도 호스팅되지 않은
        # 프로토타입에도 토큰 파일이 깔린다.
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
    """빌드 채팅 텍스트를 **본문**으로 받아 짧은 핸들을 돌려준다.

    워크스페이스 채팅(routes/turns.py의 create_turn)과 같은 이유다:
    EventSource는 GET만 지원해 본문을 실을 수 없고, 긴 입력이 URL에 실리면
    프록시가 431을 낸다(pathfinder/turn_handles.py 헤더의 실측).

    세션 존재를 여기서 확인해 없으면 404로 끝낸다 — 핸들만 받고 스트림에서
    404가 나면 사용자는 "연결이 끊어졌습니다"만 본다.
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
            # 만료·재사용·다른 세션 — 어느 쪽인지 구별해 알려주지 않는다.
            raise HTTPException(status_code=400,
                                detail="turn handle is unknown or already used")
        text = payload["text"]
    elif text is None:
        # 조용히 빈 턴을 돌리면 사용자는 응답 없는 말풍선을 보고 원인을 알 수 없다.
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
    # process restarted. With PATHFINDER_PROTO_MAX_CONCURRENT capping a workshop
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
    # 리빌드 직전에 브랜드 테마를 갱신한다. 호스팅은 rmtree 없이 기존 트리에
    # `npm run build`를 돌리므로(proto/host.py), 여기서 파일만 새로 쓰면 코드는
    # 한 줄도 건드리지 않고 색·서체·라운드만 바뀐다 -- 이미 완료된 프로토타입이
    # 개선 세션 없이 리브랜딩되는 유일한 경로다.
    #
    # ProtoHost 안이 아니라 이 호출부에 두는 이유: 그 클래스는 S3도 브랜드도
    # 모르는 범용 호스팅이다.
    #
    # 빌드 중인 세션을 여기서 따로 막지 않는다 -- 바로 위 `_live_session` 가드가
    # starting/building/waiting_input/ready 전부를 이미 409로 걸러낸다. 이
    # 지점에 도달했다는 것 자체가 "지금 아무도 이 트리에 쓰고 있지 않다"는
    # 뜻이다.
    build_dir = _prototype_dir(pid, slug).parent
    try:
        profile = await app_module.design_profile_store().load()
        sync_design(build_dir, profile, app_module.project_language(pid))
        # sync_design은 "갱신"만 한다 -- 프로필 업로드 **이전에** 빌드된
        # 프로토타입은 prototype/ 아래에 테마 사본이 없어 아무것도 갈지 않고,
        # 재호스팅해도 그대로 무브랜드로 남는다("재호스팅만으로 리브랜딩"이
        # 성립하지 않는 유일한 경우). 화면(admin.designSubtitle)이 이 한계를
        # 이제는 정확히 말하지만, 운영자가 "왜 아무 일도 안 일어났는지"를 이
        # 요청 시점에도 알 수 있어야 한다 -- 개선 세션을 한 번 열어야
        # 반영된다는 뜻이다.
        if profile is not None and not theme_copies(build_dir):
            _log.warning(
                "design profile present but %s/%s has no theme copy under "
                "prototype/ -- re-hosting cannot re-brand it; an improvement "
                "session must run once to import aipds-theme.css first",
                pid, slug)
    except Exception:
        # 브랜드 반영 실패가 호스팅 자체를 막지는 않는다 -- 화면이 열리는 것이
        # 색보다 우선이다. 원인은 로그에 남는다.
        _log.exception("design sync before host failed: %s/%s", pid, slug)
    try:
        info = await app_module.proto_host().start(
            pid, slug, cwd=_prototype_dir(pid, slug),
            base_path=public_base_path(pid, slug),
            # 빌드 에이전트·Discovery와 같은 출처를 쓴다(app.project_model) —
            # 프로토타입 앱의 런타임 LLM 호출도 프로젝트가 고른 모델로 돌아야
            # 한다. 세 곳이 다른 값을 쓰면 사용자가 고른 모델이 어디에
            # 적용되는지 알 수 없다.
            #
            # 리전은 주입하지 않는다: 백엔드도 Bedrock 리전을 명시적으로
            # 넘기지 않고 boto3/SDK의 기본 해석(인스턴스 리전·AWS_REGION)에
            # 맡긴다. 프로토타입은 `{**os.environ, ...}`로 백엔드 env를
            # 물려받으므로 같은 해석을 그대로 따른다 -- 여기서 별도 규약을
            # 만들면 백엔드와 프로토타입이 다른 리전을 볼 수 있다.
            model_id=app_module.project_model(pid))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype bundle not found")
    if info.state == "failed":
        raise HTTPException(status_code=502, detail=info.log_tail)
    # 접근 토큰은 **여기서만** 발급한다. 프리뷰 링크가 의미를 갖는 것은 호스팅이
    # 실제로 시작된 뒤이므로, 그보다 먼저 만들면 아무 데도 쓰이지 않는 자격증명이
    # 디스크에 남는다. `ensure_token`이므로 stop -> start를 반복해도 값이 그대로다
    # — 워크숍 중 호스팅을 껐다 켜는 것 때문에 이미 나눠 준 링크가 죽으면 안 된다.
    # 링크를 폐기하는 의도된 경로는 리셋이고, 그쪽은 purge()가 토큰까지 지운다.
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
    # GET은 토큰을 만들지 않는다(`token_for`) — 조회가 자격증명을 만드는 부수효과를
    # 가지면, 호스팅된 적 없는 프로토타입에도 토큰이 깔린다. 아직 없으면 None이고,
    # 프론트는 그때 링크를 노출하지 않는다.
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
