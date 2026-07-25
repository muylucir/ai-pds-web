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

import json
import logging

from pathfinder.survey.models import Questionnaire

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
        from datetime import datetime, timezone
        stamp = now or datetime.now(timezone.utc).isoformat()
        closed = qn.model_copy(update={"status": "closed", "closed_at": stamp})
        await self._s3.put(questionnaire_key(self.slug), closed.model_dump_json())
        return closed
