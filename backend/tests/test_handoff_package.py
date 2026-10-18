# backend/tests/test_handoff_package.py — handoff/package and the generate/view/download routes
from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.handoff import package, supplement
from aipds.handoff.readiness import assess
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store
from test_handoff_readiness import B, D, QUESTIONS

def _vision(open_table: str) -> str:
    """A vision.md with every required section of the AI-DLC guide. Only the Open Questions table is
    swapped in. Other sections have tables too — open decisions are only the table under Open Questions."""
    return f"""# 샘플몰 안전ON — Vision

## Executive Summary
지점 안전 서명을 전자로 바꾼다. [discovery-document.md · Press Release]

## Business Context
### Problem Statement
종이 서명을 모으는 데 월 30~60시간이 든다. [envision/pain-point-questions.md Q6]
### Target Users and Stakeholders
| User Type | Description | Primary Need |
|---|---|---|
| 지점 담당자 | 21명 | 누락자 추적 [envision/pain-point-questions.md Q1] |
### Success Metrics
| Metric | Today | Target | How it is measured |
|---|---|---|---|
| 완료율 | 재료 없음 | 95% | 건별 집계 (AI 제안, PM 미확인) [go-to-market/gtm-questions.md Q13] |

## Full Scope Vision
### Product Vision Statement
모든 안전 서류가 전자로 남는다. [discovery-document.md · Positioning]

## MVP Scope
### Features In Scope
| Feature | Description | Why it cannot wait |
|---|---|---|
| 발송 | 대상 지정 후 발송 | 첫 사용 장면 [discovery-document.md · Launch Plan] |
### Features Out of Scope
| Feature | Why it can wait | Target phase |
|---|---|---|
| HR 연동 | 파일 업로드로 시작 | 2단계 [discovery-document.md · Launch Plan] |
### Assumptions and Accepted Limits
- **Accepted limit**: 검증은 사내 동료가 역할을 대신했다. [prototype/validation-results.md]

## Risks and Dependencies
### Key Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 법무 불인정 | Medium | High | 범위 재설계 [discovery-document.md · Build Decision] |
### Open Questions
{open_table}"""


#: An open decision that cites a source path from the included originals (B).
_OPEN = _vision("""| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |
|---|---|---|---|---|---|
| O-01 | 상담 이력의 보존 기간은? | PM | 빌드 전 | — | use-case-intake/use-cases.md |
""")


def _step_of(prompt: str) -> str:
    if "Write the options" in prompt:
        return "options"
    if f"## Write {package.VISION}" in prompt:
        return "vision"
    raise AssertionError("prompt names no step")


def _writer(calls: list | None = None, *, fail_at: str | None = None, error=None,
            doc: str = _OPEN, options: str = '{"questions": []}'):
    """A fake model that returns each step's result. Fails at the `fail_at` step."""
    async def call(prompt, progress=None):
        step = _step_of(prompt)
        if calls is not None:
            calls.append((step, prompt))
        if step == fail_at:
            raise error or RuntimeError("AccessDenied: arn:aws:iam::123:role/x")
        return options if step == "options" else "머리말은 버려진다.\n" + doc
    return call


def _workspace_reader(files):
    async def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]
    return read


def _run(coro):
    return asyncio.run(coro)


async def _generate(s3, files, call, *, resume=False, paths=B):
    state = assess(paths, {})
    started = await package.start(s3, state, resume=resume)
    return await package.run(s3, read=_workspace_reader(files), paths=paths, readiness=state,
                             language="ko", call=call, started=started)


def _flat(text: str) -> str:
    """The prompt with line breaks removed — so phrase lookups do not depend on where lines wrap."""
    return " ".join(text.split())


def _facts(prompt: str) -> dict:
    return json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])


# ---- sources and prompt ----

def test_sources_skip_build_instructions_design_and_survey_forms_and_lead_with_the_document():
    paths = [D + "prototype/build-instructions.md", D + "prototype/design-context.md",
             D + "prototype/validation-questionnaire.md", D + "prototype/prototype-spec.md",
             D + "envision/pain-point-questions.md", D + "discovery-document.md",
             "aiplc-docs/audit.md", "prototype/src/app.ts"]
    assert package.select_sources(paths) == [
        D + "discovery-document.md", D + "prototype/prototype-spec.md",
        D + "envision/pain-point-questions.md"]


def test_the_archive_keeps_the_document_and_its_evidence_but_not_process_files():
    """Observed (harness trial ws-g): the side that received only the originals carried the build
    instructions' tech stack over as the production stack.
    Observed (ws-g instance package): 15 of the 26 originals were question files — the answers are in the decision ledger."""
    paths = [D + "prototype/build-instructions.md", D + "prototype/design-context.md",
             D + "prototype/validation-questionnaire.md", D + "discovery-document.md",
             D + "envision/pain-point-questions.md", D + "prototype/build-decision-questions.md",
             D + "prototype/validation-results.md", D + "solution-analysis/identified-solutions.md",
             "aiplc-docs/audit.md"]
    assert package.archive_originals(paths) == [
        D + "discovery-document.md", D + "prototype/validation-results.md",
        D + "solution-analysis/identified-solutions.md"]


def test_the_prompt_asks_only_for_what_the_files_do_not_give():
    path = D + "use-case-intake/use-case-intake-questions.md"
    state = assess(B + [path], {path: QUESTIONS})
    record = supplement.Supplement(
        answers={"problem.evidence": supplement.Answer(text="인터뷰 5명"),
                 "assumptions.failure_reasons": supplement.Answer(unknown=True)},
        confirmed={path + "#1": "t"})
    prompt = package.build_prompt(language="ko", readiness=state, record=record,
                                  sources={D + "use-case-intake/use-cases.md": "UC1 고객 문의"})
    assert "<<<FILE aiplc-docs/discovery/use-case-intake/use-cases.md>>>" in prompt
    assert "UC1 고객 문의" in prompt and "Write in Korean" in prompt
    assert "cite them instead of copying" in _flat(prompt)
    assert "## Features Out of Scope" in prompt and "### Open Questions" in prompt
    assert "| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |" in prompt
    assert '"빌드 전"' in prompt and '"빌드 중"' in prompt and '"PM(외부 확인)"' in prompt
    assert "At most about 16,000 characters" in prompt
    facts = _facts(prompt)
    assert facts["PM answers given at handoff"] == [
        {"topic": package._SUPPLEMENT_TOPICS["problem.evidence"], "answer": "인터뷰 5명"}]
    assert facts["topics the PM said they cannot answer yet"] == [
        package._SUPPLEMENT_TOPICS["assumptions.failure_reasons"]]
    assert "confirmed_by_pm" not in prompt and "handoff_answers" not in prompt


def test_english_projects_get_english_labels():
    prompt = package.build_prompt(language="en", readiness=assess(B, {}),
                                  record=supplement.Supplement(), sources={})
    assert "| ID | Question | Who answers | When | Until answered | Source |" in prompt
    assert "Write in English" in prompt


def test_clean_output_drops_a_preface_and_refuses_an_empty_document():
    assert package.clean_output("네, 작성했습니다.\n\n# 제목\n본문\n") == "# 제목\n본문\n"
    assert package.clean_output("제목 없는 본문\n") == "제목 없는 본문\n"
    with pytest.raises(ValueError):
        package.clean_output("   \n ")


# ---- checks ----

def _kinds(findings, kind):
    return [(f.line, f.term) for f in findings if f.kind == kind]


def test_lint_finds_technology_vague_words_and_internal_notation():
    doc = "\n".join([
        "상담 이력을 PostgreSQL에 저장하는가?",
        "Bedrock 호출 없이 고정 문장으로 답했다",
        "모델은 us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "포트 3000에서 띄운다",
        "리액트로 화면을 만든다",
        "고객이 빠르게 답을 받는가?",
        "The flow is user-friendly",
        "H3 가설의 통과 기준은? confirmed_by_pm=true",
    ])
    found = {(f.line, f.kind, f.term) for f in package.lint(doc)}
    assert (1, "tech", "PostgreSQL") in found
    assert (2, "tech", "Bedrock") in found
    assert any(line == 3 and "anthropic" in term for line, _, term in found)
    assert (4, "tech", "포트 3000") in found
    assert (5, "tech", "리액트") in found
    assert (6, "vague", "빠르게") in found
    assert (7, "vague", "user-friendly") in found
    assert (8, "internal", "H3") in found and (8, "internal", "confirmed_by_pm=true") in found


def test_lint_leaves_ordinary_product_language_and_citations_alone():
    doc = "\n".join([
        "# 열린 결정",
        "생성 10/08 14:20 · Express 배송 고객",
        "매장 300곳의 상담원이 질문 후 3초 안에 첫 답을 보는가?",
        "근거: go-to-market/gtm-questions.md Q14 · discovery-document.md",
    ])
    assert [f for f in package.lint(doc) if f.kind != "structure"] == []


def test_a_missing_vision_section_is_flagged():
    """The guide assigns each section to the stage that uses it (Features In/Out → Requirements
    Analysis, Code Generation). Without the section, that stage has nothing to read."""
    assert _kinds(package.lint(_OPEN), "structure") == []
    cut = _OPEN.replace("### Features Out of Scope", "### Later")
    assert [term for _, term in _kinds(package.lint(cut), "structure")] == ["Features Out of Scope"]


_TRIAGED = _vision("""| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |
|---|---|---|---|---|---|
| O-01 | 증빙 기록의 보존 기간은 얼마인가? | PM(외부 확인) | 빌드 전 | — | discovery-document.md · Build Decision |
| O-02 | 개인 링크의 유효 기간은? | PM | **빌드 전** | — | envision/prfaq-clarifying-questions.md Q3 |
| O-03 | 지원 외국어의 최종 목록은? | PM | 빌드 중 | 목록을 바꿀 수 있게 두고 영어·베트남어로 시작 | prototype/prototype-spec.md |
| O-04 | 파일럿 개발 규모는? | 개발팀 | 빌드 전 | — | go-to-market/gtm-questions.md Q9 |
| O-05 | 만족도 버튼의 형태는? | PM | 빌드 중 | — | go-to-market/gtm-questions.md Q15 |
| O-06 | 확인 문제의 통과 기준은? | PM | 빌드 전 | — | legal/somewhere.md |
| O-07 | 지점당 단말 대수는? | PM | 나중에 | — | discovery-document.md |
""")
_SRC = [D + "discovery-document.md", D + "envision/prfaq-clarifying-questions.md",
        D + "prototype/prototype-spec.md", D + "go-to-market/gtm-questions.md"]


def test_open_decisions_split_into_before_and_during_build_and_external_checks():
    found = package.open_questions(_TRIAGED, "ko")
    assert [(q.id, q.blocking, q.external) for q in found] == [
        ("O-01", True, True), ("O-02", True, False), ("O-03", False, False),
        ("O-05", False, False), ("O-06", True, False),
        ("O-07", True, False)]  # an unknown timing is asked as before-build · the dev team's items are not asked
    assert found[2].until == "목록을 바꿀 수 있게 두고 영어·베트남어로 시작"
    assert found[0].until == "" and found[3].until == ""


def test_a_triage_without_grounds_is_flagged():
    """Each row cites a location in the included originals, and during-build rows state how to build in
    the meantime. Timing is one of the two."""
    found = package.lint(_TRIAGED, sources=_SRC)
    assert [term for _, term in _kinds(found, "triage")] == ["O-05", "O-06", "나중에"]
    # A table missing the timing/source columns is flagged on its header row.
    old = _vision("| ID | 질문 | 답할 사람 |\n|---|---|---|\n| O-01 | 보존 기간 | PM |\n")
    assert [term for _, term in _kinds(package.lint(old), "triage")] == ["시점", "근거"]


def test_the_previous_prd_is_read_from_its_ninth_section():
    """In the previous structure's PRD, section 9 is the open questions. A heading without the number
    is found too (observed ws-g)."""
    prd = ("# P\n\n## 5. 요구사항\n| 1 | x | PM |\n\n## 열린 질문\n\n"
           "| # | 질문 | 답할 사람 |\n|---|---|---|\n| 1 | 보존 기간(R-08) | PM |\n"
           "| 2 | 저장 용량 | 개발팀 |\n")
    assert [(q.id, q.question) for q in package.open_questions(prd, "ko", kind="prd")] == [
        ("1", "보존 기간(R-08)")]
    assert package.previous_rows(prd, "ko", kind="prd") == [
        {"질문": "보존 기간(R-08)", "답할 사람": "PM", "시점": ""},
        {"질문": "저장 용량", "답할 사람": "개발팀", "시점": ""}]


# ---- decision ledger and step-by-step generation ----

def test_steps_write_the_open_decisions_then_the_ledger_and_readme():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls)))
    assert done.status == "ready" and done.origin == "B"
    # If the open decisions include a before-build PM decision, options are generated.
    assert [step for step, _ in calls] == ["vision", "options"]
    assert [s.status for s in done.steps] == ["done", "done"]
    assert done.files == ["README.md", "start-prompt.md", "vision.md", "decisions.md"]
    assert set(done.sources) == set(package.select_sources(B))
    assert s3.blobs[package.PACKAGE_PREFIX + "vision.md"].startswith("# 샘플몰 안전ON — Vision")
    prompt = s3.blobs[package.PACKAGE_PREFIX + "start-prompt.md"]
    assert "/aidlc ./project-handoff/vision.md에 정리된 제품을 만들어 주세요." in prompt and "$aidlc" in prompt
    assert "./project-handoff/discovery/에 있으니 discovery-document.md부터" in prompt
    assert s3.blobs[package.PACKAGE_PREFIX + "decisions.md"].startswith("# 결정 장부")
    readme = s3.blobs[package.PACKAGE_PREFIX + "README.md"]
    assert readme.startswith("# 개발 인계 패키지")
    assert readme.index("start-prompt.md") < readme.index("1. `vision.md`") < readme.index("2. `decisions.md`")
    assert all("build-instructions" not in prompt for _, prompt in calls)
    assert done.findings == []


@pytest.mark.parametrize("error, reason", [
    (RuntimeError("AccessDenied: arn:aws:iam::123:role/x"), "generation_failed"),
    (ValueError("empty document"), "malformed_output"),
])
def test_a_failed_step_is_reported_without_the_raw_error(error, reason):
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    failed = _run(_generate(s3, files, _writer(fail_at="vision", error=error)))
    assert failed.status == "failed" and failed.error == reason
    assert [(s.status, s.error) for s in failed.steps] == [("failed", reason), ("pending", None)]
    assert "arn:aws" not in s3.blobs[package.MANIFEST_KEY]


def test_a_failed_options_step_leaves_the_package_complete():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    done = _run(_generate(s3, files, _writer(fail_at="options")))
    assert done.status == "ready" and done.error is None
    assert [(s.status, s.error) for s in done.steps][-1] == ("failed", "generation_failed")
    assert _run(package.load_options(s3)) == {}


async def _interrupted_after_open_async(s3, files):
    """Builds the state of a restart cutting off the options step midway."""
    started = await package.start(s3, assess(B, {}))
    steps = package.fresh_steps()
    steps[0] = steps[0].model_copy(update={"status": "done"})
    steps[1] = steps[1].model_copy(update={"status": "running"})
    await s3.put(package.PACKAGE_PREFIX + package.VISION, _OPEN)
    sources = await package.read_sources(_workspace_reader(files), package.select_sources(B))
    record = await supplement.load(s3)
    await s3.put(package.MANIFEST_KEY, started.model_copy(update={
        "steps": steps, "sources": {p: package.digest(t) for p, t in sources.items()},
        "supplement": package.digest(record.model_dump_json())}).model_dump_json())


def _interrupted_after_open(s3, files):
    _run(_interrupted_after_open_async(s3, files))


def test_view_reports_an_interrupted_run_as_resumable_and_resuming_runs_only_the_rest():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _interrupted_after_open(s3, files)
    v = _run(package.view(s3, read=_workspace_reader(files), paths=B, running=False))
    assert v.manifest.status == "interrupted" and v.resumable
    assert [(s.status, s.error) for s in v.manifest.steps] == [
        ("done", None), ("failed", "interrupted")]
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls), resume=True))
    assert done.status == "ready" and [step for step, _ in calls] == ["options"]
    # The resumed options step also receives the stored open decisions.
    assert "O-01 | 상담 이력의 보존 기간은?" in calls[0][1]


def test_resuming_after_the_sources_changed_starts_over():
    """Documents written from changed originals and documents written from old originals must not mix in one package."""
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _interrupted_after_open(s3, files)
    files[D + "use-case-intake/use-cases.md"] = "# 바뀜\n"
    v = _run(package.view(s3, read=_workspace_reader(files), paths=B, running=False))
    assert not v.resumable and v.stale
    calls: list = []
    _run(_generate(s3, files, _writer(calls), resume=True))
    assert [step for step, _ in calls] == ["vision", "options"]


def test_a_running_step_reports_thinking_then_received_characters(monkeypatch):
    monkeypatch.setattr(package, "_PROGRESS_EVERY_S", 0.01)
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    snapshots: list = []
    put = s3.put

    async def recording_put(key, content):
        if key == package.MANIFEST_KEY:
            snapshots.append(json.loads(content))
        return await put(key, content)
    s3.put = recording_put

    async def call(prompt, progress):
        if _step_of(prompt) == "vision":
            progress(0, True)
            await asyncio.sleep(0.05)
            progress(500, False)
            await asyncio.sleep(0.05)
            return _OPEN
        return '{"questions": []}'

    _run(_generate(s3, files, call))
    first = [m["steps"][0] for m in snapshots if m["steps"] and m["steps"][0]["status"] == "running"]
    assert any(s["thinking"] and s["chars"] == 0 for s in first)
    assert any(s["chars"] == 500 and not s["thinking"] for s in first)
    # A late-arriving progress record does not overwrite "done".
    assert snapshots[-1]["status"] == "ready"
    assert [s["status"] for s in snapshots[-1]["steps"]] == ["done", "done"]


def test_view_reports_stale_and_supplement_changes_after_ready():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}

    async def go():
        await _generate(s3, files, _writer())
        fresh = await package.view(s3, read=_workspace_reader(files), paths=B, running=False)
        assert fresh.stale == [] and not fresh.supplement_changed
        assert set(fresh.files) == set(package.FILES)
        files[D + "use-case-intake/use-cases.md"] = "# 바뀜\n"
        added = D + "product-strategy/strategy-questions.md"
        files[added] = "# 새 원본\n"
        await supplement.save(s3, supplement.Supplement(
            answers={"goals.success": supplement.Answer(text="처리 시간 절반")}))
        later = await package.view(s3, read=_workspace_reader(files), paths=B + [added],
                                   running=False)
        assert later.stale == sorted([D + "use-case-intake/use-cases.md", added])
        assert later.supplement_changed
    _run(go())


def test_an_old_manifest_with_prd_checks_is_still_readable():
    """If a manifest from the previous structure (check kinds acceptance, ai_goal) cannot be read, the package looks as if it does not exist."""
    s3 = FakeS3Store()
    _run(s3.put(package.MANIFEST_KEY, json.dumps({
        "status": "ready", "started_at": "t", "files": ["PRD.md"], "steps": [],
        "findings": [{"file": "PRD.md", "line": 3, "term": "G-01", "kind": "ai_goal"}]})))
    assert _run(package.load_manifest(s3)).findings[0].kind == "ai_goal"


def test_the_package_lives_outside_the_agent_workspace():
    from aipds.runner import AgentRunner
    from aipds.workspace_sync import is_synced_key
    for name in package.FILES + (package.OPTIONS, "manifest.json"):
        key = package.PACKAGE_PREFIX + name
        assert not is_synced_key(key)
        assert not key.startswith(AgentRunner._RESTORE_PREFIXES)


def _read_timeout():
    from botocore.exceptions import ReadTimeoutError
    return ReadTimeoutError(endpoint_url="https://bedrock-runtime.ap-northeast-2.amazonaws.com")


def _chain(outer: Exception, cause: Exception) -> Exception:
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize("make", [
    _read_timeout,
    # Observed shape: a stream read timeout comes up wrapped as the cause of another exception.
    lambda: _chain(RuntimeError("stream failed"), _read_timeout()),
    lambda: TimeoutError("The read operation timed out"),
])
def test_a_wrapped_read_timeout_is_reported_as_a_timeout(make):
    s3 = FakeS3Store()
    failed = _run(_generate(s3, {}, _writer(fail_at="vision", error=make())))
    assert failed.error == "timeout"
    assert failed.steps[0].error == "timeout"


def test_hitting_the_output_cap_is_reported_as_too_long():
    class MaxTokensReachedException(Exception):
        pass
    s3 = FakeS3Store()
    failed = _run(_generate(s3, {}, _writer(fail_at="vision", error=_chain(
        RuntimeError("agent loop"), MaxTokensReachedException("Model stopped generating")))))
    assert failed.error == "too_long" and failed.steps[0].error == "too_long"


def test_the_handoff_writer_waits_long_and_reports_stream_progress(monkeypatch):
    """With the Strands default read limit (120 seconds), the first output of a large project cannot be
    awaited (observed). Progress comes from the Strands callback's body chunks (data) and thinking (reasoningText)."""
    import sys
    import types

    made = []

    class FakeModel:
        def __init__(self, **kwargs):
            made.append(kwargs)

    class FakeAgent:
        def __init__(self, **kwargs):
            self.callback = kwargs.get("callback_handler")

        async def invoke_async(self, prompt):
            if self.callback:
                # Opus 5.5 thinking arrives without content by default (display "omitted") — only the marker.
                self.callback(reasoningText="", delta={}, reasoning=True)
                self.callback(data="안녕")
                self.callback(data="하세요")
            return "ok"

    strands = types.ModuleType("strands")
    strands.Agent = FakeAgent
    models = types.ModuleType("strands.models")
    models.BedrockModel = FakeModel
    monkeypatch.setitem(sys.modules, "strands", strands)
    monkeypatch.setitem(sys.modules, "strands.models", models)
    monkeypatch.setattr(app_module, "project_model", lambda pid: "model-x")

    events = []
    asyncio.run(app_module.handoff_writer_factory("p")("hi", lambda n, t: events.append((n, t))))
    asyncio.run(app_module.questionnaire_agent_factory("p")("hi"))
    handoff, questionnaire = made
    assert handoff["max_tokens"] == 64000
    assert handoff["boto_client_config"].read_timeout == package.CALL_TIMEOUT_S
    assert "boto_client_config" not in questionnaire
    assert events == [(0, True), (2, False), (3, False)]


# ---- options for supplementary questions ----

def _asked():
    return [q for q in package.open_questions(_TRIAGED, "ko") if q.blocking and not q.external]


def test_options_keep_only_grounded_unmarked_choices_in_a_neutral_order():
    reply = "```json\n" + json.dumps({"questions": [
        {"id": "O-02", "options": [
            {"text": "7일", "source": "go-to-market/gtm-questions.md Q4"},
            {"text": "30일", "source": "discovery/envision/prfaq-clarifying-questions.md Q2"},
            {"text": "1년", "source": "legal/somewhere.md"},          # not an included original
            {"text": "30일", "source": "envision/prfaq-clarifying-questions.md"}]},  # duplicate
        {"id": "O-06", "options": [
            {"text": "2개 (추천)", "source": "envision/prfaq-clarifying-questions.md Q3"},
            {"text": "3개", "source": "envision/prfaq-clarifying-questions.md Q3"}]},
        {"id": "O-07", "options": [
            {"text": "2대", "source": "discovery-document.md"}]},
        {"id": "O-99", "options": [
            {"text": "x", "source": "discovery-document.md"},
            {"text": "y", "source": "discovery-document.md"}]},
    ]}, ensure_ascii=False) + "\n```"
    options = package.parse_options(reply, _asked(), _SRC)
    by_id = {q.id: options.get(supplement.open_key(q.question)) for q in _asked()}
    assert sorted(o.text for o in by_id["O-02"]) == ["30일", "7일"]
    assert by_id["O-06"] is None   # recommendation marker → free-text
    assert by_id["O-07"] is None   # fewer than two grounded options → free-text
    assert len(options) == 1       # unknown ids are dropped
    # The order is determined by the text, not by the order the model gave.
    flipped = reply.replace('"7일"', '"__"').replace('"30일", "source": "discovery',
                                                    '"7일", "source": "discovery').replace('"__"', '"30일"')
    again = package.parse_options(flipped, _asked(), _SRC)
    key = supplement.open_key(_asked()[0].question)
    assert [o.text for o in again[key]] == [o.text for o in by_id["O-02"]]
    with pytest.raises(ValueError):
        package.parse_options("보기를 만들 수 없습니다.", _asked(), _SRC)


def test_an_option_that_points_at_another_option_is_dropped():
    """Observed (ws-g instance generation): "위 전체 항목에 … 추가" ("add … to all of the above") showed up
    first after reordering. A "위" inside a word, as in "범위의" or "위험성평가", is left alone."""
    src = "envision/prfaq-clarifying-questions.md Q7"
    texts = ["서명 시각과 단말만 남긴다.", "위 전체 항목에 영상 시청 기록을 추가한다.",
             "위험성평가 참여 기록까지 남긴다.", "법무가 정한 범위의 항목만 남긴다.",
             "All of the above, plus video logs."]
    reply = json.dumps({"questions": [{"id": "O-02", "options": [
        {"text": t, "source": src} for t in texts]}]}, ensure_ascii=False)
    kept = package.parse_options(reply, _asked(), _SRC)[supplement.open_key(_asked()[0].question)]
    assert sorted(o.text for o in kept) == sorted([texts[0], texts[2], texts[3]])


def test_the_options_step_asks_only_pm_decisions_before_build_that_are_not_external():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B + _SRC}
    reply = json.dumps({"questions": [{"id": "O-02", "options": [
        {"text": "7일", "source": "envision/prfaq-clarifying-questions.md Q2"},
        {"text": "30일", "source": "go-to-market/gtm-questions.md Q4"}]}]}, ensure_ascii=False)
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls, doc=_TRIAGED, options=reply), paths=B + _SRC))
    assert done.status == "ready" and done.steps[-1].status == "done"
    prompt = dict(calls)["options"]
    assert "- O-02:" in prompt and "- O-06:" in prompt
    # External checks · during-build · dev-team items get no options.
    assert "O-01:" not in prompt and "O-03:" not in prompt and "O-04:" not in prompt
    saved = _run(package.load_options(s3))
    assert sorted(o.text for o in saved[supplement.open_key("개인 링크의 유효 기간은?")]) == [
        "30일", "7일"]


# ---- open decisions from the previous version ----

def test_a_question_being_checked_goes_back_as_still_open():
    q = "전자 기록이 종이와 같은 증빙 효력을 갖는가?"
    record = supplement.Supplement(open_answers={supplement.open_key(q): supplement.OpenAnswer(
        question=q, pending_with="법무팀")})
    prompt = package.build_prompt(language="ko", readiness=assess(B, {}), record=record,
                                  sources={})
    facts = _facts(prompt)
    assert facts["open questions being checked with someone else"] == [
        {"question": q, "checking with": "법무팀"}]
    assert facts["PM answers given at handoff"] == []   # being checked is not an answer
    assert '"확인 중: <who>"' in prompt


def test_answers_to_open_decisions_go_back_into_the_next_version():
    known = supplement.OpenAnswer(question="자동 재알림 규칙", text="기한 2일 전, 1회, 문자")
    unknown = supplement.OpenAnswer(question="개인 링크 유효기간", unknown=True)
    record = supplement.Supplement(open_answers={
        supplement.open_key(known.question): known, supplement.open_key(unknown.question): unknown})
    facts = _facts(package.build_prompt(language="ko", readiness=assess(B, {}), record=record,
                                        sources={}))
    assert {"question": "자동 재알림 규칙", "answer": "기한 2일 전, 1회, 문자"} in facts[
        "PM answers given at handoff"]
    assert facts["open questions the PM said they cannot answer yet"] == ["개인 링크 유효기간"]


_LEGACY_PRD = """# 샘플몰 안전ON PRD

## 9. 열린 질문

| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 |
|---|---|---|---|---|
| O-01 | 증빙 기록의 보존 기간은 얼마인가? (R-08, C-05) | PM | 빌드 전 | — |
| O-02 | 개인 링크의 유효 기간은? (R-04) | PM | 빌드 전 | — |
| O-03 | 만족도 버튼의 형태는? (R-14) | PM | 빌드 중 | 바꿀 수 있게 둔다 |
| O-04 | 파일럿 개발 규모는? (C-08) | 개발팀 | 빌드 중 | 먼저 착수 |
"""


def test_the_first_version_carries_over_the_old_prd_questions_and_flags_a_lost_one():
    """Observed (ws-g): "개인 링크의 유효 기간" (personal link validity period) from the earlier PRD vanished
    without any notice in the next generation. Section 9 of the previous structure's PRD is the
    previous version of the first open-decisions document."""
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B + _SRC}
    _run(s3.put(package.PACKAGE_PREFIX + "PRD.md", _LEGACY_PRD))
    # One was answered in the handoff tab — that question dropping out is expected. A different number is still the same question.
    answered = "만족도 버튼의 형태는? (R-15)"
    _run(supplement.save(s3, supplement.Supplement(open_answers={
        supplement.open_key(answered): supplement.OpenAnswer(question=answered, text="5점 척도")})))
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls, doc=_TRIAGED), paths=B + _SRC))
    previous = _facts(dict(calls)["vision"])["open decisions in the previous version"]
    assert [r["질문"] for r in previous] == [
        "증빙 기록의 보존 기간은 얼마인가? (R-08, C-05)", "개인 링크의 유효 기간은? (R-04)",
        "만족도 버튼의 형태는? (R-14)", "파일럿 개발 규모는? (C-08)"]
    assert previous[2]["시점"] == "빌드 중" and previous[3]["답할 사람"] == "개발팀"
    assert "Carry every one of them over" in _flat(dict(calls)["vision"])
    assert done.carried == ["증빙 기록의 보존 기간은 얼마인가? (R-08, C-05)",
                            "개인 링크의 유효 기간은? (R-04)", "만족도 버튼의 형태는? (R-14)"]
    # Retention period and link validity are in the new version (related IDs are ignored), and the satisfaction button was answered.
    assert _kinds(done.findings, "dropped") == []

    # If link validity disappears in the next version, flag it. This time the previous version is the open-decisions document.
    lost = _TRIAGED.replace("| O-02 | 개인 링크의 유효 기간은? | PM | **빌드 전** | — | "
                            "envision/prfaq-clarifying-questions.md Q3 |\n", "")
    calls = []
    again = _run(_generate(s3, files, _writer(calls, doc=lost), paths=B + _SRC))
    assert [term for _, term in _kinds(again.findings, "dropped")] == ["개인 링크의 유효 기간은?"]


# ---- routes ----

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()
    monkeypatch.setattr(app_module, "s3_store_factory", lambda project_id: s3)

    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    writer = {"call": _writer()}
    monkeypatch.setattr(app_module, "handoff_writer_factory", lambda pid: writer["call"])
    with TestClient(app) as client:
        yield client, s3, writer


def _seed(client, pid, files):
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)

    async def seed():
        for path, content in files.items():
            await ws.runner.write_file(path, content)
    client.portal.call(seed)


def _wait_settled(client, pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/projects/{pid}/handoff/package").json()
        if body["manifest"] and body["manifest"]["status"] != "generating":
            return body
        time.sleep(0.02)
    raise AssertionError("package never finished")


def test_route_generates_views_and_downloads(env):
    client, s3, _ = env
    files = ({p: f"# {p}\n" for p in B}
             | {D + "prototype/build-instructions.md": "Node.js + PostgreSQL\n",
                D + "envision/pain-point-questions.md": "# Q\n",
                D + "prototype/design-context.md": "# 색\n",
                "prototype/src/index.ts": "export {}\n"})
    _seed(client, "pkg-b", files)
    client.portal.call(s3.put_bytes, "uploads/ab12cd34/동의서.pdf", b"%PDF-1.7 bytes")

    r = client.post("/projects/pkg-b/handoff/package")
    assert r.status_code == 202 and r.json()["status"] == "generating"
    assert [s["status"] for s in r.json()["steps"]] == ["pending"] * len(package.STEPS)
    body = _wait_settled(client, "pkg-b")
    assert body["manifest"]["status"] == "ready"
    assert body["files"]["vision.md"].startswith("# 샘플몰 안전ON")
    assert body["stale"] == []

    r = client.get("/projects/pkg-b/handoff/package/archive")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="pkg-b-handoff.zip"'
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    # Every entry sits under one folder named like the zip — whatever tool extracts it, the start prompt's
    # paths are right, and extracting into the project root does not let the package README overwrite the project's README.
    assert {n.split("/", 1)[0] for n in zf.namelist()} == {"pkg-b-handoff"}
    prompt = zf.read("pkg-b-handoff/start-prompt.md").decode()
    assert "/aidlc ./pkg-b-handoff/vision.md에 정리된 제품을 만들어 주세요." in prompt
    assert "./pkg-b-handoff/decisions.md" in prompt
    # The start prompt shown on screen points to the same folder.
    assert "./pkg-b-handoff/vision.md" in body["files"]["start-prompt.md"]
    names = [n.split("/", 1)[1] for n in zf.namelist()]
    assert {"README.md", "start-prompt.md", "vision.md", "decisions.md"} <= set(names)
    assert "discovery/use-case-intake/use-cases.md" in names
    assert zf.read("pkg-b-handoff/uploads/ab12cd34/동의서.pdf") == b"%PDF-1.7 bytes"   # bytes unchanged
    # Question files, build instructions, design context, prototype source, the audit log, options, and previous-structure documents are excluded.
    assert not any(n.endswith(("-questions.md", "build-instructions.md", "design-context.md",
                               "audit.md")) or n.startswith("prototype/") for n in names)
    assert package.OPTIONS not in names and "PRD.md" not in names


def test_route_resumes_from_the_interrupted_step(env):
    client, s3, writer = env
    files = {p: f"# {p}\n" for p in B}
    _seed(client, "pkg-resume", files)
    client.portal.call(_interrupted_after_open_async, s3, files)
    body = client.get("/projects/pkg-resume/handoff/package").json()
    assert body["manifest"]["status"] == "interrupted" and body["resumable"]

    calls: list = []
    writer["call"] = _writer(calls)
    r = client.post("/projects/pkg-resume/handoff/package", json={"resume": True})
    assert [s["status"] for s in r.json()["steps"]] == ["done", "pending"]
    assert _wait_settled(client, "pkg-resume")["manifest"]["status"] == "ready"
    assert [step for step, _ in calls] == ["options"]


def test_route_refuses_to_generate_without_a_spec(env):
    client, _, _ = env
    _seed(client, "pkg-empty", {D + "envision/pain-point-analysis.md": "# x\n"})
    r = client.post("/projects/pkg-empty/handoff/package")
    assert r.status_code == 409
    assert r.json()["detail"]["blockers"] == ["no_spec"]


def test_route_archive_is_404_before_a_package_exists(env):
    client, _, _ = env
    _seed(client, "pkg-none", {p: "# x\n" for p in B})
    assert client.get("/projects/pkg-none/handoff/package/archive").status_code == 404
    assert client.get("/projects/pkg-none/handoff/package").json()["manifest"] is None


def test_route_supplement_asks_the_open_pm_decisions_with_options(env):
    client, _, writer = env
    _seed(client, "pkg-open", {p: f"# {p}\n" for p in B + _SRC})
    assert client.get("/projects/pkg-open/handoff/supplement").json()["open_questions"] == []

    reply = json.dumps({"questions": [{"id": "O-02", "options": [
        {"text": "7일", "source": "envision/prfaq-clarifying-questions.md Q2"},
        {"text": "30일", "source": "go-to-market/gtm-questions.md Q4"}]}]}, ensure_ascii=False)
    writer["call"] = _writer(doc=_TRIAGED, options=reply)
    client.post("/projects/pkg-open/handoff/package")
    assert _wait_settled(client, "pkg-open")["manifest"]["status"] == "ready"

    body = client.get("/projects/pkg-open/handoff/supplement").json()
    assert body["has_package"]
    assert [(q["id"], q["external"], len(q["options"])) for q in body["open_questions"]] == [
        ("O-01", True, 0), ("O-02", False, 2), ("O-06", False, 0), ("O-07", False, 0)]
    assert [(q["id"], bool(q["until"])) for q in body["open_deferred"]] == [
        ("O-03", True), ("O-05", False)]

    asked = body["open_questions"]
    r = client.put("/projects/pkg-open/handoff/supplement", json={"open_answers": {
        asked[1]["key"]: {"question": asked[1]["question"], "text": "7일"}}})
    assert r.status_code == 200
    assert r.json()["open_questions"][1]["answer"]["text"] == "7일"
    # Answering makes the package stale — the next generation uses that answer.
    assert client.get("/projects/pkg-open/handoff/package").json()["supplement_changed"] is True


# ---- reset ----

def test_route_reset_wipes_the_package_the_old_prd_and_the_answers_but_not_discovery(env):
    """Regenerating carries over the previous version and the supplementary answers. Reset wipes both too and returns to the initial state."""
    client, s3, _ = env
    files = {p: f"# {p}\n" for p in B}
    _seed(client, "pkg-reset", files)
    client.post("/projects/pkg-reset/handoff/package")
    assert _wait_settled(client, "pkg-reset")["manifest"]["status"] == "ready"
    client.portal.call(s3.put, package.PACKAGE_PREFIX + "PRD.md", _LEGACY_PRD)
    q = "상담 이력의 보존 기간은?"
    r = client.put("/projects/pkg-reset/handoff/supplement", json={"open_answers": {
        supplement.open_key(q): {"question": q, "text": "3년"}}})
    assert r.status_code == 200

    r = client.delete("/projects/pkg-reset/handoff")
    assert r.status_code == 200 and r.json()["deleted"] >= 6
    assert not [k for k in s3.blobs if k.startswith(package.HANDOFF_PREFIX)]
    assert client.get("/projects/pkg-reset/handoff/package").json()["manifest"] is None
    body = client.get("/projects/pkg-reset/handoff/supplement").json()
    assert body["has_package"] is False and body["open_answered"] == []
    # Discovery artifacts are untouched.
    ws = registry.get("pkg-reset")
    assert client.portal.call(ws.runner.read_file, B[0]) == files[B[0]]

    # The next generation has no previous version to carry over.
    calls: list = []
    env[2]["call"] = _writer(calls)
    client.post("/projects/pkg-reset/handoff/package")
    _wait_settled(client, "pkg-reset")
    assert "open decisions in the previous version" not in _facts(dict(calls)["vision"])


def test_route_reset_is_refused_while_generating(env, monkeypatch):
    client, s3, _ = env
    _seed(client, "pkg-busy", {p: f"# {p}\n" for p in B})
    client.portal.call(s3.put, package.PACKAGE_PREFIX + "vision.md", _OPEN)
    monkeypatch.setattr(package.jobs, "running", lambda pid: True)
    assert client.delete("/projects/pkg-busy/handoff").status_code == 409
    assert package.PACKAGE_PREFIX + "vision.md" in s3.blobs


def test_the_zip_and_its_folder_share_one_name():
    """The zip name, the folder inside it, and the start prompt's paths come from one function. An imported
    project's id may not follow the creation screen's rule (Latin letters, digits, _, -)."""
    assert package.archive_folder("sample-mall") == "sample-mall-handoff"
    assert package.archive_folder("안전 프로젝트") == "project-handoff"
    assert package.archive_folder("ws_a.v2 (copy)") == "ws_a.v2-copy-handoff"
    assert "./ws-x-handoff/vision.md" in package.start_prompt("en", package.archive_folder("ws-x"))


# ---- spec reconciliation: vision receives only the fact and raises it as an open question ----

def test_an_unreconciled_spec_reaches_the_vision_as_a_fact_and_a_rule():
    from aipds.handoff import package
    from aipds.handoff.readiness import assess
    d = "aiplc-docs/discovery/"
    history = d + "prototype/change-history.md"
    paths = [d + "prototype/prototype-spec.md", history]
    stale = assess(paths, {history: "# 수정 이력\n\n## 2026-10-13 16:27 UTC · gen 1\n\n검색 제거\n"})
    fresh = assess([d + "prototype/prototype-spec.md"], {})

    [proto] = stale.prototypes
    fact = package._reconcile_fact(proto)
    assert fact["spec reflects every change made after approval"] is False
    assert fact["change history"] == "prototype/change-history.md"
    assert package._reconcile_fact(fresh.prototypes[0]) == {}

    # The fact's name and the rule that reads it must use the same words for the model to connect them.
    prompt = package.build_prompt(language="ko", readiness=stale,
                                  record=supplement.Supplement(), sources={})
    assert '"spec reflects every change made after approval"' in prompt
    assert "does not reflect every change made after approval" in _flat(prompt)


def test_the_readme_names_spec_as_built_as_the_spec_of_record():
    from aipds.handoff import package
    for text in package._README.values():
        assert "spec-as-built.md" in text
        assert "change-history.md" in text
