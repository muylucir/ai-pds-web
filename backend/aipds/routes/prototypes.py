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
from aipds.pathsafe import reject_unsafe_segment
from aipds.proto.design_sync import sync_design, theme_copies
from aipds.proto.session import has_build_output, purge_session_state
from aipds.turn_job import TurnBusy, subscribe
from aipds.proto.source import newest_source_mtime, source_entries
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
    """명세 본문에서 카드에 쓸 이름을 읽는다. 못 읽으면 None.

    **실패를 삼키는 것이 여기서는 옳다.** 이름은 카드의 장식이고 상태·버튼은
    이 값에 의존하지 않는다 — 이름 하나 때문에 Prototypes 탭 전체가 500이 되면
    더 나쁜 결과를 고른 것이다. 부재는 호출부에서 슬러그로 되돌아간다.
    `discover`는 `list`로 키를 찾고 여기서 `get`으로 읽으므로, 그 사이에
    리셋·삭제가 끼어드는 것만으로도 이 실패는 정상 운영에서 일어난다.

    그래도 **조용히** 삼키지는 않는다: "카드에 이름이 안 나온다"를 로그 한 줄로
    진단할 수 있어야 한다.
    """
    try:
        return spec_name(await s3.get(spec_path))
    except Exception:
        _log.warning("could not read prototype name from %s", spec_path,
                     exc_info=True)
        return None


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
    #
    # 이름을 읽는 `get`이 카드당 세 번째 왕복이지만 **같은 gather 안**이므로
    # 벽시계는 그대로다 — 카드 하나의 세 왕복이 서로를 기다리지 않는다. 목록
    # 응답에 이름을 실어 보내는 대안(카드가 각자 명세를 받아 파싱)은 왕복을
    # 클라이언트로 옮기고 파싱 규칙을 두 벌로 만든다.
    bundle_lists, surveys, names = await asyncio.gather(
        asyncio.gather(*(s3.list(f"prototypes/{slug}/bundle/")
                         for slug, _ in ordered)),
        asyncio.gather(*(survey_summary(s3, slug) for slug, _ in ordered)),
        asyncio.gather(*(_card_name(s3, spec_path) for _, spec_path in ordered)),
    )
    out = []
    for (slug, spec_path), bundle_keys, survey, name in zip(
            ordered, bundle_lists, surveys, names):
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

        # 빌드 세션이 열려 있는가. **`state`와 별개 필드인 이유**가 이 블록의
        # 요점이다: 서버가 떠 있는지와 세션이 열려 있는지는 서로 독립적인
        # 사실인데, 열거형 하나가 둘을 실어 나르면서 세션이 호스팅을 가렸다.
        # 실행 중인 프로토타입을 수정하기 시작하면 카드가 `building`으로 바뀌어
        # 프리뷰·공유 링크가 사라졌고 — 서버는 계속 떠 있었으므로 화면만
        # 거짓말을 했다. 프리뷰를 본 직후가 수정하고 싶어지는 순간인데, 바로 그
        # 순간에 방금 보던 것을 잃는 화면이었다.
        session_open = session is not None and session.status in _WORKING_STATUSES

        # `running`이 `building`보다 먼저다. 세션을 여는 것은 호스팅을 건드리지
        # 않으므로(어느 라우트도 stop을 부르지 않는다) 서버는 실제로 살아 있고,
        # 그 사실을 먼저 말해야 카드가 프리뷰·링크·중지를 계속 그릴 수 있다.
        # 첫 빌드에는 뜬 서버가 없으므로 그때는 여전히 `building`이다.
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

        # 떠 있는 서버가 소스보다 오래됐는가. 실행 중에 수정할 수 있게 되면서 생긴
        # 구간이다 — 서버는 그대로 뜬 채 소스만 바뀐다. 그때 카드가 "실행 :4007"만
        # 말하면 사용자는 자기 수정이 반영됐다고 읽지만, 참가자에게 나간 링크가
        # 보여 주는 것은 이전 버전이고 다시 호스팅해야 바뀐다.
        #
        # **세션이 열려 있는지로 대신하지 않는다.** 그러면 세션이 닫히는 순간 신호가
        # 사라지고, 정작 그때가 사용자가 오해하기 가장 쉬운 시점이다.
        #
        # `running`일 때만 묻는다: 호스팅되지 않은 프로토타입에는 낡을 프리뷰가 없고,
        # 트리를 걷는 비용도 그만큼 아낀다(이 라우트는 폴링된다 —
        # `newest_source_mtime`이 제외 디렉토리를 잘라내는 이유와 같은 자리다).
        preview_stale = False
        if state == "running" and host_info is not None and host_info.built_at:
            newest = newest_source_mtime(app_module._proto_root() / pid / slug)
            preview_stale = newest is not None and newest > host_info.built_at

        # 카드 제목. **`slug`와 별개 필드다** — slug는 여전히 식별자이고(리셋·
        # 빌드·세션이 그 값으로 키된다) 이름은 표시용이다. 하나로 합치면 Path
        # A.1의 예약 id `prototype`을 이름으로 덮어쓰게 되어 식별자가 흔들린다.
        # 없으면 null: 프론트가 슬러그로 되돌아가는 분기가 값의 유무여야 한다.
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
    """사용자가 보낸 말을 에이전트에게 갈 턴 텍스트로 바꾼다.

    세션의 **첫 메시지는 곧 개시 턴**이다. 센티넬이면 사용자가 아무 말도 하지
    않은 자동 개시이므로 기본 개시 프롬프트를 쓰고, 아니면 그 메시지가 사용자의
    요청이므로 개시 프롬프트에 실어 보낸다.

    **왜 사용자 요청을 개시 턴에 싣는가.** "수정하기"를 누른 사람은 이미 무엇을
    고칠지 알고 있는데(프리뷰에서 봤다), 개시 프롬프트가 되묻고 그 질문이 떠 있는
    동안 입력창이 비활성이라(BuildPanel) 자기 요청을 타이핑할 수조차 없었다.

    **생 텍스트를 그대로 보내면 안 된다.** handoff 분기는 새 session_id로 시작해
    트랜스크립트가 없으므로(proto/session의 _resolve_session_id), 개시 프롬프트가
    지고 오는 요약과 "먼저 prototype/을 봐라"가 빠지면 에이전트가 맥락 없이
    시작한다.

    판정 기준이 `session.opened`인 것이 요점이다 — UI 플래그가 아니다. 그것은
    세션이 아는 사실이고, 프론트가 추측하면 새로고침·경합·이미 열린 세션에서
    어긋난다.
    """
    if text == _FIRST_TURN_SENTINEL:
        return session.first_prompt()
    if not session.opened:
        return session.first_prompt(request=text)
    return text


def _start_build_turn(session, text: str):
    """빌드 턴을 서버 작업으로 시작한다(aipds/turn_job.py). 도는 턴이 있으면 409 —
    본문에 그 턴의 id가 있다(Discovery의 routes/turns.start_turn과 같은 모양)."""
    turn_text = _opening_text(session, text)
    shown = None if text == _FIRST_TURN_SENTINEL else text
    try:
        return session.turns.start(
            "message", lambda: session.send_message(turn_text), input_text=shown)
    except TurnBusy as busy:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": busy.job.id})


def _build_turn_response(job, after: int = 0) -> EventSourceResponse:
    """턴 로그를 `after` 뒤부터 흘린다. 프레임의 `id`가 seq다. 보던 화면이 끊겨도
    턴은 계속 돈다 — 다시 붙을 때 마지막으로 받은 seq를 `after`로 준다."""
    async def gen():
        async for seq, event in subscribe(job, after):
            yield {"id": str(seq), "data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


@router.post("/projects/{pid}/prototypes/{slug}/turns")
async def create_session_turn(pid: str, slug: str, body: TurnBody):
    """빌드 턴을 **시작**하고 id를 돌려준다. 스트림은 `GET /events?turn=<id>`로 본다.

    텍스트를 본문으로 받는 이유는 워크스페이스 채팅(routes/turns.py)과 같다:
    EventSource는 GET만 지원하고, 긴 입력이 URL에 실리면 프록시가 431을 낸다
    (aipds/turn_handles.py 헤더의 실측). 자동 개시는 센티넬(`__first__`)을 보낸다.
    """
    _require_registered(pid)
    session = _require_session(pid, slug)
    return {"turn_id": _start_build_turn(session, body.text).id}


@router.get("/projects/{pid}/prototypes/{slug}/events")
async def stream_session_events(pid: str, slug: str,
                                turn: str | None = None, after: int = 0,
                                text: str | None = None):
    """빌드 턴 하나를 본다. `?text=`는 시작과 구독을 한 요청으로 하는 경로다."""
    _require_registered(pid)
    session = _require_session(pid, slug)
    if turn is not None:
        job = session.turns.get(turn)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown turn")
        return _build_turn_response(job, after)
    if text is None:
        # 조용히 빈 턴을 돌리면 사용자는 응답 없는 말풍선을 보고 원인을 알 수 없다.
        raise HTTPException(status_code=400,
                            detail="either `turn` or `text` is required")
    return _build_turn_response(_start_build_turn(session, text))


@router.get("/projects/{pid}/prototypes/{slug}/session")
async def get_session(pid: str, slug: str):
    """열린 빌드 세션과 그 세션의 턴들. 빌드 화면이 다시 열릴 때(새로고침, 패널을
    닫았다 엶) 이것으로 대화를 처음부터 재생하고 도는 턴에 붙는다."""
    import aipds.app as app_module
    _require_registered(pid)
    session = app_module.proto_sessions.get((pid, slug))
    if session is None:
        raise HTTPException(status_code=404, detail="no build session")
    return {"status": session.status,
            "turns": [{"turn_id": j.id, "state": j.state,
                       "last_seq": j.log.last_seq, "input": j.input_text}
                      for j in session.turns.all()]}


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
    """이 프로젝트/슬러그의 소스. 수집 규칙은 proto/source.py가 소유한다."""
    import aipds.app as app_module

    return await source_entries(
        build_dir=app_module._proto_root() / pid / slug,
        s3=app_module.s3_store_factory(pid), slug=slug)


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
