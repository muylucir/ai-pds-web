# backend/pathfinder/survey/store.py — S3 persistence for validation surveys.
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
from datetime import datetime, timezone

from pathfinder.survey.models import Questionnaire, Rollup, SurveyResponse
from pathfinder.survey.rollup import build_rollup

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


def questionnaire_md_key(slug: str) -> str:
    # aiplc-docs/ so the existing artifacts viewer can serve it (that route is
    # hard-limited to this subtree).
    return f"aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md"


#: Where the rule expects validation synthesis to live
#: (prototype-validation.md Step 6), and where the later product-strategy
#: stage looks for it. Singular "prototype/", NOT the per-slug
#: "prototypes/{slug}/" tree the questionnaire copy uses.
RESULTS_MD_KEY = "aiplc-docs/discovery/prototype/validation-results.md"


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
                      now: str) -> str:
    """Render the survey aggregate under prototype-validation.md's Step 6
    headings. Sections the rule expects the PM to judge (theme analysis, pain
    point mapping, build decision) are emitted as empty templates rather than
    machine guesses."""
    lines = [
        "# Validation Results",
        "",
        f"- **프로토타입**: {qn.slug}",
        f"- **설문**: {qn.title}",
        f"- **검증 가설**: {qn.hypothesis}",
        f"- **응답 수**: {rollup.count}",
        f"- **설문 상태**: {'마감' if qn.status == 'closed' else '진행 중'}",
        f"- **취합 시각**: {now}",
        "",
        "> 이 파일은 Pathfinder 설문 집계로 생성되었다. 아래 '정량 집계'와",
        "> '자유 응답 전문'은 수집된 데이터이며, 테마 분석·pain point 매핑·",
        "> 빌드 결정은 PM이 판단해 채운다(prototype-validation.md Step 6).",
        "",
        "## Feedback Sources",
        "",
        "| Source | Type | Users | Feedback Items |",
        "|---|---|---|---|",
        f"| Pathfinder 검증 설문 | Survey | {rollup.count} | {rollup.count} |",
        "",
        "## 정량 집계",
        "",
    ]

    for idx, q in enumerate(qn.questions, start=1):
        stat = rollup.per_question.get(q.id)
        if stat is None:
            continue
        lines.append(f"### Q{idx}. {q.text}")
        lines.append("")
        if stat.type == "scale":
            lines.append(f"평균 **{stat.mean}** / 5 (응답 {stat.n}건)")
            lines.append("")
            lines.append("| 점수 | 응답 수 |")
            lines.append("|---|---|")
            for score in ("5", "4", "3", "2", "1"):
                lines.append(f"| {score} | {stat.distribution.get(score, 0)} |")
        elif stat.type == "choice":
            lines.append(f"응답 {stat.n}건")
            lines.append("")
            lines.append("| 선택지 | 응답 수 | 비율 |")
            lines.append("|---|---|---|")
            for opt, n in stat.counts.items():
                pct = f"{round(n / stat.n * 100)}%" if stat.n else "-"
                lines.append(f"| {opt} | {n} | {pct} |")
        else:
            lines.append(f"자유 응답 {stat.n}건 — 전문은 아래 '자유 응답 전문' 참조")
        lines.append("")

    text_questions = [q for q in qn.questions if q.type == "text"]
    if text_questions:
        lines.append("## 자유 응답 전문")
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
                lines.append("(응답 없음)")
            else:
                lines.extend(f"- {a}" for a in answers)
            lines.append("")

    lines.extend([
        "## Theme Analysis",
        "",
        "| Theme | Frequency | Severity | Representative Quote |",
        "|---|---|---|---|",
        "| (PM이 위 자유 응답에서 도출) | | | |",
        "",
        "## Pain Point Mapping",
        "",
        "| Original Pain Point | Validated? | Evidence |",
        "|---|---|---|",
        "| (Envision의 pain point를 옮겨 판정) | | |",
        "",
        "## Build Decision",
        "",
        "- [ ] Proceed — 검증됨, 다음 단계로",
        "- [ ] Iterate — 부분 검증, 프로토타입 수정 후 재검증",
        "- [ ] Pivot — 접근 재고 (Envision으로 복귀)",
        "",
    ])
    return "\n".join(lines)


def _to_markdown(qn: Questionnaire) -> str:
    lines = [f"# {qn.title}", "", f"**검증 가설**: {qn.hypothesis}", ""]
    for i, q in enumerate(qn.questions, start=1):
        suffix = "" if q.required else " (선택)"
        lines.append(f"## Question {i}{suffix}")
        lines.append(q.text)
        lines.append("")
        if q.type == "scale":
            lines.append("1(전혀 아니다) ~ 5(매우 그렇다) 중 선택")
        elif q.type == "choice":
            lines.extend(f"- {opt}" for opt in q.options)
        else:
            lines.append("(자유 응답)")
        lines.append("")
    return "\n".join(lines)


class SurveyStore:
    """One prototype's survey. `project_s3` is the project-prefixed store
    (projects/{pid}/); `root_s3` is a bucket-root store used only for the
    token index, which must be readable before the project is known."""

    def __init__(self, project_s3, root_s3, slug: str, project_id: str):
        self._s3 = project_s3
        self._root = root_s3
        self.slug = slug
        self.project_id = project_id

    @staticmethod
    def public_url_path(token: str) -> str:
        return f"/survey/{token}"

    @staticmethod
    async def resolve_token(root_s3, token: str) -> tuple[str, str]:
        raw = await root_s3.get(f"{TOKEN_INDEX_PREFIX}{token}.json")
        data = json.loads(raw)
        return data["project_id"], data["slug"]

    async def save_questionnaire(self, qn: Questionnaire) -> None:
        await self._s3.put(questionnaire_key(self.slug), qn.model_dump_json())
        await self._root.put(
            f"{TOKEN_INDEX_PREFIX}{qn.token}.json",
            json.dumps({"project_id": qn.project_id, "slug": qn.slug}))
        await self._s3.put(questionnaire_md_key(self.slug), _to_markdown(qn))

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

        Idempotent and non-raising: a prototype that never had a survey is the
        common case, and a partially-purged one must converge on a retry.
        """
        for token in await self._collect_tokens():
            await self._root.delete_prefix(f"{TOKEN_INDEX_PREFIX}{token}.json")
        await self._s3.delete_prefix(survey_prefix(self.slug))
        # Outside the survey/ tree: the viewer copy under aiplc-docs/.
        # RESULTS_MD_KEY is deliberately NOT touched -- it has no slug in it
        # and is shared across prototypes.
        await self._s3.delete_prefix(questionnaire_md_key(self.slug))

    async def _collect_tokens(self) -> set[str]:
        """Every token this prototype has issued, live and archived."""
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
                token = json.loads(raw).get("token")
            except json.JSONDecodeError:
                _log.warning("unparseable questionnaire, token not reclaimed: %s", key)
                continue
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
        md = _results_markdown(qn, responses, rollup, self._now(now))
        await self._s3.put(RESULTS_MD_KEY, md)
        return RESULTS_MD_KEY, rollup.count
