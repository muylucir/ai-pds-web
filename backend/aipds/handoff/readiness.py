# backend/aipds/handoff/readiness.py — 인계 패키지를 만들 재료가 무엇이 있고 무엇이 없는가.
#
# **왜 있는가.** 인계 패키지(PRD·검증 보고·남은 작업)는 Discovery 산출물에서 유도하는
# 웹 소유 파생물이다(docs/superpowers/plans/2026-10-09-handoff-package.md). 출발점마다
# 룰이 남기는 산출물이 다르다 — Path B와 EP1은 Envision을 거치지 않아 PR/FAQ가 없다.
# 그래서 생성 전에 "어느 섹션의 재료가 없는가"를 먼저 보여 주고, 없는 곳은 지어내지 않고
# 보완 질문이나 "재료 없음"으로 넘긴다. 그 판정이 이 모듈이다.
#
# **결정적이고 IO가 없다.** 경로 목록과 몇몇 파일의 내용만 받는다. 라우트가 읽어서 넘기고,
# 같은 함수를 드릴 버킷 덤프에도 그대로 돌린다 — 판정 규칙을 실측으로 검증하는 경로가
# 제품 경로와 같아야 둘이 갈라지지 않는다.
#
# **파일의 존재로 판정한다.** 상태 파일의 `Entry Point` 줄은 에이전트가 자유 서술로 쓰고
# (실측 test2222: "Entry Point 3 (Path B — 보유 유스케이스에서 시작)"), ko 프로젝트는
# 템플릿 라벨을 번역한다. 문구로 읽으면 언어마다 깨진다. 경로는 discovery-config/CLAUDE.md와
# 상류 룰이 고정한다. 대가는 정밀도다: 파일이 있어도 그 절이 비어 있을 수 있고, 절 단위
# 판정은 패키지 합성 단계가 한다.
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from aipds.models import Question
from aipds.parsers.doc_references import missing_references
from aipds.parsers.questions import parse_question_file
from aipds.proto import layout

_DISCOVERY = layout.DISCOVERY_PREFIX
_ENVISION = _DISCOVERY + "envision/"
_INTAKE = _DISCOVERY + "use-case-intake/"
_USE_CASES = _INTAKE + "use-cases.md"
_PRIORITIZATION = _DISCOVERY + "prioritization/"
_STRATEGY = _DISCOVERY + "product-strategy/"
_GTM = _DISCOVERY + "go-to-market/"
#: 개발 조직에 넘기는 입구 문서. Path A에서는 Envision이 만들고 PR/FAQ가 그 Part 1이다.
DISCOVERY_DOCUMENT = _DISCOVERY + "discovery-document.md"
#: Path B에서 에이전트가 프로토타입 디렉터리 밖에 두는 검증 계획(실측 test2222).
_GLOBAL_VALIDATION_PLAN = _DISCOVERY + "validation-plan.md"
_QUESTION_SUFFIX = "-questions.md"

#: pain point 분석의 두 모양: 대화형(`pain-point-analysis.md`)과 URL·보고서형
#: (`pain-points-from-url.md`, 실측 tobacco `pain-points-from-report.md`).
_PAIN_POINTS = re.compile(
    r"^aiplc-docs/discovery/envision/(?:pain-point-analysis|pain-points-from-[^/]+)\.md$")
_PAIN_POINT_ANALYSIS = _ENVISION + "pain-point-analysis.md"

#: 검증 근거 파일. 앞의 것이 강하다.
_VALIDATION_RESULTS = "validation-results.md"
_PLANNED = ("validation-questionnaire.md", "validation-plan.md")
#: Proceed 직전의 명세 정합성 패스가 쓰는 "실제로 만든 명세"(discovery-config/CLAUDE.md).
_SPEC_AS_BUILT = "spec-as-built.md"

#: AI-PLC가 Option A에 붙이는 "intelligent default" 표시. 문구가 아니라 화살표로 본다 —
#: 실측(드릴 11개 프로젝트) 뒤의 말은 "제안", "추정", "Suggested", "Discovery 결과 …",
#: "페인 포인트 …"로 제각각이고 `parsers.questions._RECO`가 읽는 "추천"은 0건이었다.
_SUGGESTION_MARK = "←"
#: 답이 보기 글자인 두 모양: `"A"`, `"A: 부연"`(answer_summary 헤더의 계약).
_SINGLE_LETTER = re.compile(r"^([A-J])\s*(?::.*)?$", re.DOTALL)

Origin = Literal["A.1", "A.2", "A", "B", "EP1"]
SectionStatus = Literal["sourced", "partial", "missing", "derived"]
ValidationStatus = Literal["validated", "survey", "planned", "none"]

#: PRD 섹션의 순서와 키. 화면과 패키지가 이 순서를 그대로 쓴다.
SECTIONS = ("problem", "audience", "goals", "scenarios", "requirements",
            "non_goals", "constraints", "assumptions", "open_questions")


class Prototype(BaseModel):
    id: str
    #: 이 프로토타입의 기준 명세. 승인된 `spec-as-built.md`가 있으면 그쪽이다.
    spec: str
    validation: ValidationStatus
    evidence: list[str]


class Section(BaseModel):
    key: str
    number: int
    status: SectionStatus
    #: 이 섹션의 재료가 된 파일. 1차 재료가 앞이다.
    sources: list[str]


#: 질문 파일이 속한 Discovery 단계. 화면이 파일 이름 대신 단계로 묶는다 — PM에게
#: `strategy-questions.md`는 의미가 없다.
Stage = Literal["envision", "solution_analysis", "use_case_intake", "prioritization",
                "prototype", "product_strategy", "go_to_market", "other"]
_STAGE_DIRS: tuple[tuple[str, Stage], ...] = (
    ("envision/", "envision"), ("solution-analysis/", "solution_analysis"),
    ("use-case-intake/", "use_case_intake"), ("prioritization/", "prioritization"),
    ("prototypes/", "prototype"), ("prototype/", "prototype"),
    ("product-strategy/", "product_strategy"), ("go-to-market/", "go_to_market"),
)


def stage_of(path: str) -> Stage:
    rest = path[len(_DISCOVERY):] if path.startswith(_DISCOVERY) else path
    for prefix, stage in _STAGE_DIRS:
        if rest.startswith(prefix):
            return stage
    return "other"


class AcceptedSuggestion(BaseModel):
    file: str
    number: int
    ask: str
    #: 답의 원문(`"A"`, `"A: 부연"`, `"A,C"`).
    answer: str
    stage: Stage = "other"
    #: 고른 보기의 문장 — 실제 결정이다. 여럿이면 순서대로. `answer`의 글자만으로는 PM이 무엇을
    #: 확인하는지 알 수 없다(실측: 확인 항목 40개가 전부 "고른 답: A"로 보였다).
    choices: list[str] = []
    #: 보기의 `←` 뒤에 AI가 붙인 메모(예: "페인 포인트 분석 기반 제안").
    note: str = ""
    #: `"A: 부연"`의 부연 — PM이 보기에 덧붙인 말.
    remark: str = ""


class AiDefaults(BaseModel):
    #: 답이 있는 문항 수.
    answered: int
    #: 그중 AI 제안 표시가 붙은 보기가 있던 문항 수.
    suggested: int
    #: 그중 제안된 보기를 고른 문항 수. 표시 없이 A에 둔 기본값은 셀 수 없으므로 하한이다.
    accepted: int
    items: list[AcceptedSuggestion]


class Readiness(BaseModel):
    origin: Origin | None
    prototypes: list[Prototype]
    sections: list[Section]
    ai_defaults: AiDefaults
    discovery_document: str | None
    broken_references: list[str]
    #: 생성을 막는 사유. 비어 있으면 만들 수 있다.
    blockers: list[str]


def contents_needed(paths: Sequence[str]) -> list[str]:
    """`assess`가 내용까지 읽어야 하는 파일 — 질문 파일과 Discovery Document."""
    return [p for p in paths
            if p.endswith(_QUESTION_SUFFIX) or p == DISCOVERY_DOCUMENT]


def assess(paths: Sequence[str], contents: Mapping[str, str]) -> Readiness:
    """워크스페이스 상대 경로 목록과 `contents_needed`의 내용 → 인계 준비 상태."""
    present = set(paths)
    envision = any(p.startswith(_ENVISION) for p in present)
    specs = layout.discover(sorted(present))
    origin = _origin(present, envision, set(specs))
    prototypes = [_prototype(pid, spec, present) for pid, spec in sorted(specs.items())]
    has_document = DISCOVERY_DOCUMENT in present
    document = contents.get(DISCOVERY_DOCUMENT) if has_document else None
    return Readiness(
        origin=origin,
        prototypes=prototypes,
        sections=_sections(present, envision and has_document, prototypes),
        ai_defaults=_ai_defaults(contents),
        discovery_document=DISCOVERY_DOCUMENT if has_document else None,
        broken_references=(missing_references(document, sorted(present))
                           if document is not None else []),
        # PRD의 최소 재료는 "무엇을 만드는가"를 적은 명세다. 단계 미완료(Strategy·GTM
        # 없음)는 막지 않는다 — 실측 11개 중 GTM까지 간 프로젝트는 하나였다.
        blockers=[] if specs else ["no_spec"],
    )


def _origin(present: set[str], envision: bool, spec_ids: set[str]) -> Origin | None:
    single = layout.SINGLE_ID in spec_ids
    slugged = bool(spec_ids - {layout.SINGLE_ID})
    intake_or_priority = any(p.startswith((_INTAKE, _PRIORITIZATION)) for p in present)
    if envision:
        if single:
            return "A.1"
        if slugged or intake_or_priority:
            return "A.2"
        return "A"
    if intake_or_priority:
        return "B"
    if slugged:
        return "EP1"
    return None


def _prototype(pid: str, spec: str, present: set[str]) -> Prototype:
    directory = layout.artifact_dir(pid) + "/"
    as_built = directory + _SPEC_AS_BUILT
    results = directory + _VALIDATION_RESULTS
    survey = layout.survey_aggregate_key(pid)
    planned = [directory + name for name in _PLANNED if directory + name in present]
    if results in present:
        status, evidence = "validated", [results]
    elif survey in present:
        status, evidence = "survey", [survey]
    elif planned or _GLOBAL_VALIDATION_PLAN in present:
        status = "planned"
        evidence = planned or [_GLOBAL_VALIDATION_PLAN]
    else:
        status, evidence = "none", []
    return Prototype(id=pid, spec=as_built if as_built in present else spec,
                     validation=status, evidence=evidence)


def _sections(present: set[str], prfaq: bool,
              prototypes: list[Prototype]) -> list[Section]:
    """섹션마다 1차 재료(있으면 sourced)와 2차 재료(그것만 있으면 partial).

    PR/FAQ는 Envision이 `discovery-document.md`의 Part 1로 쓴다 — Envision 없이 생긴
    Discovery Document(실측 test2222의 v0.9 중간 통합)에는 없으므로 둘이 함께 있어야
    PR/FAQ가 있다고 본다.
    """
    def under(prefix: str) -> list[str]:
        return sorted(p for p in present if p.startswith(prefix))

    pain = sorted(p for p in present if _PAIN_POINTS.match(p))
    analysis = [_PAIN_POINT_ANALYSIS] if _PAIN_POINT_ANALYSIS in present else []
    strategy, gtm = under(_STRATEGY), under(_GTM)
    use_cases = [_USE_CASES] if _USE_CASES in present else []
    prfaq_doc = [DISCOVERY_DOCUMENT] if prfaq else []
    specs = [p.spec for p in prototypes]
    single = [p.spec for p in prototypes if p.id == layout.SINGLE_ID]
    slugged = [p.spec for p in prototypes if p.id != layout.SINGLE_ID]

    table: dict[str, tuple[list[str], list[str]]] = {
        "problem": (pain, use_cases + specs),
        "audience": (analysis + strategy, use_cases + specs),
        "goals": (strategy + gtm, specs),
        "scenarios": (specs, []),
        # 제품 범위는 PR/FAQ가 잡는다. 명세만 있으면 요구사항이 프로토타입 크기로 줄어든다
        # (PROTOTYPE 템플릿은 "도구 1~2개", "Mock Data"로 범위를 줄여 쓰게 한다).
        "requirements": (specs + prfaq_doc if specs and prfaq_doc else [],
                         specs or prfaq_doc),
        # A.1 명세의 Out of Scope 표와 GTM의 MVP 범위가 제품의 "하지 않을 것"이다.
        # PROTOTYPE의 Future Enhancements는 "이 프로토타입에서는 아님"이라 2차다.
        "non_goals": (single + gtm, slugged),
        "constraints": (prfaq_doc, use_cases + specs),
        "assumptions": (prfaq_doc, specs),
    }
    sections: list[Section] = []
    for number, key in enumerate(SECTIONS, start=1):
        if key == "open_questions":
            # 다른 섹션의 빈칸과 보완 질문의 "모름"에서 유도한다 — 재료가 따로 없다.
            sections.append(Section(key=key, number=number, status="derived", sources=[]))
            continue
        primary, secondary = table[key]
        status: SectionStatus = ("sourced" if primary
                                 else "partial" if secondary else "missing")
        sources = list(dict.fromkeys(primary + secondary))
        sections.append(Section(key=key, number=number, status=status, sources=sources))
    return sections


def _ai_defaults(contents: Mapping[str, str]) -> AiDefaults:
    answered = suggested = 0
    items: list[AcceptedSuggestion] = []
    for path in sorted(contents):
        if not path.endswith(_QUESTION_SUFFIX):
            continue
        parsed = parse_question_file(path.rsplit("/", 1)[-1], contents[path])
        for question in parsed.questions:
            if not question.answer:
                continue
            answered += 1
            marked = _suggested_letters(question)
            if not marked:
                continue
            suggested += 1
            chosen = _chosen_letters(question)
            if chosen & marked:
                choices, note = _chosen_texts(question, chosen)
                items.append(AcceptedSuggestion(
                    file=path, number=question.number,
                    ask=question.ask or question.text, answer=question.answer,
                    stage=stage_of(path), choices=choices, note=note,
                    remark=_remark(question.answer or "")))
    return AiDefaults(answered=answered, suggested=suggested,
                      accepted=len(items), items=items)


def _suggested_letters(question: Question) -> set[str]:
    return {o.letter for o in question.options
            if not o.is_other and (o.recommended or _SUGGESTION_MARK in o.text)}


def _chosen_texts(question: Question, chosen: set[str]) -> tuple[list[str], str]:
    """고른 보기의 문장들과, 그 보기에 AI가 붙인 `←` 메모(처음 것)."""
    choices: list[str] = []
    note = ""
    for option in question.options:
        if option.letter not in chosen or option.is_other:
            continue
        text, _, mark = option.text.partition(_SUGGESTION_MARK)
        choices.append(text.strip())
        if mark.strip() and not note:
            note = mark.strip()
    return choices, note


def _remark(answer: str) -> str:
    match = _SINGLE_LETTER.match(answer.strip())
    if not match:
        return ""
    _, _, rest = answer.partition(":")
    return rest.strip()


def _chosen_letters(question: Question) -> set[str]:
    """답이 보기 글자일 때만 그 글자들. 자유 서술은 빈 집합 — 수락으로 세지 않는다.

    `answer_summary`가 펼치는 모양과 같다: `"A"`, `"A: 부연"`, `"A,C"`. 토큰 하나라도
    보기 글자가 아니면 전체를 자유 서술로 본다("Broker: 큐를 …"를 B로 읽지 않는다).
    """
    value = (question.answer or "").strip()
    letters = {o.letter for o in question.options if not o.is_other}
    match = _SINGLE_LETTER.match(value)
    if match and match.group(1) in letters:
        return {match.group(1)}
    parts = [p.strip() for p in value.split(",")]
    if len(parts) > 1 and all(p in letters for p in parts):
        return set(parts)
    return set()
