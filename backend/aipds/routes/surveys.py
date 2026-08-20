# backend/pathfinder/routes/surveys.py — validation survey: admin routes.
#
# The public (token-only) routes live in the same module but are registered
# without the /projects prefix -- see routes/surveys_public.py in Task 7.
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from starlette.responses import Response

from aipds.survey.builder import build_questionnaire
from aipds.survey.inputs import gather_context
from aipds.survey.store import SurveyStore
from aipds.proto import layout as proto_layout

_log = logging.getLogger(__name__)

router = APIRouter()

TOKEN_BYTES = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_registered(pid: str) -> None:
    import aipds.app as app_module
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")


def _store(pid: str, slug: str) -> SurveyStore:
    import aipds.app as app_module
    return app_module.survey_store_factory(pid, slug)


@router.post("/projects/{pid}/prototypes/{slug}/survey", status_code=201)
async def create_survey(pid: str, slug: str):
    import aipds.app as app_module
    _require_registered(pid)
    store = _store(pid, slug)

    try:
        existing = await store.load_questionnaire()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing.status == "open":
            raise HTTPException(status_code=409, detail="survey already open")
        # Closed: archive it so old answers never mix into the new survey's
        # aggregate or CSV (spec §3).
        await store.archive_current()

    s3 = app_module.s3_store_factory(pid)
    spec_key = proto_layout.spec_key(slug)
    try:
        prototype_md = await s3.get(spec_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype spec not found")

    # 스펙 요약이 나온 근거(페인포인트 분석·비즈니스 컨텍스트)를 함께 싣는다.
    # 없으면 없는 대로 간다 — gather_context는 모든 조회 실패를 None으로
    # 강등하므로 이 호출이 아래 502 경로를 타지 않는다(survey/inputs.py).
    context = await gather_context(s3)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        qn = await build_questionnaire(
            prototype_md, app_module.questionnaire_agent_factory(pid),
            token=token, project_id=pid, slug=slug, now=_now(),
            language=app_module.project_language(pid), context=context)
    except Exception:
        # Model/AWS detail can carry credentials -- log it, return a sanitized
        # reason (same policy as routes/prototypes.py).
        _log.exception("questionnaire generation failed: %s/%s", pid, slug)
        raise HTTPException(status_code=502,
                            detail="questionnaire generation failed")

    await store.save_questionnaire(qn)
    return {"token": qn.token, "url": SurveyStore.public_url_path(qn.token),
            "questions": [q.model_dump() for q in qn.questions]}


@router.get("/projects/{pid}/prototypes/{slug}/survey")
async def get_survey(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        qn = await store.load_questionnaire()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    rollup = await store.get_rollup()
    return {"questionnaire": qn.model_dump(),
            "url": SurveyStore.public_url_path(qn.token),
            "rollup": rollup.model_dump()}


@router.post("/projects/{pid}/prototypes/{slug}/survey/close", status_code=204)
async def close_survey(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        await store.close()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    return Response(status_code=204)


@router.post("/projects/{pid}/prototypes/{slug}/survey/synthesize")
async def synthesize_results(pid: str, slug: str):
    """Write the aggregate into the rule's validation-results.md so the PM's
    Discovery flow (and the later product-strategy stage, which reads that
    exact path) picks it up."""
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        key, count = await store.synthesize_results()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    return {"path": key, "response_count": count}


@router.get("/projects/{pid}/prototypes/{slug}/survey/responses.csv")
async def export_csv(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        csv_text = await store.responses_csv()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    filename = f"survey-{slug}.csv"
    return Response(content=csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})
