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
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from aipds.survey.models import Questionnaire, Rollup, SurveyResponse
from aipds.survey.report_labels import labels
from aipds.survey.rollup import build_rollup, persona_groups
from aipds.proto import layout

_log = logging.getLogger(__name__)

#: Bucket-root prefix of the token -> survey index. An S3 key prefix, not a
#: credential -- the `nosec` is for the name, which is all bandit B105 looks at.
TOKEN_INDEX_PREFIX = "surveys/by-token/"  # nosec B105

#: Survey token entropy (bytes). Passed to `secrets.token_urlsafe`.
TOKEN_BYTES = 32


def new_token() -> str:
    """A new survey token.

    Tokens are created in two places -- survey creation (routes/surveys.py) and
    project import (project_import.py; reusing the source's token would hijack the
    source project's link in the same bucket). This module owns the token index, so
    it also defines the token's shape: with two different lengths, one side would
    silently be weaker.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


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


@dataclass(frozen=True)
class SurveySummary:
    """Everything the list route needs to know about this prototype's survey."""

    #: Whether there is a survey that can accept responses now (= a live `questionnaire.json`).
    exists: bool
    #: Number of answers a reset would destroy -- the live round plus archived rounds.
    responses: int


async def survey_summary(project_s3, slug: str) -> SurveySummary:
    """Survey existence and response count. **Answers both in a single round trip.**

    **Why existence is needed separately (observed 2026-08-20).** The response count
    alone cannot distinguish "there is no survey" from "there is a survey with 0
    responses". Both are 0. So the card could not say "this prototype has no survey",
    and in ws-n only 1 of 3 prototypes had a survey yet the screen did not show
    it -- there was no way to notice the other two were missing their surveys.

    **Why list only once.** The list route asks this question for every card (which
    is also why that route parallelizes with `asyncio.gather`), so adding one round
    trip adds as many as there are prototypes. Both facts are in the same key list,
    so there is no reason to split them.

    `exists` looks only at the **live** questionnaire. In a state where only the
    archive remains (new question generation failed with 502 right after
    `archive_current()`), what the PM needs to do is recreate the survey, so "none" is
    correct. `responses` still counts the archive, because the answers a reset would
    destroy are real.
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
    # hard-limited to this subtree). The directory is decided by layout -- it has to
    # sit next to the spec so a single prototype's survey does not end up alone in a
    # separate tree.
    return f"{layout.artifact_dir(slug)}/validation-questionnaire.md"


def aggregate_md_key(slug: str) -> str:
    """Where "Synthesize results" writes this prototype's survey aggregate —
    the feedback file Step 6 of prototype-validation.md imports.

    **This is not the rule's `validation-results.md`.** The agent writes that file
    itself in Step 6 by reading this aggregate, and Part 2 cites it as evidence. The
    web app neither writes nor deletes that file -- see `layout.SURVEY_AGGREGATE` for
    why there must be two files.

    **Why per slug (observed 2026-08-20).** The synthesize route is per slug
    (`POST .../prototypes/{slug}/survey/synthesize`) and Path B builds N prototypes --
    ws-n had 3. When the key had no slug, synthesizing all three made them
    overwrite the same key so only the last one survived, with no error.
    `layout.artifact_dir` branches on the single id, so Path A.1 lands next to the
    spec (`prototype/`).
    """
    return layout.survey_aggregate_key(slug)


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


def _aggregate_markdown(qn: Questionnaire, responses: list, rollup: Rollup,
                        now: str, language: str = "ko") -> str:
    """Render the survey aggregate: counts, means and every free-text answer.

    Deliberately **only** the collected data. Theme analysis, pain-point
    verdicts and the build decision are Step 6 judgments, and they belong in the
    rule's `validation-results.md`, which the agent writes from this file. A
    machine-written guess here would be indistinguishable from a real finding,
    and an empty template of those sections invites the agent to fill it in
    here -- in a file the next synthesis overwrites.
    """
    L = labels(language)
    # If there is a persona question, split the other questions by its answer. With
    # only the overall average, "managers gave 5, the staff who actually use it gave
    # 2" collapses into a single 3.5.
    split = persona_groups(qn.questions, responses)
    persona_q = split[0] if split else None
    groups = [(name, build_rollup(qn.questions, rs, now).per_question)
              for name, rs in split[1]] if split else []
    persona_of = {r.response_id: r.answers.get(persona_q.id)
                  for r in responses} if persona_q else {}
    lines = [
        f"# {L['title']}",
        "",
        f"- **{L['prototype']}**: {qn.slug}",
        f"- **{L['survey']}**: {qn.title}",
        f"- **{L['hypothesis']}**: {qn.hypothesis}",
        f"- **{L['source']}**: {L['source_name']}",
        f"- **{L['response_count']}**: {rollup.count}",
        f"- **{L['survey_status']}**: "
        f"{L['status_closed'] if qn.status == 'closed' else L['status_open']}",
        # Both ends of the validation period. Used for Step 6's Duration and, compared
        # against the change history, to tell which build respondents saw
        # (discovery-config/CLAUDE.md "Mark what validation covered"). Without it the
        # agent asks the PM for the start time, and the PM usually does not remember
        # (2026-10-06 ws-c).
        f"- **{L['started_at']}**: {qn.created_at}",
        *([f"- **{L['closed_at']}**: {qn.closed_at}"] if qn.closed_at else []),
        f"- **{L['collected_at']}**: {now}",
    ]
    if persona_q:
        # Personas with 0 responses are listed too -- this is the only place that
        # shows which target users were not reached.
        counts = rollup.per_question[persona_q.id].counts
        lines.append(f"- **{L['respondents']}**: "
                     + " · ".join(f"{opt} {n}" for opt, n in counts.items()))
    lines += ["", L["note"], ""]
    if persona_q:
        lines += [L["persona_note"], ""]
    lines += [f"## {L['quantitative']}", ""]

    for idx, q in enumerate(qn.questions, start=1):
        stat = rollup.per_question.get(q.id)
        if stat is None:
            continue
        lines.append(f"### Q{idx}. {q.text}")
        lines.append("")
        # The persona question itself is not split -- doing so yields a table with
        # only the diagonal filled.
        by_persona = [(name, per[q.id]) for name, per in groups] \
            if groups and q.id != persona_q.id else []
        if stat.type == "scale":
            lines.append(f"{L['mean']} **{stat.mean}** {L['of_5']} "
                         f"({L['responses_n'].format(n=stat.n)})")
            lines.append("")
            if by_persona:
                lines.append(f"| {L['persona']} | {L['count']} | {L['mean']} "
                             "| 5 | 4 | 3 | 2 | 1 |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for name, st in [(L["overall"], stat)] + by_persona:
                    dist = " | ".join(str(st.distribution.get(sc, 0))
                                      for sc in ("5", "4", "3", "2", "1"))
                    mean = st.mean if st.n else "-"
                    lines.append(f"| {name} | {st.n} | {mean} | {dist} |")
            else:
                lines.append(f"| {L['score']} | {L['count']} |")
                lines.append("|---|---|")
                for score in ("5", "4", "3", "2", "1"):
                    lines.append(f"| {score} | {stat.distribution.get(score, 0)} |")
        elif stat.type == "choice":
            lines.append(L["responses_n"].format(n=stat.n))
            lines.append("")
            extra = "".join(f" {name} ({L['responses_n'].format(n=st.n)}) |"
                            for name, st in by_persona)
            lines.append(f"| {L['option']} | {L['count']} | {L['ratio']} |{extra}")
            lines.append("|---|---|---|" + "---|" * len(by_persona))
            for opt, n in stat.counts.items():
                pct = f"{round(n / stat.n * 100)}%" if stat.n else "-"
                cells = "".join(f" {st.counts.get(opt, 0)} |" for _, st in by_persona)
                lines.append(f"| {opt} | {n} | {pct} |{cells}")
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
            # Every answer, not the rollup's 20-sample cap: this file is Step
            # 6's input, so truncating it would hide evidence.
            # If there is a persona, attach it to each answer. Whose voice it is is
            # the first thing needed when using these answers as evidence.
            answers = [(f"[{persona_of[r.response_id]}] " if persona_q else "")
                       + str(r.answers[q.id]).strip() for r in
                       sorted(responses, key=lambda x: x.submitted_at)
                       if isinstance(r.answers.get(q.id), str)
                       and str(r.answers[q.id]).strip()]
            if not answers:
                lines.append(L["no_response"])
            else:
                lines.extend(f"- {a}" for a in answers)
            lines.append("")

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
        # Report generation language. Why the project language rather than
        # questionnaire.language: the report is an artifact document, and the project
        # decides the document language. On the normal path the two values are the
        # same (the survey is also generated in the project language).
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
        response_keys = await self._s3.list(responses_prefix(self.slug))
        for key in response_keys:
            body = await self._s3.get(key)
            name = key.rsplit("/", 1)[-1]
            await self._s3.put(f"{dest}responses/{name}", body)
        if response_keys:
            # Submissions do not update the aggregate (routes/surveys_public.py), so the
            # cache may be missing or stale -- the preserved aggregate is made to match
            # the preserved responses.
            rollup = await self.get_rollup()
            await self._s3.put(f"{dest}rollup.json", rollup.model_dump_json())
        else:
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
        # Outside the survey/ tree: the two files we write under aiplc-docs/.
        # Both are this prototype's own — `layout.artifact_dir(slug)` scopes
        # them — so a sibling prototype's documents are out of reach. The rule's
        # `validation-results.md` beside them is NOT ours and stays: the agent
        # wrote it, and once Step 8 has run, Part 2 cites it as the evidence of
        # a build decision already made. A rebuilt prototype gets new results
        # when Step 6 runs again, exactly as the rule's Iterate path does.
        #
        # **Delete only the exact keys.** For a single prototype, the spec
        # (`prototype-spec.md`) lives in this same directory. Deleting the directory as a
        # prefix would remove the spec, turning a reset into a deletion -- the card
        # disappears from the list. Passing a full key to `delete_prefix` is the
        # established convention for single-key deletion (S3StoreLike has no single
        # delete).
        await self._s3.delete_prefix(questionnaire_md_key(self.slug))
        await self._s3.delete_prefix(aggregate_md_key(self.slug))

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

    # ---- the survey aggregate Step 6 imports ----

    async def synthesize_aggregate(self, now: str | None = None) -> tuple[str, int]:
        """Render the aggregate and store it. Returns (key, response_count).

        Re-runnable on an open survey -- reading an interim aggregate and then
        collecting more answers is the common flow -- and each run overwrites
        the previous aggregate with fresh numbers. That is safe only because the
        file is ours alone: the agent's Step 6 synthesis lives in the rule's
        `validation-results.md`, which this never touches.
        """
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        rollup = build_rollup(qn.questions, responses, self._now(now))
        md = _aggregate_markdown(qn, responses, rollup, self._now(now),
                                 self._language)
        key = aggregate_md_key(self.slug)
        await self._s3.put(key, md)
        return key, rollup.count
