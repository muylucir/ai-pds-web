# backend/tests/test_survey_store_purge.py — SurveyStore.purge()
from __future__ import annotations

import json

import pytest

from fakes.in_memory_s3 import FakeS3Store
from aipds.survey.store import (SurveyStore,
                                     questionnaire_key, questionnaire_md_key,
                                     aggregate_md_key, survey_summary,
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


async def test_purge_removes_this_prototypes_aggregate():
    """The rollup is the web's file, rebuilt from the responses — when the responses go away, it must go
    away too. If it remained, it would be read as the rollup of a prototype recreated under the same slug."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[aggregate_md_key(SLUG)] = "# aggregate"

    await store.purge()

    assert aggregate_md_key(SLUG) not in project_s3.blobs


async def test_purge_keeps_the_rules_results_doc():
    """The rules' `validation-results.md` was written by the agent, and after Step 8, Part 2 cites it as the
    basis for build decisions already made. If a reset deleted it, only the conclusion would remain and
    the basis would vanish. The results for a rebuilt prototype are updated when Step 6 runs again —
    that is the rules' Iterate path."""
    from aipds.proto import layout
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    results = f"{layout.artifact_dir(SLUG)}/validation-results.md"
    project_s3.blobs[results] = "# findings"

    await store.purge()

    assert project_s3.blobs[results] == "# findings"


async def test_purge_leaves_another_prototypes_aggregate_alone():
    """In Path B, the rollups of sibling prototypes must remain."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    sibling = aggregate_md_key("flight-disruption-notice")
    project_s3.blobs[sibling] = "# sibling aggregate"

    await store.purge()

    assert project_s3.blobs[sibling] == "# sibling aggregate"


async def test_purge_keeps_the_spec_that_shares_the_directory():
    """For a singular prototype, the rollup and **the spec are in the same directory**
    (`aiplc-docs/discovery/prototype/`). Deleting by prefix would delete the spec,
    and then the card would vanish from the list — it would be a deletion, not a reset.
    """
    from aipds.proto import layout
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=layout.SINGLE_ID,
                        project_id=PID)
    project_s3.blobs[layout.SINGLE_SPEC_KEY] = "# spec"
    project_s3.blobs[aggregate_md_key(layout.SINGLE_ID)] = "# aggregate"

    await store.purge()

    assert project_s3.blobs[layout.SINGLE_SPEC_KEY] == "# spec"
    assert aggregate_md_key(layout.SINGLE_ID) not in project_s3.blobs


async def test_purge_is_idempotent_on_a_prototype_with_no_survey():
    """Most prototypes never get a survey. Purge must be a no-op, not a raise."""
    store, project_s3, root_s3 = _store()

    await store.purge()
    await store.purge()  # twice: the second call has even less to find


# ---- survey_summary().responses: what the reset dialog warns about ----

async def test_summary_responses_includes_archived_rounds():
    """The regression this guards: the reset dialog said "0 responses" over a
    dozen real submissions.

    `archive_current()` MOVES a closed round's answers to
    archive/{closed_at}/responses/ rather than deleting them, and `purge()`
    deletes the whole survey/ tree -- archive included. Counting only the live
    `responses/` prefix therefore reported 0 for a prototype whose survey had
    been regenerated after a first round (the documented normal flow), and the
    dialog's count/irreversibility warning both hinge on that number being
    non-zero: 0 takes neither the `> 0` branch nor the `=== null` branch, so
    the user was shown a bare "검증 설문" bullet and then lost 12 answers.
    """
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    _seed_survey(project_s3, root_s3, "tok-current")
    for i in range(12):
        project_s3.blobs[
            f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/responses/a{i}.json"
        ] = "{}"
    # The current round has none yet -- exactly the shape that reported 0.

    assert (await survey_summary(project_s3, SLUG)).responses == 12


async def test_summary_responses_sums_live_and_archived():
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r2.json"] = "{}"
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/responses/a1.json"] = "{}"

    assert (await survey_summary(project_s3, SLUG)).responses == 3


async def test_summary_responses_counts_only_responses():
    """The questionnaire, its archived copy and the rollup are all inside the
    tree purge deletes, but none of them is a respondent's answer -- counting
    them would inflate the warning and make "응답 3건" mean nothing."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}rollup.json"] = "{}"
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/rollup.json"] = "{}"

    assert (await survey_summary(project_s3, SLUG)).responses == 0


async def test_summary_responses_is_zero_without_a_survey():
    store, project_s3, root_s3 = _store()
    assert (await survey_summary(project_s3, SLUG)).responses == 0


async def test_purge_destroys_exactly_what_the_summary_reported():
    """The pairing is the point: the number shown to the user has to be the
    number that disappears. Asserted together so a change to either side that
    breaks the correspondence fails here rather than in the dialog."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/responses/a1.json"] = "{}"

    reported = (await survey_summary(project_s3, SLUG)).responses
    before = [k for k in project_s3.blobs if "/responses/" in k]
    await store.purge()

    assert reported == len(before) == 2
    assert [k for k in project_s3.blobs if "/responses/" in k] == []


# ---- unreclaimable tokens must raise, not strand the index ----

@pytest.mark.parametrize("body,label", [
    ("{not json at all", "unparseable"),
    ("null", "valid JSON but not an object"),
    ("[]", "a list"),
])
async def test_purge_raises_rather_than_stranding_a_token_it_cannot_read(
        body, label):
    """The regression this guards: a 204 over a token nothing can ever reclaim.

    Probed both branches before the fix. Unparseable JSON warned and CONTINUED:
    purge then deleted the questionnaire that named the token while
    surveys/by-token/{token}.json survived — still resolving to this slug, and
    with no reverse lookup from the index, unreachable forever. Every retry
    returned 204 over it. Once the slug is rebuilt that stale token is a live
    credential into the NEW survey, which is exactly the outcome the route's
    ordering gate exists to prevent, reached without violating any ordering.
    (`null` happened to escape as AttributeError and so was collected correctly
    by accident; both shapes now raise for the same stated reason.)

    A truncated `put` on questionnaire.json is the realistic producer, and
    `get_rollup` already treats unparseable JSON as an expected state, so this
    store does not assume well-formed blobs elsewhere either.
    """
    store, project_s3, root_s3 = _store()
    project_s3.blobs[questionnaire_key(SLUG)] = body
    root_s3.blobs["surveys/by-token/tok-1.json"] = json.dumps(
        {"project_id": PID, "slug": SLUG})
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"

    with pytest.raises(RuntimeError):
        await store.purge()

    # Nothing was deleted, so the token is still reclaimable by a fixed retry
    # (or by hand) — the questionnaire that names it is the only way back.
    assert questionnaire_key(SLUG) in project_s3.blobs
    assert "surveys/by-token/tok-1.json" in root_s3.blobs


async def test_purge_raises_on_an_unreadable_archived_questionnaire_too():
    """The archive holds N-1 of the N tokens a regenerated prototype has issued,
    so an unreadable one there strands a token just as permanently."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/questionnaire.json"
    ] = "{truncated"
    root_s3.blobs["surveys/by-token/tok-old.json"] = json.dumps(
        {"project_id": PID, "slug": SLUG})

    with pytest.raises(RuntimeError):
        await store.purge()

    assert "surveys/by-token/tok-old.json" in root_s3.blobs
    assert "surveys/by-token/tok-current.json" in root_s3.blobs


# ---- survey_summary: the signal that lets a card display "no survey" ----
#
# **Why the response count alone is not enough.** That value is 0 when there is no survey,
# and also 0 when there is a survey but no responses yet. The two states were indistinguishable, so
# the card could not say "this prototype has no survey" — in measured ws-n, only 1 of 3
# prototypes had a survey, but the screen did not show that fact.
#
# **It is obtained without extra S3 calls.** This function lists `survey_prefix(slug)` once
# and reads both facts from that result. The list route makes this query for every card, so adding
# round trips would multiply them by the number of prototypes.

async def test_summary_reports_no_survey_on_an_untouched_prototype():
    project_s3 = FakeS3Store()
    got = await survey_summary(project_s3, SLUG)
    assert got.exists is False and got.responses == 0


async def test_summary_distinguishes_a_survey_with_no_answers_yet():
    """This is the state that could not be expressed before — a survey exists and there are 0 responses."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    _seed_survey(project_s3, root_s3, "tok-current")
    got = await survey_summary(project_s3, SLUG)
    assert got.exists is True and got.responses == 0


async def test_summary_counts_live_and_archived_answers():
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/responses/a1.json"] = "{}"
    got = await survey_summary(project_s3, SLUG)
    assert got.exists is True and got.responses == 2


async def test_summary_says_no_survey_when_only_an_archived_round_remains():
    """A state where only the archive remains is "no survey".

    It takes this shape if generating new questions fails (502) right after `archive_current()` moved the
    closed round. What the PM must do then is recreate the survey, so the card must say "none". The
    response count is still counted — the answers a reset would destroy really exist.
    """
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    project_s3.blobs[
        f"{survey_prefix(SLUG)}archive/2026-01-01T00:00:00Z/responses/a1.json"] = "{}"
    got = await survey_summary(project_s3, SLUG)
    assert got.exists is False
    assert got.responses == 1


async def test_summary_uses_one_listing():
    """One round trip. With N cards it is called N times, so any increase here is multiplied N-fold."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    _seed_survey(project_s3, root_s3, "tok-current")
    calls = []
    original = project_s3.list

    async def counting(prefix):
        calls.append(prefix)
        return await original(prefix)

    project_s3.list = counting
    await survey_summary(project_s3, SLUG)
    assert calls == [survey_prefix(SLUG)]
