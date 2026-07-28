# backend/tests/test_survey_store_purge.py — SurveyStore.purge()
from __future__ import annotations

import json

import pytest

from fakes.in_memory_s3 import FakeS3Store
from pathfinder.survey.store import (RESULTS_MD_KEY, SurveyStore,
                                     questionnaire_key, questionnaire_md_key,
                                     survey_prefix)

pytestmark = pytest.mark.asyncio

SLUG = "todo-app"
PID = "proj-1"


def _store() -> tuple[SurveyStore, FakeS3Store, FakeS3Store]:
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    return (SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID),
            project_s3, root_s3)


def _seed_survey(project_s3, root_s3, token: str, *, closed_at=None) -> None:
    """One survey: questionnaire + its token index. `closed_at` puts a COPY
    under archive/ the way archive_current() does on regeneration."""
    qn = {"slug": SLUG, "project_id": PID, "token": token,
          "status": "closed" if closed_at else "open",
          "closed_at": closed_at, "questions": []}
    if closed_at:
        project_s3.blobs[
            f"{survey_prefix(SLUG)}archive/{closed_at}/questionnaire.json"
        ] = json.dumps(qn)
    else:
        project_s3.blobs[questionnaire_key(SLUG)] = json.dumps(qn)
    root_s3.blobs[f"surveys/by-token/{token}.json"] = json.dumps(
        {"project_id": PID, "slug": SLUG})


async def test_purge_removes_the_whole_survey_tree():
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"
    project_s3.blobs[f"{survey_prefix(SLUG)}rollup.json"] = "{}"
    project_s3.blobs[questionnaire_md_key(SLUG)] = "# survey"

    await store.purge()

    assert [k for k in project_s3.blobs if k.startswith(survey_prefix(SLUG))] == []
    assert questionnaire_md_key(SLUG) not in project_s3.blobs


async def test_purge_removes_archived_tokens_too():
    """The regression this guards: archive_current() does NOT delete the token
    index, so a prototype whose survey was regenerated N times has N indexes.
    Deleting only the current one leaves live /survey/{token} links pointing at
    a survey that no longer exists — the respondent sees a broken page."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    _seed_survey(project_s3, root_s3, "tok-current")

    await store.purge()

    assert "surveys/by-token/tok-old.json" not in root_s3.blobs
    assert "surveys/by-token/tok-current.json" not in root_s3.blobs


async def test_purge_keeps_the_shared_results_doc():
    """RESULTS_MD_KEY has no slug in it ("prototype/", singular) — it is shared
    across prototypes. Purging one must not destroy another's findings."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[RESULTS_MD_KEY] = "# shared findings"

    await store.purge()

    assert project_s3.blobs[RESULTS_MD_KEY] == "# shared findings"


async def test_purge_is_idempotent_on_a_prototype_with_no_survey():
    """Most prototypes never get a survey. Purge must be a no-op, not a raise."""
    store, project_s3, root_s3 = _store()

    await store.purge()
    await store.purge()  # twice: the second call has even less to find
