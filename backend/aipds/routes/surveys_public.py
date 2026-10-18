# backend/aipds/routes/surveys_public.py — PUBLIC survey routes.
#
# This is the only UNAUTHENTICATED WRITE path in the app, so it is deliberately
# narrow (spec §7):
#   - the token is the only credential; nothing here takes a project id
#   - responses never echo internal identifiers (project_id / slug / token)
#   - only keys defined by the questionnaire are stored (no arbitrary payload)
#   - size and count caps bound S3 growth
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import Response

from aipds import error_codes as ec
from aipds.survey.models import (SCALE_MAX, SCALE_MIN, Questionnaire,
                                      SurveyResponse)
from aipds.survey.store import SurveyStore

router = APIRouter()

MAX_ANSWER_CHARS = 2000
MAX_BODY_BYTES = 32 * 1024
MAX_RESPONSES = 1000


class AnswersBody(BaseModel):
    answers: dict[str, object]


async def _resolve(token: str) -> tuple[SurveyStore, Questionnaire]:
    import aipds.app as app_module
    try:
        pid, slug = await SurveyStore.resolve_token(
            app_module.surveys_root_s3_factory(), token)
    except FileNotFoundError:
        # Do not distinguish "no such token" from "no such survey": that would
        # let a prober learn which tokens exist.
        raise HTTPException(status_code=404, detail="survey not found")
    store = app_module.survey_store_factory(pid, slug)
    try:
        qn = await store.load_questionnaire()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="survey not found")
    if qn.status == "closed":
        raise HTTPException(status_code=410, detail=ec.SURVEY_CLOSED)
    return store, qn


@router.get("/survey/{token}")
async def public_get_survey(token: str):
    _, qn = await _resolve(token)
    # Questions only: no token echo, no project_id/slug, no aggregate.
    # language is sent along too -- respondents are outsiders with no aipds_lang
    # cookie, and English questions with only the screen text in Korean is worse for
    # the respondent. project_id/slug/token stay unexposed (the rule in this file's
    # header).
    return {"title": qn.title, "hypothesis": qn.hypothesis,
            "language": qn.language,
            "questions": [q.model_dump() for q in qn.questions]}


def _validate_answers(qn: Questionnaire, answers: dict) -> dict:
    by_id = {q.id: q for q in qn.questions}
    unknown = set(answers) - set(by_id)
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"unknown question(s): {sorted(unknown)}")
    total = 0
    clean: dict = {}
    for qid, value in answers.items():
        q = by_id[qid]
        if q.type == "scale":
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=400,
                                    detail=f"{qid}: scale answer must be an integer")
            if not SCALE_MIN <= value <= SCALE_MAX:
                raise HTTPException(status_code=400,
                                    detail=f"{qid}: scale answer out of range")
            clean[qid] = value
            continue
        if not isinstance(value, str):
            raise HTTPException(status_code=400,
                                detail=f"{qid}: answer must be a string")
        if len(value) > MAX_ANSWER_CHARS:
            raise HTTPException(status_code=413, detail=f"{qid}: answer too long")
        if q.type == "choice" and value not in q.options:
            raise HTTPException(status_code=400,
                                detail=f"{qid}: option not offered")
        total += len(value.encode("utf-8"))
        clean[qid] = value
    if total > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="response too large")
    missing = [q.id for q in qn.questions
               if q.required and q.id not in clean]
    if missing:
        raise HTTPException(status_code=400,
                            detail=f"missing required answer(s): {missing}")
    return clean


def _reject_oversized_body(request: Request) -> None:
    """Cheap pre-parse guard on the one unauthenticated write path.

    The byte cap in _validate_answers is authoritative, but it only runs
    after Starlette has buffered and json-parsed the whole body — so an
    anonymous caller could make us parse megabytes before the 400. Same
    Content-Length short-circuit routes/uploads.py already uses: it is
    client-controlled and therefore not a security boundary, just a way to
    stop honest oversized bodies from being parsed at all.
    """
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES * 2:
        raise HTTPException(status_code=413, detail="response too large")


@router.post("/survey/{token}", status_code=204)
async def public_submit_survey(token: str, body: AnswersBody, request: Request):
    _reject_oversized_body(request)
    store, qn = await _resolve(token)
    if await store.response_count() >= MAX_RESPONSES:
        raise HTTPException(status_code=429, detail=ec.SURVEY_FULL)
    clean = _validate_answers(qn, body.answers)

    resp = SurveyResponse(response_id=uuid.uuid4().hex,
                          submitted_at=datetime.now(timezone.utc).isoformat(),
                          answers=clean)
    await store.append_response(resp)   # this PUT is what commits the response
    # The aggregate is not updated here. Updating re-reads every response, so a single
    # submission would become O(N) (the 1000th submission = 1000 GETs), and this path
    # is open without authentication. The aggregate is only a cache, and `get_rollup`
    # rebuilds it when the response count does not match -- that cost is paid once by
    # whoever opens the dashboard.
    return Response(status_code=204)
