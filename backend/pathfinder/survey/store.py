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
                            [r.answers.get(q.id, "") for q in qn.questions])
        return buf.getvalue()
