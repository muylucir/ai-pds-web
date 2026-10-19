# backend/aipds/handoff/ledger.py — decision ledger: a table carrying over, as is, the decisions the PM answered in Discovery.
#
# **Why it exists.** Discovery documents hold the results of decisions, but not whether a decision was chosen
# by the PM or was an AI-proposed default accepted as is. Which decisions development trusts and which it
# re-checks is determined by that difference — in observations, 89% of questions where the AI marked a default were answered with that default.
#
# **It does not call the model.** It only moves question file answers and handoff tab records into a table. A
# rewritten document can diverge from the source (observed: internal contradictions in a rewritten PRD), and
# the same input must produce the same table so a person can check it against the source.
from __future__ import annotations

import re
from collections.abc import Mapping

from aipds.handoff import readiness as readiness_mod
from aipds.handoff import supplement as supplement_mod
from aipds.models import Question
from aipds.parsers.questions import parse_question_file

LEDGER = "decisions.md"

_STAGE_ORDER = ("envision", "solution_analysis", "use_case_intake", "prioritization",
                "prototype", "product_strategy", "go_to_market", "other")

_TEXT = {
    "ko": {
        "title": "# 결정 장부",
        "lead": ("Discovery의 질문 파일에서 PM이 답한 결정과 인계 직전에 PM이 보탠 답을 그대로 "
                 "옮겼습니다. 결정의 내용과 이유는 `discovery-document.md`에 있고, 이 표는 "
                 "**누가 정했는가**를 보여 줍니다."),
        "legend": ("- **PM 선택**: PM이 직접 고르거나 썼습니다.\n"
                   "- **AI 제안 그대로**: AI가 기본값으로 표시한 보기를 PM이 그대로 골랐습니다. "
                   "구현을 좌우하면 PM에게 다시 확인하세요.\n"
                   "- **AI 제안 · PM 확인**: 위와 같지만 인계 직전에 PM이 맞다고 확인했습니다.\n"
                   "- **인계 시 보완(PM)**: Discovery 밖에서, 인계 직전에 PM이 답했습니다."),
        "columns": ("근거", "질문", "답", "누가 정했나"),
        "pm": "PM 선택", "ai": "AI 제안 그대로", "confirmed": "AI 제안 · PM 확인",
        "handoff": "인계 시 보완(PM)", "handoff_title": "## 인계 시 보완(PM)",
        "handoff_columns": ("질문", "답", "답한 시각"),
        "unknown": "모름 (열린 결정으로 넘김)", "checking": "확인 중",
        "stages": {"envision": "Envision", "solution_analysis": "솔루션 분석",
                   "use_case_intake": "유스케이스 접수", "prioritization": "우선순위",
                   "prototype": "프로토타입", "product_strategy": "제품 전략",
                   "go_to_market": "Go-to-Market", "other": "기타"},
        "topics": {
            "problem.evidence": "문제가 실제로 있다는 근거", "problem.why_now": "지금 풀어야 하는 이유",
            "goals.success": "성공의 기준", "non_goals.list": "만들지 않을 것",
            "constraints.rules": "지켜야 할 규정·사내 규칙·연동 시스템",
            "assumptions.must_be_true": "성공하려면 사실이어야 하는 것",
            "assumptions.failure_reasons": "실패할 가능성이 가장 큰 이유",
        },
    },
    "en": {
        "title": "# Decision ledger",
        "lead": ("Decisions the PM answered in the Discovery question files, and answers the PM "
                 "added right before handoff, copied as they are. What was decided and why is in "
                 "`discovery-document.md`; this table shows **who decided**."),
        "legend": ("- **PM choice**: the PM picked or wrote it.\n"
                   "- **AI suggestion kept**: the PM kept the option an AI marked as the default. "
                   "If it shapes the build, confirm it with the PM.\n"
                   "- **AI suggestion · PM confirmed**: as above, and the PM confirmed it before handoff.\n"
                   "- **Added at handoff (PM)**: the PM answered it outside Discovery, right before handoff."),
        "columns": ("Source", "Question", "Answer", "Decided by"),
        "pm": "PM choice", "ai": "AI suggestion kept", "confirmed": "AI suggestion · PM confirmed",
        "handoff": "Added at handoff (PM)", "handoff_title": "## Added at handoff (PM)",
        "handoff_columns": ("Question", "Answer", "Answered at"),
        "unknown": "Unknown (moved to open decisions)", "checking": "Being checked with",
        "stages": {"envision": "Envision", "solution_analysis": "Solution analysis",
                   "use_case_intake": "Use case intake", "prioritization": "Prioritization",
                   "prototype": "Prototype", "product_strategy": "Product strategy",
                   "go_to_market": "Go-to-Market", "other": "Other"},
        "topics": {
            "problem.evidence": "Evidence that the problem is real",
            "problem.why_now": "Why it must be solved now",
            "goals.success": "What counts as success", "non_goals.list": "What will not be built",
            "constraints.rules": "Regulations, internal rules and systems to work with",
            "assumptions.must_be_true": "What must be true to succeed",
            "assumptions.failure_reasons": "The most likely reasons it would fail",
        },
    },
}

def labels(language: str) -> dict:
    """This module's wording in the project language — the handoff section title and the topic names are reused elsewhere."""
    return _TEXT.get(language, _TEXT["ko"])


_SINGLE = re.compile(r"^([A-J])\s*(?::\s*(.*))?$", re.DOTALL)


def build(contents: Mapping[str, str], record: supplement_mod.Supplement, language: str) -> str:
    """Question files (workspace path → content) and handoff tab records → `decisions.md`."""
    lab = _TEXT.get(language, _TEXT["ko"])
    confirmed = set(record.confirmed)
    by_stage: dict[str, list[tuple[str, str, str, str]]] = {}
    for path in sorted(p for p in contents if p.endswith("-questions.md")):
        parsed = parse_question_file(path.rsplit("/", 1)[-1], contents[path])
        cite = path.removeprefix("aiplc-docs/discovery/")
        for q in parsed.questions:
            if not (q.answer or "").strip():
                continue
            suggested = readiness_mod.suggested_letters(q)
            chosen = readiness_mod.chosen_letters(q)
            if chosen & suggested:
                who = (lab["confirmed"]
                       if supplement_mod.confirmation_key(path, q.number) in confirmed
                       else lab["ai"])
            else:
                who = lab["pm"]
            by_stage.setdefault(readiness_mod.stage_of(path), []).append(
                (f"{cite} Q{q.number}", q.ask or q.text, _answer(q, chosen), who))

    head = "| " + " | ".join(lab["columns"]) + " |\n|" + "---|" * len(lab["columns"])
    parts = [lab["title"], lab["lead"], lab["legend"]]
    for stage in _STAGE_ORDER:
        rows = by_stage.get(stage)
        if not rows:
            continue
        body = "\n".join("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
        parts.append(f"## {lab['stages'][stage]}\n\n{head}\n{body}")
    handoff = _handoff_rows(record, lab)
    if handoff:
        cols = lab["handoff_columns"]
        head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
        body = "\n".join("| " + " | ".join(_cell(c) for c in row) + " |" for row in handoff)
        parts.append(f"{lab['handoff_title']}\n\n{head}\n{body}")
    return "\n\n".join(parts) + "\n"


def _answer(q: Question, chosen: set[str]) -> str:
    """The text of the chosen option (with the elaboration appended if any). If not an option letter, the PM's answer as written."""
    if not chosen:
        return (q.answer or "").strip()
    texts = [o.text.partition(readiness_mod.SUGGESTION_MARK)[0].strip()
             for o in q.options if o.letter in chosen and not o.is_other]
    match = _SINGLE.match((q.answer or "").strip())
    remark = (match.group(2) or "").strip() if match else ""
    return " / ".join(texts) + (f" — {remark}" if remark else "")


def _handoff_rows(record: supplement_mod.Supplement, lab: dict) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for qid, a in record.answers.items():
        rows.append((lab["topics"].get(qid, qid), lab["unknown"] if a.unknown else a.text,
                     a.updated_at))
    for a in record.open_answers.values():
        if a.unknown:
            text = lab["unknown"]
        elif a.text:
            text = a.text
        else:
            text = f"{lab['checking']}: {a.pending_with}"
        rows.append((a.question, text, a.updated_at))
    return rows


def _cell(text: str) -> str:
    """One table cell — so that line breaks and vertical bars do not break the table."""
    return " ".join(text.replace("|", "\\|").split())
