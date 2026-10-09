# backend/tests/test_handoff_readiness.py — handoff/readiness.assess
#
# 경로 모양은 2026-10-09 드릴 버킷의 실제 프로젝트를 따른다: A.1은 industry-safe-law
# (Strategy·GTM까지 간 유일한 프로젝트), B는 test2222(Envision 없이 v0.9 Discovery
# Document를 쓴 프로젝트). EP1은 실측이 없어 룰의 경로로 만든다.
from __future__ import annotations

import asyncio
from pathlib import Path

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
    """test2222의 모양: Discovery Document는 있지만 Envision이 없다 — PR/FAQ가 아니다."""
    r = assess(B, {})
    assert r.origin == "B"
    s = _sections(r)
    assert s["scenarios"].status == "sourced"
    for key in ("problem", "audience", "goals", "requirements", "non_goals",
                "constraints", "assumptions"):
        assert s[key].status == "partial", key
    assert D + "use-case-intake/use-cases.md" in s["problem"].sources
    # 요구사항의 재료가 명세뿐이면 프로토타입 범위다.
    assert D + "discovery-document.md" not in s["requirements"].sources


def test_path_b_validation_is_only_planned_when_no_results_exist():
    r = assess(B, {})
    by_id = {p.id: p for p in r.prototypes}
    assert by_id["customer-inquiry-triage"].validation == "planned"
    assert by_id["customer-inquiry-triage"].evidence == [
        D + "prototypes/customer-inquiry-triage/validation-questionnaire.md"]
    # 디렉터리에 아무것도 없으면 프로토타입 밖의 검증 계획을 근거로 든다(test2222).
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
    # PR/FAQ가 있으니 제품 범위가 잡힌다.
    assert s["requirements"].status == "sourced"
    # PROTOTYPE의 Future Enhancements는 제품의 "하지 않을 것"이 아니다.
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
    # 답한 문항 6(Q6 미답), 그중 표시가 있던 문항 5(Q7 없음).
    assert (d.answered, d.suggested) == (6, 5)
    # Q1 "A", Q2 "A: 부연", Q4 "A,C"는 수락. Q3 "B"는 거절, Q5 자유 서술은 수락이 아니다.
    assert [i.number for i in d.items] == [1, 2, 4]
    assert d.accepted == 3
    assert d.items[0].file == path


def test_real_korean_corpus_has_suggestions_under_translated_markers():
    """fixtures/real의 ko 질문 파일은 `← 권고`, `← 추천: …`처럼 번역된 표시를 쓴다."""
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
