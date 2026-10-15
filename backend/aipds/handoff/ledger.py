# backend/aipds/handoff/ledger.py — 결정 장부: Discovery에서 PM이 답한 결정을 그대로 옮긴 표.
#
# **왜 있는가.** Discovery 문서는 결정의 결과를 담지만, 그 결정이 PM이 고른 것인지 AI가 제안한
# 기본값을 그대로 받은 것인지는 담지 않는다. 개발이 어떤 결정을 믿고 어떤 결정을 다시 확인할지는
# 그 차이로 정해진다 — 실측에서 AI가 기본값을 표시한 문항의 89%가 그대로 골라졌다.
#
# **모델을 부르지 않는다.** 질문 파일의 답과 인계 탭의 기록을 표로 옮길 뿐이다. 다시 쓰는 문서는
# 원본과 어긋날 수 있고(실측: 다시 쓴 PRD의 내부 모순), 같은 입력에서 같은 표가 나와야 사람이
# 원본과 대조할 수 있다.
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

_SINGLE = re.compile(r"^([A-J])\s*(?::\s*(.*))?$", re.DOTALL)


def build(contents: Mapping[str, str], record: supplement_mod.Supplement, language: str) -> str:
    """질문 파일(워크스페이스 경로 → 내용)과 인계 탭 기록 → `decisions.md`."""
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
    """고른 보기의 문장(부연이 있으면 덧붙인다). 보기 글자가 아니면 PM이 쓴 답 그대로."""
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
    """표 칸 하나 — 줄바꿈과 세로줄이 표를 깨지 않게."""
    return " ".join(text.replace("|", "\\|").split())
