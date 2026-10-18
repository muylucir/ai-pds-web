# backend/aipds/handoff/readiness.py — what material exists, and what is missing, for building the handoff package.
#
# **Why this exists.** The handoff package (open decisions, decision ledger) is a web-owned derivative
# built from Discovery artifacts (docs/superpowers/plans/2026-10-09-handoff-package.md). Each starting
# point leaves different artifacts from the rules — Path B and EP1 skip Envision, so there is no PR/FAQ.
# So before generation we first show "which sections have no material", and where material is missing
# we do not make anything up but hand it off as a supplementary question or as "재료 없음" (no source
# material). This module makes that determination.
#
# **Deterministic, with no IO.** It takes only the list of paths and the contents of a few files. The
# route reads and passes them in, and the same function is run as-is on drill bucket dumps too — the
# path that validates the rules against real data must be the same as the product path so the two do not diverge.
#
# **It decides by file existence.** The `Entry Point` line in the state file is written free-form by the
# agent (measured on ws-n: "Entry Point 3 (Path B — 보유 유스케이스에서 시작)", i.e. "starting from
# existing use cases"), and ko projects translate template labels. Reading the wording breaks per language.
# Paths are fixed by discovery-config/CLAUDE.md and the upstream rules. The price is precision: a file can
# exist while its section is empty, and per-section judgment happens in the package synthesis step.
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
#: The entry document handed to the development organization. In Path A, Envision creates it and the PR/FAQ is its Part 1.
DISCOVERY_DOCUMENT = _DISCOVERY + "discovery-document.md"
#: The validation plan the agent places outside the prototype directory in Path B (measured on ws-n).
_GLOBAL_VALIDATION_PLAN = _DISCOVERY + "validation-plan.md"
_QUESTION_SUFFIX = "-questions.md"

#: Two shapes of pain point analysis: conversational (`pain-point-analysis.md`) and URL/report-based
#: (`pain-points-from-url.md`, measured on ws-b: `pain-points-from-report.md`).
_PAIN_POINTS = re.compile(
    r"^aiplc-docs/discovery/envision/(?:pain-point-analysis|pain-points-from-[^/]+)\.md$")
_PAIN_POINT_ANALYSIS = _ENVISION + "pain-point-analysis.md"

#: Validation evidence files. Earlier ones are stronger.
_VALIDATION_RESULTS = "validation-results.md"
_PLANNED = ("validation-questionnaire.md", "validation-plan.md")
#: The "spec as actually built" written by the spec reconciliation pass just before Proceed (discovery-config/CLAUDE.md).
_SPEC_AS_BUILT = "spec-as-built.md"
#: Heading of a change history entry — `## 2026-10-13 16:27 UTC · gen 1` (proto/prompts.change_history_entry).
#: The timestamp has the same shape regardless of language, and the reconciliation pass cites applied entries by exactly this timestamp.
_HISTORY_ENTRY = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", re.MULTILINE)

#: The "intelligent default" marker AI-PLC attaches to Option A. It is detected by the arrow, not the wording —
#: measured (11 drill projects), the words after it varied: "제안" (suggestion), "추정" (estimate), "Suggested",
#: "Discovery 결과 …" (Discovery result …), "페인 포인트 …" (pain point …), and "추천" (recommended), which
#: `parsers.questions._RECO` reads, appeared 0 times.
SUGGESTION_MARK = "←"
#: Two shapes where the answer is an option letter: `"A"`, `"A: note"` (the contract in the answer_summary header).
_SINGLE_LETTER = re.compile(r"^([A-J])\s*(?::.*)?$", re.DOTALL)

Origin = Literal["A.1", "A.2", "A", "B", "EP1"]
SectionStatus = Literal["sourced", "partial", "missing", "derived"]
ValidationStatus = Literal["validated", "survey", "planned", "none"]
#: Whether the spec of record reflects the change history. not_needed: there is no history (nothing changed after
#: approval) · current: `spec-as-built.md` reflects up to the last history entry · missing: there is history but
#: no `spec-as-built.md` · stale: `spec-as-built.md` stopped before the last entry.
ReconcileStatus = Literal["not_needed", "current", "missing", "stale"]

#: Order and keys of the topics Discovery covers. Topics with no material are asked as supplementary questions.
SECTIONS = ("problem", "audience", "goals", "scenarios", "requirements",
            "non_goals", "constraints", "assumptions", "open_questions")


class Prototype(BaseModel):
    id: str
    #: This prototype's spec of record. If an approved `spec-as-built.md` exists, it is that one.
    spec: str
    validation: ValidationStatus
    evidence: list[str]
    #: If missing or stale, the spec the package receives differs from the prototype the user saw — what the build
    #: session changed after approval exists only in the change history. It is reflected by Discovery's reconciliation pass.
    reconcile: ReconcileStatus = "not_needed"
    #: The change history file. None if there is no history.
    history: str | None = None


class Section(BaseModel):
    key: str
    number: int
    status: SectionStatus
    #: Files that served as material for this section. Primary material first.
    sources: list[str]


#: The Discovery stage a question file belongs to. The screen groups by stage instead of file name — to a PM,
#: `strategy-questions.md` means nothing.
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
    #: The raw answer (`"A"`, `"A: note"`, `"A,C"`).
    answer: str
    stage: Stage = "other"
    #: Text of the chosen options — the actual decision. In order if several. From the letter in `answer` alone, the PM
    #: cannot tell what they are confirming (measured: all 40 confirmation items showed up as "고른 답: A" (chosen answer: A)).
    choices: list[str] = []
    #: The note the AI attached after the option's `←` (e.g. "페인 포인트 분석 기반 제안", "suggested based on pain point analysis").
    note: str = ""
    #: The remark in `"A: note"` — what the PM added to the option.
    remark: str = ""


class AiDefaults(BaseModel):
    #: Number of questions with an answer.
    answered: int
    #: Of those, the number of questions that had an option with the AI-suggestion marker.
    suggested: int
    #: Of those, the number where the suggested option was chosen. Defaults left at A without a marker cannot be counted, so this is a lower bound.
    accepted: int
    items: list[AcceptedSuggestion]


class Readiness(BaseModel):
    origin: Origin | None
    prototypes: list[Prototype]
    sections: list[Section]
    ai_defaults: AiDefaults
    discovery_document: str | None
    broken_references: list[str]
    #: Reasons that block generation. If empty, it can be generated.
    blockers: list[str]


def contents_needed(paths: Sequence[str]) -> list[str]:
    """Files whose contents `assess` must read — question files, the Discovery Document, and the two files
    for the reconciliation check (the change history and `spec-as-built.md`)."""
    names = (layout.CHANGE_HISTORY, _SPEC_AS_BUILT)
    return [p for p in paths
            if p.endswith(_QUESTION_SUFFIX) or p == DISCOVERY_DOCUMENT
            or p.rsplit("/", 1)[-1] in names]


def assess(paths: Sequence[str], contents: Mapping[str, str]) -> Readiness:
    """List of workspace-relative paths plus the contents from `contents_needed` → handoff readiness."""
    present = set(paths)
    envision = any(p.startswith(_ENVISION) for p in present)
    specs = layout.discover(sorted(present))
    origin = _origin(present, envision, set(specs))
    prototypes = [_prototype(pid, spec, present, contents)
                  for pid, spec in sorted(specs.items())]
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
        # The minimum material for a handoff is a spec stating "what is being built". Incomplete stages (no Strategy
        # or GTM) do not block — of the 11 measured projects, only one went as far as GTM.
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


def _reconcile(history: str | None, as_built: str | None) -> ReconcileStatus:
    """Whether the timestamp of the last change history entry is cited in `spec-as-built.md`.

    **It checks by content, not by file time.** File modification times are rewritten by bundle import and sync,
    and this module has no IO (header). The reconciliation pass records applied entries using exactly the heading
    timestamp (discovery-config/CLAUDE.md), so if the last entry's timestamp is absent, it stopped before that entry.
    """
    stamps = _HISTORY_ENTRY.findall(history or "")
    if not stamps:
        return "not_needed"
    if as_built is None:
        return "missing"
    # The model transcribing it in ISO shape (`2026-10-13T16:27`) counts as the same citation.
    return "current" if stamps[-1] in as_built.replace("T", " ") else "stale"


def _prototype(pid: str, spec: str, present: set[str],
               contents: Mapping[str, str]) -> Prototype:
    directory = layout.artifact_dir(pid) + "/"
    as_built = directory + _SPEC_AS_BUILT
    history = layout.change_history_key(pid)
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
                     validation=status, evidence=evidence,
                     reconcile=_reconcile(contents.get(history),
                                          contents.get(as_built) if as_built in present else None),
                     history=history if history in present else None)


def _sections(present: set[str], prfaq: bool,
              prototypes: list[Prototype]) -> list[Section]:
    """For each section, primary material (sourced if present) and secondary material (partial if only that).

    The PR/FAQ is written by Envision as Part 1 of `discovery-document.md` — a Discovery Document produced without
    Envision (the v0.9 interim consolidation measured on ws-n) does not have it, so the PR/FAQ is considered
    present only when both exist.
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
        # Product scope is set by the PR/FAQ. With only the spec, the requirements shrink to prototype size
        # (the PROTOTYPE template has scope written down to "도구 1~2개" (1-2 tools) and "Mock Data").
        "requirements": (specs + prfaq_doc if specs and prfaq_doc else [],
                         specs or prfaq_doc),
        # The Out of Scope table in an A.1 spec and the MVP scope in GTM are the product's "will not do".
        # PROTOTYPE's Future Enhancements mean "not in this prototype", so they are secondary.
        "non_goals": (single + gtm, slugged),
        "constraints": (prfaq_doc, use_cases + specs),
        "assumptions": (prfaq_doc, specs),
    }
    sections: list[Section] = []
    for number, key in enumerate(SECTIONS, start=1):
        if key == "open_questions":
            # Derived from the gaps in other sections and the "don't know" answers to supplementary questions — it has no material of its own.
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
            marked = suggested_letters(question)
            if not marked:
                continue
            suggested += 1
            chosen = chosen_letters(question)
            if chosen & marked:
                choices, note = _chosen_texts(question, chosen)
                items.append(AcceptedSuggestion(
                    file=path, number=question.number,
                    ask=question.ask or question.text, answer=question.answer,
                    stage=stage_of(path), choices=choices, note=note,
                    remark=_remark(question.answer or "")))
    return AiDefaults(answered=answered, suggested=suggested,
                      accepted=len(items), items=items)


def suggested_letters(question: Question) -> set[str]:
    return {o.letter for o in question.options
            if not o.is_other and (o.recommended or SUGGESTION_MARK in o.text)}


def _chosen_texts(question: Question, chosen: set[str]) -> tuple[list[str], str]:
    """Text of the chosen options, and the `←` note the AI attached to those options (the first one)."""
    choices: list[str] = []
    note = ""
    for option in question.options:
        if option.letter not in chosen or option.is_other:
            continue
        text, _, mark = option.text.partition(SUGGESTION_MARK)
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


def chosen_letters(question: Question) -> set[str]:
    """The letters only when the answer is option letters. Free text gives the empty set — it is not counted as acceptance.

    Same shapes that `answer_summary` expands: `"A"`, `"A: note"`, `"A,C"`. If even one token is not an option
    letter, the whole answer is treated as free text ("Broker: 큐를 …" ("Broker: the queue …") is not read as B).
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
