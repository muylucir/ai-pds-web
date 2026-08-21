# backend/aipds/survey/store.py — S3 persistence for validation surveys.
#
# Layout (spec §3), under the project prefix:
#   prototypes/{slug}/survey/questionnaire.json   definition + token + status
#   prototypes/{slug}/survey/rollup.json          aggregate CACHE
#   prototypes/{slug}/survey/responses/{uuid}.json  one response = one object (SOURCE OF TRUTH)
#   prototypes/{slug}/survey/archive/{closed_at}/   previous survey on regeneration
# and at the bucket root (needed before we know which project a token belongs to):
#   surveys/by-token/{token}.json                 {"project_id":..., "slug":...}
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aipds.survey.models import Questionnaire, Rollup, SurveyResponse
from aipds.survey.report_labels import labels
from aipds.survey.rollup import build_rollup
from aipds.proto import layout

_log = logging.getLogger(__name__)

TOKEN_INDEX_PREFIX = "surveys/by-token/"


def survey_prefix(slug: str) -> str:
    return f"prototypes/{slug}/survey/"


def questionnaire_key(slug: str) -> str:
    return f"{survey_prefix(slug)}questionnaire.json"


def rollup_key(slug: str) -> str:
    return f"{survey_prefix(slug)}rollup.json"


def responses_prefix(slug: str) -> str:
    return f"{survey_prefix(slug)}responses/"


def archive_prefix(slug: str, closed_at: str) -> str:
    return f"{survey_prefix(slug)}archive/{closed_at}/"


async def purgeable_response_count(project_s3, slug: str) -> int:
    """How many submitted answers a `SurveyStore.purge()` would destroy — the
    number the reset confirmation warns about.

    NOT the same question as `SurveyStore.response_count()`, which counts the
    CURRENT round only (`responses/`) because that is what the live rollup, the
    CSV and the MAX_RESPONSES cap are about. `purge()` deletes the whole
    `survey/` tree, and `archive_current()` MOVES each previous round's answers
    to `archive/{closed_at}/responses/` rather than deleting them --
    regenerating a survey after a first round is the documented normal flow. So
    a prototype with 12 archived answers and none in the current round reported
    0, and the dialog then rendered neither the count nor the irreversibility
    warning before destroying all 12.

    Lives in this module, next to the purge whose scope it describes, because "a
    response a reset destroys" is a fact about the key layout owned here -- the
    route assembling it from `responses_prefix` is what let the two definitions
    drift apart. A module function, not a `SurveyStore` method: the LIST route
    needs this per prototype and building a store per slug would also build a
    bucket-root boto3 client per slug, which this question does not need (only
    the project store).
    """
    return (await survey_summary(project_s3, slug)).responses


@dataclass(frozen=True)
class SurveySummary:
    """Everything the list route needs to know about this prototype's survey."""

    #: Whether there is a survey that can take responses right now (= a live
    #: `questionnaire.json`).
    exists: bool
    #: How many answers a reset would destroy -- the live round plus the archived ones.
    responses: int


async def survey_summary(project_s3, slug: str) -> SurveySummary:
    """Whether a survey exists, and how many responses it has. **Both answered in one round
    trip.**

    **Why existence has to be separate (measured 2026-08-20).** The response count alone
    cannot distinguish "there is no survey" from "there is a survey with 0 responses". Both are
    0. So the card could not say "this prototype has no survey", and in test2222, where only 1
    of 3 prototypes had one, that fact was nowhere on screen -- there was no way to notice that
    the other two were missing their surveys.

    **Why it lists only once.** The list route asks this question per card (which is also why
    that route parallelises with `asyncio.gather`), so adding one round trip multiplies by the
    number of prototypes. Both facts are in the same key listing, so there is no reason to
    split them.

    `exists` looks only at the **live** questionnaire. In a state where only archives remain
    (new question generation failed with a 502 right after `archive_current()`), what the PM
    has to do is create the survey again, so "none" is correct. `responses` still counts the
    archives, because the answers a reset would destroy really exist.
    """
    keys = await project_s3.list(survey_prefix(slug))
    # Any `.../responses/{id}.json`, in the live round or any archived one.
    # Matched on the whole `/responses/` segment rather than a startswith so a
    # future sibling prefix (`responses-draft/`) cannot quietly inflate the
    # count; the trailing-slash exclusion skips the empty directory marker some
    # S3 console/CLI flows leave behind, which is not an answer.
    return SurveySummary(
        exists=questionnaire_key(slug) in keys,
        responses=sum(1 for k in keys
                      if "/responses/" in k and not k.endswith("/")))


def questionnaire_md_key(slug: str) -> str:
    # aiplc-docs/ so the existing artifacts viewer can serve it (that route is
    # hard-limited to this subtree). The directory is decided by layout -- it has to sit
    # where the spec does, or a singular prototype's questionnaire ends up alone in another
    # tree.
    return f"{layout.artifact_dir(slug)}/validation-questionnaire.md"


def results_md_key(slug: str) -> str:
    """Where this prototype's validation synthesis lives
    (prototype-validation.md Step 6), and where the later product-strategy
    stage looks for it.

    **Why it is per slug (measured 2026-08-20).** This used to be a module constant with no
    slug (`aiplc-docs/discovery/prototype/validation-results.md`). But the synthesis route is
    per slug (`POST .../prototypes/{slug}/survey/synthesize`) and Path B creates N prototypes
    -- test2222 had 3. Synthesising all three had all three overwrite the same key so only the
    last survived, with no error.

    The grounds for the singular path were "the rules specify that path", but that rule
    (`prototype-validation.md`) is titled "Path A.1 - Single Solution" and its body states
    "ORIGINAL single-prototype flow". `prototype-building.md`, which Path B follows, has no
    validation stage at all (it ends with "Proceed to: Product Strategy"), and
    `use-case-prioritization.md` mentions validation zero times. So there is no upstream
    requiring the singular path for a multi-prototype project.

    **On A.1 the path is unchanged.** There, upstream really does specify that path and
    product-strategy reads it -- `layout.artifact_dir` branches on the singular id, so one
    expression satisfies both (the same shape as `questionnaire_md_key`, for the same
    reason).
    """
    return f"{layout.artifact_dir(slug)}/validation-results.md"


#: Leading characters a spreadsheet treats as the start of a formula.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Neutralize spreadsheet formulas in respondent-authored text.

    Answer text comes from anonymous respondents, and the CSV's whole purpose
    is to be opened in Excel/Sheets by the PM (the rule's Step 6 handoff).
    RFC-4180 quoting — which csv.writer already does — does NOT stop a cell
    starting with `=`/`+`/`-`/`@` from being evaluated as a formula there
    (CWE-1236), so one malicious answer could exfiltrate data or chain
    commands on the PM's machine. Prefixing with an apostrophe makes the
    spreadsheet treat it as literal text; the raw answer is unchanged in S3.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEADERS):
        return "'" + value
    return value


def _results_markdown(qn: Questionnaire, responses: list, rollup: Rollup,
                      now: str, language: str = "ko") -> str:
    """Render the survey aggregate under prototype-validation.md's Step 6
    headings. Sections the rule expects the PM to judge (theme analysis, pain
    point mapping, build decision) are emitted as empty templates rather than
    machine guesses.

    The section names and table headers Step 6 fixes are English in both languages -- the
    rules find the document by those names (see the report_labels.py header).
    """
    L = labels(language)
    lines = [
        "# Validation Results",
        "",
        f"- **{L['prototype']}**: {qn.slug}",
        f"- **{L['survey']}**: {qn.title}",
        f"- **{L['hypothesis']}**: {qn.hypothesis}",
        f"- **{L['response_count']}**: {rollup.count}",
        f"- **{L['survey_status']}**: "
        f"{L['status_closed'] if qn.status == 'closed' else L['status_open']}",
        f"- **{L['collected_at']}**: {now}",
        "",
        L["note"],
        "",
        "## Feedback Sources",
        "",
        "| Source | Type | Users | Feedback Items |",
        "|---|---|---|---|",
        f"| {L['source_name']} | Survey | {rollup.count} | {rollup.count} |",
        "",
        f"## {L['quantitative']}",
        "",
    ]

    for idx, q in enumerate(qn.questions, start=1):
        stat = rollup.per_question.get(q.id)
        if stat is None:
            continue
        lines.append(f"### Q{idx}. {q.text}")
        lines.append("")
        if stat.type == "scale":
            lines.append(f"{L['mean']} **{stat.mean}** {L['of_5']} "
                         f"({L['responses_n'].format(n=stat.n)})")
            lines.append("")
            lines.append(f"| {L['score']} | {L['count']} |")
            lines.append("|---|---|")
            for score in ("5", "4", "3", "2", "1"):
                lines.append(f"| {score} | {stat.distribution.get(score, 0)} |")
        elif stat.type == "choice":
            lines.append(L["responses_n"].format(n=stat.n))
            lines.append("")
            lines.append(f"| {L['option']} | {L['count']} | {L['ratio']} |")
            lines.append("|---|---|---|")
            for opt, n in stat.counts.items():
                pct = f"{round(n / stat.n * 100)}%" if stat.n else "-"
                lines.append(f"| {opt} | {n} | {pct} |")
        else:
            lines.append(L["free_n"].format(n=stat.n))
        lines.append("")

    text_questions = [q for q in qn.questions if q.type == "text"]
    if text_questions:
        lines.append(f"## {L['free_text']}")
        lines.append("")
        for idx, q in enumerate(qn.questions, start=1):
            if q.type != "text":
                continue
            lines.append(f"### Q{idx}. {q.text}")
            lines.append("")
            # Every answer, not the rollup's 20-sample cap: this file is the
            # PM's synthesis input, so truncating it would hide evidence.
            answers = [str(r.answers[q.id]).strip() for r in
                       sorted(responses, key=lambda x: x.submitted_at)
                       if isinstance(r.answers.get(q.id), str)
                       and str(r.answers[q.id]).strip()]
            if not answers:
                lines.append(L["no_response"])
            else:
                lines.extend(f"- {a}" for a in answers)
            lines.append("")

    lines.extend([
        "## Theme Analysis",
        "",
        "| Theme | Frequency | Severity | Representative Quote |",
        "|---|---|---|---|",
        f"| {L['theme_placeholder']} | | | |",
        "",
        "## Pain Point Mapping",
        "",
        "| Original Pain Point | Validated? | Evidence |",
        "|---|---|---|",
        f"| {L['pain_placeholder']} | | |",
        "",
        "## Build Decision",
        "",
        f"- [ ] {L['decision_proceed']}",
        f"- [ ] {L['decision_iterate']}",
        f"- [ ] {L['decision_pivot']}",
        "",
    ])
    return "\n".join(lines)


def _to_markdown(qn: Questionnaire, language: str = "ko") -> str:
    L = labels(language)
    lines = [f"# {qn.title}", "", f"**{L['hypothesis']}**: {qn.hypothesis}", ""]
    for i, q in enumerate(qn.questions, start=1):
        suffix = "" if q.required else L["optional_suffix"]
        lines.append(f"## Question {i}{suffix}")
        lines.append(q.text)
        lines.append("")
        if q.type == "scale":
            lines.append(L["scale_hint"])
        elif q.type == "choice":
            lines.extend(f"- {opt}" for opt in q.options)
        else:
            lines.append(L["free_response"])
        lines.append("")
    return "\n".join(lines)


class SurveyStore:
    """One prototype's survey. `project_s3` is the project-prefixed store
    (projects/{pid}/); `root_s3` is a bucket-root store used only for the
    token index, which must be readable before the project is known."""

    def __init__(self, project_s3, root_s3, slug: str, project_id: str,
                 language: str = "ko"):
        self._s3 = project_s3
        self._root = root_s3
        self.slug = slug
        self.project_id = project_id
        # The language the report is generated in. Why the project language rather than
        # questionnaire.language: a report is an artifact document, and a document's language
        # is decided by the project. On the normal path the two are equal (a survey is
        # generated in the project language too).
        self._language = language

    @staticmethod
    def public_url_path(token: str) -> str:
        return f"/survey/{token}"

    @staticmethod
    async def resolve_token(root_s3, token: str) -> tuple[str, str]:
        raw = await root_s3.get(f"{TOKEN_INDEX_PREFIX}{token}.json")
        data = json.loads(raw)
        return data["project_id"], data["slug"]

    async def save_questionnaire(self, qn: Questionnaire) -> None:
        """Persist a new survey: token index first, THEN the definition.

        Write order is load-bearing, and this is the order that fails safely.
        A questionnaire with no index is the one unrecoverable state: it is
        `status == "open"`, so `create_survey` refuses to replace it with 409
        "survey already open" for good -- while the survey it is protecting
        cannot collect a single answer, because `/survey/{token}` resolves
        through an index entry that was never written. The prototype loses the
        feature permanently, and the only exit is deleting the object by hand.

        Reversed, a failure leaves nothing behind and the user's retry simply
        works. The leftover is an index entry pointing at a questionnaire that
        does not exist, which `surveys_public._resolve` already treats as an
        ordinary 404 (the token is unguessable and grants nothing), and which
        `purge()` reclaims -- it collects tokens from the questionnaires, and
        the token here is one the caller generated fresh for a save that never
        completed, so no live survey ever depended on it.

        Observed as a real production failure: the deploy role's S3 policy
        covered `projects/*` and `sessions/*` but not the root-level
        `surveys/by-token/`, so this PUT was AccessDenied on every attempt --
        and the questionnaire written before it turned a fixable permission
        error into a prototype that could not have a survey at all.
        """
        await self._root.put(
            f"{TOKEN_INDEX_PREFIX}{qn.token}.json",
            json.dumps({"project_id": qn.project_id, "slug": qn.slug}))
        await self._s3.put(questionnaire_key(self.slug), qn.model_dump_json())
        await self._s3.put(questionnaire_md_key(self.slug),
                           _to_markdown(qn, self._language))

    async def load_questionnaire(self) -> Questionnaire:
        raw = await self._s3.get(questionnaire_key(self.slug))
        return Questionnaire.model_validate_json(raw)

    async def close(self, now: str | None = None) -> Questionnaire:
        qn = await self.load_questionnaire()
        if qn.status == "closed":
            return qn  # idempotent: never bump closed_at
        stamp = now or datetime.now(timezone.utc).isoformat()
        closed = qn.model_copy(update={"status": "closed", "closed_at": stamp})
        await self._s3.put(questionnaire_key(self.slug), closed.model_dump_json())
        return closed

    # ---- responses (source of truth) ----

    async def append_response(self, resp: SurveyResponse) -> None:
        key = f"{responses_prefix(self.slug)}{resp.response_id}.json"
        await self._s3.put(key, resp.model_dump_json())

    async def response_count(self) -> int:
        return len(await self._s3.list(responses_prefix(self.slug)))

    async def load_responses(self) -> list[SurveyResponse]:
        keys = await self._s3.list(responses_prefix(self.slug))
        # Parallel, never sequential: measured 61ms per object round-trip, so a
        # sequential rebuild of 500 responses would take ~30s (spec §2).
        raw = await asyncio.gather(*[self._s3.get(k) for k in keys])
        return [SurveyResponse.model_validate_json(r) for r in raw]

    # ---- rollup (cache) ----

    @staticmethod
    def _now(now: str | None) -> str:
        return now or datetime.now(timezone.utc).isoformat()

    async def refresh_rollup(self, now: str | None = None) -> Rollup:
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        ru = build_rollup(qn.questions, responses, self._now(now))
        await self._s3.put(rollup_key(self.slug), ru.model_dump_json())
        return ru

    async def get_rollup(self, now: str | None = None) -> Rollup:
        count = await self.response_count()
        try:
            cached = Rollup.model_validate_json(
                await self._s3.get(rollup_key(self.slug)))
        except (FileNotFoundError, ValueError):
            cached = None
        if cached is not None and cached.count == count:
            return cached
        # Absent, unparseable, or stale (a rollup write can fail after the
        # response PUT succeeded -- the response is still committed). Rebuild
        # from the source of truth rather than report wrong numbers.
        return await self.refresh_rollup(now)

    # ---- archive on regeneration ----

    async def archive_current(self) -> None:
        """Move the closed survey's definition, responses and rollup under
        archive/{closed_at}/. Reusing responses/ across surveys would mix
        answers to OLD questions into the new survey's aggregate and CSV --
        silently wrong numbers."""
        qn = await self.load_questionnaire()
        if qn.status != "closed" or not qn.closed_at:
            raise ValueError("only a closed survey can be archived")
        dest = archive_prefix(self.slug, qn.closed_at)

        await self._s3.put(f"{dest}questionnaire.json", qn.model_dump_json())
        for key in await self._s3.list(responses_prefix(self.slug)):
            body = await self._s3.get(key)
            name = key.rsplit("/", 1)[-1]
            await self._s3.put(f"{dest}responses/{name}", body)
        try:
            await self._s3.put(f"{dest}rollup.json",
                               await self._s3.get(rollup_key(self.slug)))
        except FileNotFoundError:
            pass  # never aggregated; nothing to preserve
        await self._s3.delete_prefix(responses_prefix(self.slug))
        await self._s3.delete_prefix(rollup_key(self.slug))

    async def purge(self) -> None:
        """Delete this prototype's entire survey: the per-slug tree, the
        questionnaire markdown copy, and EVERY token index that ever pointed
        here.

        Token order is load-bearing. `surveys/by-token/` is root-scoped (a
        one-way token -> prototype index with no reverse lookup), so the only
        way to learn this prototype's tokens is to read them back out of the
        questionnaires. Deleting the tree first would strand those indexes
        permanently -- a live /survey/{token} link resolving to a survey that
        no longer exists.

        `archive_current` does not remove the index when it files a survey
        away, so a prototype whose survey was regenerated N times has N of
        them; collect from the archive as well as the live questionnaire.

        Idempotent: a prototype that never had a survey is the common case, and
        a partially-purged one converges on a retry.

        NOT non-raising, and deliberately so. `RuntimeError` if a questionnaire
        cannot be read for its token (unparseable, or valid JSON that is not an
        object -- a truncated `put` produces both). Raising is what makes that
        case a 502 the user retries and an operator can see; the alternative
        (skip it and keep going) deletes the questionnaire that NAMED the token
        while `surveys/by-token/{token}.json` survives, still resolving here --
        and every subsequent retry then reports 204 over an index no code can
        ever reach again. Once the slug is rebuilt, that stale token is a live
        credential into the NEW survey. The route's ordering gate stops on this
        raise, so the questionnaire stays put and the token stays reclaimable
        by hand.
        """
        for token in await self._collect_tokens():
            await self._root.delete_prefix(f"{TOKEN_INDEX_PREFIX}{token}.json")
        await self._s3.delete_prefix(survey_prefix(self.slug))
        # Outside the survey/ tree: the two copies under aiplc-docs/. Both are
        # this prototype's own — `layout.artifact_dir(slug)` scopes them — so
        # a sibling prototype's documents are out of reach.
        #
        # **Only the exact keys are deleted.** For a singular prototype the spec
        # (`prototype-spec.md`) lives in this same directory. Deleting the directory as a
        # prefix would take the spec with it, and then this is a deletion rather than a reset
        # -- the card disappears from the list. Passing a full key to `delete_prefix` is the
        # established convention for a single-key delete (S3StoreLike has no single
        # delete).
        await self._s3.delete_prefix(questionnaire_md_key(self.slug))
        await self._s3.delete_prefix(results_md_key(self.slug))

    async def _collect_tokens(self) -> set[str]:
        """Every token this prototype has issued, live and archived.

        Raises `RuntimeError` on a questionnaire it cannot read a token out of,
        rather than skipping it. A skip looks harmless -- one warning, purge
        continues -- but it is the one failure mode that CANNOT be retried:
        purge goes on to delete the questionnaire, and `surveys/by-token/` is a
        one-way root-scoped index with no reverse lookup, so the token becomes
        unreachable forever while still resolving to this slug. `get_rollup`
        already treats unparseable JSON as an expected state, so a truncated
        blob is not a can't-happen here.

        A missing key is different and stays a skip: no survey at all is the
        common case, and an archive entry without a definition has no token to
        reclaim.
        """
        keys = [questionnaire_key(self.slug)]
        keys += [k for k in await self._s3.list(f"{survey_prefix(self.slug)}archive/")
                 if k.endswith("/questionnaire.json")]
        tokens: set[str] = set()
        for key in keys:
            try:
                raw = await self._s3.get(key)
            except FileNotFoundError:
                continue  # no survey, or an archive entry without a definition
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"unparseable questionnaire, token not reclaimable: {key}"
                ) from exc
            # A valid non-object (`null`, a list, a bare number) used to reach
            # `.get` and escape as AttributeError -- accidentally the right
            # OUTCOME (the route collected it) via the wrong mechanism. Made
            # explicit so purge()'s contract is one thing on both shapes.
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"questionnaire is not an object, token not reclaimable: {key}")
            token = data.get("token")
            if token:
                tokens.add(token)
        return tokens

    # ---- export ----

    async def responses_csv(self) -> str:
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["response_id", "submitted_at"] +
                        [q.text for q in qn.questions])
        for r in sorted(responses, key=lambda x: x.submitted_at):
            writer.writerow([r.response_id, r.submitted_at] +
                            [_csv_safe(r.answers.get(q.id, ""))
                             for q in qn.questions])
        return buf.getvalue()

    # ---- synthesis into the rule's validation-results.md ----

    async def synthesize_results(self, now: str | None = None) -> tuple[str, int]:
        """Render the aggregate as the rule's validation-results.md and store
        it. Returns (key, response_count).

        Deliberately mechanical: it lays out counts, means and every free-text
        answer under the rule's headings so the PM has the evidence in one
        place. It does NOT invent theme analysis or pain-point verdicts — those
        are the PM's judgment calls in prototype-validation.md Step 6, and a
        machine-written guess there would be indistinguishable from a real
        finding. Placeholder rows are left for the PM to fill.
        """
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        rollup = build_rollup(qn.questions, responses, self._now(now))
        md = _results_markdown(qn, responses, rollup, self._now(now),
                               self._language)
        key = results_md_key(self.slug)
        await self._s3.put(key, md)
        return key, rollup.count
