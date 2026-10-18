# backend/tests/test_handoff_readiness.py — handoff/readiness.assess
#
# The path shapes follow real projects in the drill bucket as of 2026-10-09: A.1 is ws-g
# (the only project that went as far as Strategy/GTM), B is ws-n (a project that wrote a v0.9 Discovery
# Document without Envision). EP1 has no measurements, so it is built from the rules' paths.
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.handoff.readiness import SECTIONS, assess, contents_needed
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

D = "aiplc-docs/discovery/"

A1 = [
    "aiplc-docs/aiplc-state.md", "aiplc-docs/audit.md",
    D + "discovery-document.md",
    D + "envision/business-context.md", D + "envision/pain-point-analysis.md",
    D + "envision/prfaq-clarifying-questions.md",
    D + "solution-analysis/identified-solutions.md",
    D + "prototype/prototype-spec.md", D + "prototype/build-instructions.md",
    D + "prototype/validation-results.md",
    D + "product-strategy/strategy-questions.md",
    D + "go-to-market/gtm-questions.md",
]

B = [
    "aiplc-docs/aiplc-state.md", "aiplc-docs/audit.md",
    D + "discovery-document.md",
    D + "use-case-intake/use-cases.md", D + "use-case-intake/use-case-intake-questions.md",
    D + "prioritization/ranking.md", D + "prioritization/scoring.md",
    D + "prototypes/customer-inquiry-triage/PROTOTYPE-customer-inquiry-triage.md",
    D + "prototypes/customer-inquiry-triage/validation-questionnaire.md",
    D + "prototypes/flight-disruption-notice/PROTOTYPE-flight-disruption-notice.md",
    D + "validation-plan.md",
]


def _sections(readiness):
    return {s.key: s for s in readiness.sections}


def test_a1_with_strategy_and_gtm_has_every_section_sourced():
    r = assess(A1, {})
    assert r.origin == "A.1"
    assert r.blockers == []
    statuses = {k: s.status for k, s in _sections(r).items()}
    assert statuses == {k: "sourced" for k in SECTIONS} | {"open_questions": "derived"}
    [proto] = r.prototypes
    assert proto.id == "prototype" and proto.validation == "validated"


def test_path_b_without_envision_leaves_the_prfaq_sections_partial():
    """ws-n's shape: there is a Discovery Document but no Envision — it is not a PR/FAQ."""
    r = assess(B, {})
    assert r.origin == "B"
    s = _sections(r)
    assert s["scenarios"].status == "sourced"
    for key in ("problem", "audience", "goals", "requirements", "non_goals",
                "constraints", "assumptions"):
        assert s[key].status == "partial", key
    assert D + "use-case-intake/use-cases.md" in s["problem"].sources
    # If the only material for the requirements is the spec, it is prototype scope.
    assert D + "discovery-document.md" not in s["requirements"].sources


def test_path_b_validation_is_only_planned_when_no_results_exist():
    r = assess(B, {})
    by_id = {p.id: p for p in r.prototypes}
    assert by_id["customer-inquiry-triage"].validation == "planned"
    assert by_id["customer-inquiry-triage"].evidence == [
        D + "prototypes/customer-inquiry-triage/validation-questionnaire.md"]
    # If the directory has nothing, the validation plan outside the prototype is cited as the basis (ws-n).
    assert by_id["flight-disruption-notice"].evidence == [D + "validation-plan.md"]


def test_prototype_files_alone_are_entry_point_one():
    paths = [D + "prototypes/store-assist/PROTOTYPE-store-assist.md"]
    r = assess(paths, {})
    assert r.origin == "EP1"
    s = _sections(r)
    assert s["problem"].status == "partial"
    assert s["constraints"].sources == paths
    assert s["goals"].status == "partial"


def test_envision_without_a_spec_cannot_generate_yet():
    r = assess([D + "envision/pain-point-analysis.md", D + "envision/mode-selection-questions.md"], {})
    assert r.origin == "A"
    assert r.blockers == ["no_spec"]
    assert _sections(r)["scenarios"].status == "missing"


def test_an_empty_workspace_has_no_origin():
    r = assess([], {})
    assert r.origin is None and r.blockers == ["no_spec"] and r.prototypes == []


def test_envision_then_several_prototypes_is_a2():
    paths = [D + "envision/pain-point-analysis.md", D + "discovery-document.md",
             D + "prioritization/ranking.md",
             D + "prototypes/a/PROTOTYPE-a.md", D + "prototypes/b/PROTOTYPE-b.md"]
    r = assess(paths, {})
    assert r.origin == "A.2"
    s = _sections(r)
    # There is a PR/FAQ, so the product scope is established.
    assert s["requirements"].status == "sourced"
    # PROTOTYPE's Future Enhancements are not the product's "what we will not do".
    assert s["non_goals"].status == "partial"


def test_the_spec_as_built_is_the_spec_of_record():
    paths = A1 + [D + "prototype/spec-as-built.md"]
    [proto] = assess(paths, {}).prototypes
    assert proto.spec == D + "prototype/spec-as-built.md"
    assert D + "prototype/spec-as-built.md" in _sections(assess(paths, {}))["scenarios"].sources


def test_a_web_survey_alone_is_survey_evidence():
    paths = [D + "prototypes/a/PROTOTYPE-a.md", D + "prototypes/a/survey-aggregate.md"]
    [proto] = assess(paths, {}).prototypes
    assert proto.validation == "survey"


QUESTIONS = """\
# Strategy Questions

## Question 1
What is the revenue model?

A) Subscription ← 제안: 페인 포인트 분석에서 도출
B) One-time purchase
X) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 2
Pricing?

A) Premium ← 추정
B) Budget
X) Other

[Answer]: A: 단, 첫 해는 할인

## Question 3
Channels?

A) Direct sales ← Suggested based on your Discovery work
B) Partners
C) Self-serve
X) Other

[Answer]: B

## Question 4
Which markets (select all that apply)?

A) Seoul ← Suggested
B) Busan
C) Daegu
X) Other

[Answer]: A,C

## Question 5
Broker?

A) Broker A ← 제안
B) Broker B
X) Other

[Answer]: Broker: 큐를 따로 둔다

## Question 6
Launch date?

A) Q1 ← 추천
B) Q2
X) Other

[Answer]:

## Question 7
Team size?

A) 3
B) 5
X) Other

[Answer]: B
"""


def test_any_arrow_marker_is_an_ai_suggestion_and_only_letter_answers_accept_it():
    path = D + "product-strategy/strategy-questions.md"
    r = assess([path], {path: QUESTIONS})
    d = r.ai_defaults
    # 6 questions answered (Q6 unanswered), of which 5 had a marker (Q7 had none).
    assert (d.answered, d.suggested) == (6, 5)
    # Q1 "A", Q2 "A: 부연", and Q4 "A,C" are acceptances. Q3 "B" is a rejection, and Q5's free-form answer is not an acceptance.
    assert [i.number for i in d.items] == [1, 2, 4]
    assert d.accepted == 3
    assert d.items[0].file == path


def test_real_korean_corpus_has_suggestions_under_translated_markers():
    """The ko question files in fixtures/real use translated markers like `← 권고` and `← 추천: …`."""
    root = Path(__file__).parent / "fixtures" / "real"
    files = {str(p.relative_to(root)): p.read_text(encoding="utf-8")
             for p in root.glob("*/discovery/**/*-questions.md")}
    r = assess(list(files), files)
    assert r.ai_defaults.suggested > 0
    assert r.ai_defaults.accepted <= r.ai_defaults.suggested <= r.ai_defaults.answered


def test_broken_references_in_the_discovery_document_are_reported():
    doc = "## 검증 결과\n전체 결과: `prototype/validation-results.md`\n"
    paths = [p for p in A1 if not p.endswith("validation-results.md")]
    r = assess(paths, {D + "discovery-document.md": doc})
    assert r.broken_references == ["prototype/validation-results.md"]
    assert r.discovery_document == D + "discovery-document.md"


def test_contents_needed_is_question_files_and_the_document():
    assert contents_needed(A1) == [
        D + "discovery-document.md", D + "envision/prfaq-clarifying-questions.md",
        D + "product-strategy/strategy-questions.md", D + "go-to-market/gtm-questions.md"]


client = TestClient(app)


def test_route_reads_the_workspace(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    assert client.post("/projects", json={"project_id": "handoff-b"}).status_code == 200
    ws = registry.get("handoff-b")
    files = {p: "# x\n" for p in B} | {D + "use-case-intake/use-case-intake-questions.md": QUESTIONS}
    async def seed():
        for path, content in files.items():
            await ws.runner.write_file(path, content)
    asyncio.get_event_loop().run_until_complete(seed())

    r = client.get("/projects/handoff-b/handoff/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["origin"] == "B"
    assert body["ai_defaults"]["accepted"] == 3
    assert [s["key"] for s in body["sections"]] == list(SECTIONS)


def test_route_unknown_project_is_404():
    assert client.get("/projects/nope/handoff/readiness").status_code == 404


def test_accepted_suggestions_carry_the_decision_not_just_the_letter():
    """Measured (ws-g): all 40 confirmation items showed as "고른 답: A", so there was no way to tell what was being confirmed."""
    path = D + "product-strategy/strategy-questions.md"
    first, second, fourth = assess([path], {path: QUESTIONS}).ai_defaults.items
    assert first.stage == "product_strategy"
    assert first.choices == ["Subscription"]
    assert first.note == "제안: 페인 포인트 분석에서 도출"
    assert first.remark == ""
    assert second.choices == ["Premium"] and second.remark == "단, 첫 해는 할인"
    assert fourth.choices == ["Seoul", "Daegu"] and fourth.note == "Suggested"


@pytest.mark.parametrize("path, stage", [
    (D + "envision/prfaq-clarifying-questions.md", "envision"),
    (D + "go-to-market/gtm-clarification-questions.md", "go_to_market"),
    (D + "prototype/build-decision-questions.md", "prototype"),
    (D + "prototypes/a/prototype-context-questions.md", "prototype"),
    (D + "discovery-mode-selection-questions.md", "other"),
])
def test_question_files_are_grouped_by_discovery_stage(path, stage):
    from aipds.handoff.readiness import stage_of
    assert stage_of(path) == stage


# ---- Spec consistency: has the spec of record reflected the change history through its last entry ----
#
# What the build session changed after approval exists only in the change history. If `spec-as-built.md` is missing or
# stopped before the last entry, the spec the package receives differs from the prototype the user saw.

_HISTORY = D + "prototype/change-history.md"
_AS_BUILT = D + "prototype/spec-as-built.md"
_TWO_ENTRIES = ("# 수정 이력\n\n머리말\n"
                "\n## 2026-10-13 16:27 UTC · gen 1\n\n**변경**\n검색 제거\n"
                "\n## 2026-10-17 01:21 UTC\n\n**변경**\n없음\n")


def _proto(extra_paths, contents):
    [proto] = assess(A1 + extra_paths, contents).prototypes
    return proto


def test_without_a_change_history_there_is_nothing_to_reconcile():
    proto = _proto([], {})
    assert proto.reconcile == "not_needed"
    assert proto.history is None


def test_a_history_with_only_its_header_has_nothing_to_reconcile():
    """A history with only a header means nothing changed after approval."""
    assert _proto([_HISTORY], {_HISTORY: "# 수정 이력\n\n머리말\n"}).reconcile == "not_needed"


def test_a_history_without_spec_as_built_needs_reconciling():
    proto = _proto([_HISTORY], {_HISTORY: _TWO_ENTRIES})
    assert proto.reconcile == "missing"
    assert proto.history == _HISTORY


def test_spec_as_built_that_stops_before_the_newest_entry_is_stale():
    """Entries with no change are also in scope for reflection — the reconciliation pass records every entry it read."""
    as_built = "# Spec as Built\n\n근거: 2026-10-13 16:27 UTC · gen 1\n"
    proto = _proto([_HISTORY, _AS_BUILT], {_HISTORY: _TWO_ENTRIES, _AS_BUILT: as_built})
    assert proto.reconcile == "stale"
    assert proto.spec == _AS_BUILT              # still closer than the approved spec


def test_spec_as_built_citing_the_newest_entry_is_current():
    as_built = ("# Spec as Built\n\n근거: 2026-10-13 16:27 UTC · gen 1, "
                "2026-10-17T01:21 UTC\n")
    proto = _proto([_HISTORY, _AS_BUILT], {_HISTORY: _TWO_ENTRIES, _AS_BUILT: as_built})
    assert proto.reconcile == "current"


def test_contents_needed_includes_the_reconciliation_pair():
    paths = A1 + [_HISTORY, _AS_BUILT]
    needed = contents_needed(paths)
    assert _HISTORY in needed and _AS_BUILT in needed
