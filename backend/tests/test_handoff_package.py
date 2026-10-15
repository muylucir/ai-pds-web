# backend/tests/test_handoff_package.py — handoff/package와 생성·조회·내려받기 라우트
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

#: 실은 원본(B)의 근거 경로를 대는 열린 결정.
_OPEN = """# 열린 결정

| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |
|---|---|---|---|---|---|
| O-01 | 상담 이력의 보존 기간은? | PM | 빌드 전 | — | use-case-intake/use-cases.md |
"""


def _step_of(prompt: str) -> str:
    if "Write the options" in prompt:
        return "options"
    if f"## Write {package.OPEN_DECISIONS}" in prompt:
        return "open"
    raise AssertionError("prompt names no step")


def _writer(calls: list | None = None, *, fail_at: str | None = None, error=None,
            doc: str = _OPEN, options: str = '{"questions": []}'):
    """단계마다 그 결과를 돌려주는 가짜 모델. `fail_at` 단계에서 실패한다."""
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


def _facts(prompt: str) -> dict:
    return json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])


# ---- 재료와 프롬프트 ----

def test_sources_skip_build_instructions_design_and_survey_forms_and_lead_with_the_document():
    paths = [D + "prototype/build-instructions.md", D + "prototype/design-context.md",
             D + "prototype/validation-questionnaire.md", D + "prototype/prototype-spec.md",
             D + "envision/pain-point-questions.md", D + "discovery-document.md",
             "aiplc-docs/audit.md", "prototype/src/app.ts"]
    assert package.select_sources(paths) == [
        D + "discovery-document.md", D + "prototype/prototype-spec.md",
        D + "envision/pain-point-questions.md"]


def test_the_archive_keeps_the_originals_but_not_the_prototype_build_instructions_or_design():
    """실측(하네스 시험 ws-g): 원본만 받은 쪽이 빌드 지시서의 기술 구성을 운영 구성으로 승계했다."""
    paths = [D + "prototype/build-instructions.md", D + "prototype/design-context.md",
             D + "prototype/validation-questionnaire.md", D + "discovery-document.md",
             "aiplc-docs/audit.md"]
    assert package.archive_originals(paths) == [
        D + "prototype/validation-questionnaire.md", D + "discovery-document.md"]


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
    assert "UC1 고객 문의" in prompt and "Write it in Korean" in prompt
    assert "do not restate them" in prompt
    assert "| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |" in prompt
    assert '"빌드 전"' in prompt and '"빌드 중"' in prompt and '"PM(외부 확인)"' in prompt
    assert "At most about 9,000 characters" in prompt
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
    assert "Write it in English" in prompt


def test_clean_output_drops_a_preface_and_refuses_an_empty_document():
    assert package.clean_output("네, 작성했습니다.\n\n# 제목\n본문\n") == "# 제목\n본문\n"
    assert package.clean_output("제목 없는 본문\n") == "제목 없는 본문\n"
    with pytest.raises(ValueError):
        package.clean_output("   \n ")


# ---- 검사 ----

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
    assert package.lint(doc) == []


_TRIAGED = """# 열린 결정

| ID | 질문 | 답할 사람 | 시점 | 답이 나올 때까지 | 근거 |
|---|---|---|---|---|---|
| O-01 | 증빙 기록의 보존 기간은 얼마인가? | PM(외부 확인) | 빌드 전 | — | discovery-document.md · Build Decision |
| O-02 | 개인 링크의 유효 기간은? | PM | **빌드 전** | — | envision/prfaq-clarifying-questions.md Q3 |
| O-03 | 지원 외국어의 최종 목록은? | PM | 빌드 중 | 목록을 바꿀 수 있게 두고 영어·베트남어로 시작 | prototype/prototype-spec.md |
| O-04 | 파일럿 개발 규모는? | 개발팀 | 빌드 전 | — | go-to-market/gtm-questions.md Q9 |
| O-05 | 만족도 버튼의 형태는? | PM | 빌드 중 | — | go-to-market/gtm-questions.md Q15 |
| O-06 | 확인 문제의 통과 기준은? | PM | 빌드 전 | — | legal/somewhere.md |
| O-07 | 지점당 단말 대수는? | PM | 나중에 | — | discovery-document.md |
"""
_SRC = [D + "discovery-document.md", D + "envision/prfaq-clarifying-questions.md",
        D + "prototype/prototype-spec.md", D + "go-to-market/gtm-questions.md"]


def test_open_decisions_split_into_before_and_during_build_and_external_checks():
    found = package.open_questions(_TRIAGED, "ko")
    assert [(q.id, q.blocking, q.external) for q in found] == [
        ("O-01", True, True), ("O-02", True, False), ("O-03", False, False),
        ("O-05", False, False), ("O-06", True, False),
        ("O-07", True, False)]  # 알 수 없는 시점은 빌드 전으로 묻는다 · 개발팀 몫은 묻지 않는다
    assert found[2].until == "목록을 바꿀 수 있게 두고 영어·베트남어로 시작"
    assert found[0].until == "" and found[3].until == ""


def test_a_triage_without_grounds_is_flagged():
    """줄마다 실은 원본의 위치를 대고, 빌드 중은 그동안 어떻게 만들지를 적는다. 시점은 둘 중 하나다."""
    found = package.lint(_TRIAGED, sources=_SRC)
    assert [term for _, term in _kinds(found, "triage")] == ["O-05", "O-06", "나중에"]
    # 시점·근거 열이 없는 표는 머리 줄로 알린다.
    old = "# 열린 결정\n\n| ID | 질문 | 답할 사람 |\n|---|---|---|\n| O-01 | 보존 기간 | PM |\n"
    assert [term for _, term in _kinds(package.lint(old), "triage")] == ["시점", "근거"]


def test_the_previous_prd_is_read_from_its_ninth_section():
    """이전 구성의 PRD는 9번 섹션이 열린 질문이다. 번호를 뺀 제목도 찾는다(실측 ws-g)."""
    prd = ("# P\n\n## 5. 요구사항\n| 1 | x | PM |\n\n## 열린 질문\n\n"
           "| # | 질문 | 답할 사람 |\n|---|---|---|\n| 1 | 보존 기간(R-08) | PM |\n"
           "| 2 | 저장 용량 | 개발팀 |\n")
    assert [(q.id, q.question) for q in package.open_questions(prd, "ko", prd=True)] == [
        ("1", "보존 기간(R-08)")]
    assert package.previous_rows(prd, "ko", prd=True) == [
        {"질문": "보존 기간(R-08)", "답할 사람": "PM", "시점": ""},
        {"질문": "저장 용량", "답할 사람": "개발팀", "시점": ""}]


# ---- 결정 장부와 단계별 생성 ----

def test_steps_write_the_open_decisions_then_the_ledger_and_readme():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls)))
    assert done.status == "ready" and done.origin == "B"
    # 열린 결정에 빌드 전 PM 결정이 있으면 보기를 만든다.
    assert [step for step, _ in calls] == ["open", "options"]
    assert [s.status for s in done.steps] == ["done", "done"]
    assert done.files == ["README.md", "open-decisions.md", "decisions.md"]
    assert set(done.sources) == set(package.select_sources(B))
    assert s3.blobs[package.PACKAGE_PREFIX + "open-decisions.md"].startswith("# 열린 결정")
    assert s3.blobs[package.PACKAGE_PREFIX + "decisions.md"].startswith("# 결정 장부")
    readme = s3.blobs[package.PACKAGE_PREFIX + "README.md"]
    assert readme.startswith("# 개발 인계 패키지")
    assert readme.index("discovery-document.md") < readme.index("open-decisions.md")
    assert all("build-instructions" not in prompt for _, prompt in calls)
    assert done.findings == []


@pytest.mark.parametrize("error, reason", [
    (RuntimeError("AccessDenied: arn:aws:iam::123:role/x"), "generation_failed"),
    (ValueError("empty document"), "malformed_output"),
])
def test_a_failed_step_is_reported_without_the_raw_error(error, reason):
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    failed = _run(_generate(s3, files, _writer(fail_at="open", error=error)))
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
    """재시작이 보기 단계 도중을 끊은 모양을 만든다."""
    started = await package.start(s3, assess(B, {}))
    steps = package.fresh_steps()
    steps[0] = steps[0].model_copy(update={"status": "done"})
    steps[1] = steps[1].model_copy(update={"status": "running"})
    await s3.put(package.PACKAGE_PREFIX + package.OPEN_DECISIONS, _OPEN)
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
    # 이어서 하는 보기 단계도 저장돼 있던 열린 결정을 받는다.
    assert "O-01 | 상담 이력의 보존 기간은?" in calls[0][1]


def test_resuming_after_the_sources_changed_starts_over():
    """바뀐 원본으로 쓴 문서와 옛 원본으로 쓴 문서가 한 패키지에 섞이면 안 된다."""
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _interrupted_after_open(s3, files)
    files[D + "use-case-intake/use-cases.md"] = "# 바뀜\n"
    v = _run(package.view(s3, read=_workspace_reader(files), paths=B, running=False))
    assert not v.resumable and v.stale
    calls: list = []
    _run(_generate(s3, files, _writer(calls), resume=True))
    assert [step for step, _ in calls] == ["open", "options"]


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
        if _step_of(prompt) == "open":
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
    # 진행 기록이 늦게 도착해 "완료"를 덮지 않는다.
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
    """이전 구성의 manifest(검사 종류 acceptance·ai_goal)를 읽지 못하면 패키지가 없는 것처럼 보인다."""
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
    # 실측 모양: 스트림 읽기 시간 초과가 다른 예외의 원인으로 감싸져 올라온다.
    lambda: _chain(RuntimeError("stream failed"), _read_timeout()),
    lambda: TimeoutError("The read operation timed out"),
])
def test_a_wrapped_read_timeout_is_reported_as_a_timeout(make):
    s3 = FakeS3Store()
    failed = _run(_generate(s3, {}, _writer(fail_at="open", error=make())))
    assert failed.error == "timeout"
    assert failed.steps[0].error == "timeout"


def test_hitting_the_output_cap_is_reported_as_too_long():
    class MaxTokensReachedException(Exception):
        pass
    s3 = FakeS3Store()
    failed = _run(_generate(s3, {}, _writer(fail_at="open", error=_chain(
        RuntimeError("agent loop"), MaxTokensReachedException("Model stopped generating")))))
    assert failed.error == "too_long" and failed.steps[0].error == "too_long"


def test_the_handoff_writer_waits_long_and_reports_stream_progress(monkeypatch):
    """Strands 기본 읽기 제한(120초)으로는 큰 프로젝트의 첫 출력을 기다리지 못한다(실측).
    진행은 Strands 콜백의 본문 조각(data)과 생각(reasoningText)에서 온다."""
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
                # Opus 5.5의 생각은 기본으로 내용 없이 온다(display "omitted") — 표시만 있다.
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


# ---- 보완 질문의 보기 ----

def _asked():
    return [q for q in package.open_questions(_TRIAGED, "ko") if q.blocking and not q.external]


def test_options_keep_only_grounded_unmarked_choices_in_a_neutral_order():
    reply = "```json\n" + json.dumps({"questions": [
        {"id": "O-02", "options": [
            {"text": "7일", "source": "go-to-market/gtm-questions.md Q4"},
            {"text": "30일", "source": "discovery/envision/prfaq-clarifying-questions.md Q2"},
            {"text": "1년", "source": "legal/somewhere.md"},          # 실은 원본이 아니다
            {"text": "30일", "source": "envision/prfaq-clarifying-questions.md"}]},  # 중복
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
    assert by_id["O-06"] is None   # 추천 표시 → 서술형
    assert by_id["O-07"] is None   # 근거 있는 보기가 둘 미만 → 서술형
    assert len(options) == 1       # 모르는 id는 버린다
    # 순서는 모델이 준 순서가 아니라 문장으로 정해진다.
    flipped = reply.replace('"7일"', '"__"').replace('"30일", "source": "discovery',
                                                    '"7일", "source": "discovery').replace('"__"', '"30일"')
    again = package.parse_options(flipped, _asked(), _SRC)
    key = supplement.open_key(_asked()[0].question)
    assert [o.text for o in again[key]] == [o.text for o in by_id["O-02"]]
    with pytest.raises(ValueError):
        package.parse_options("보기를 만들 수 없습니다.", _asked(), _SRC)


def test_an_option_that_points_at_another_option_is_dropped():
    """실측(ws-g 인스턴스 생성): "위 전체 항목에 … 추가"가 순서를 다시 정한 뒤 첫 번째로 보였다.
    "범위의"·"위험성평가"처럼 단어 안의 "위"는 건드리지 않는다."""
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
    # 외부 확인 · 빌드 중 · 개발팀 몫은 보기를 만들지 않는다.
    assert "O-01:" not in prompt and "O-03:" not in prompt and "O-04:" not in prompt
    saved = _run(package.load_options(s3))
    assert sorted(o.text for o in saved[supplement.open_key("개인 링크의 유효 기간은?")]) == [
        "30일", "7일"]


# ---- 직전 판의 열린 결정 ----

def test_a_question_being_checked_goes_back_as_still_open():
    q = "전자 기록이 종이와 같은 증빙 효력을 갖는가?"
    record = supplement.Supplement(open_answers={supplement.open_key(q): supplement.OpenAnswer(
        question=q, pending_with="법무팀")})
    prompt = package.build_prompt(language="ko", readiness=assess(B, {}), record=record,
                                  sources={})
    facts = _facts(prompt)
    assert facts["open questions being checked with someone else"] == [
        {"question": q, "checking with": "법무팀"}]
    assert facts["PM answers given at handoff"] == []   # 확인 중은 답이 아니다
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
    """실측(ws-g): 앞 PRD의 "개인 링크의 유효 기간"이 다음 생성에서 아무 표시 없이 사라졌다.
    이전 구성의 PRD 9번이 첫 열린 결정의 직전 판이다."""
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B + _SRC}
    _run(s3.put(package.PACKAGE_PREFIX + "PRD.md", _LEGACY_PRD))
    # 인계 탭에서 하나에 답했다 — 그 질문이 빠지는 것은 정상이다. 번호가 달라도 같은 질문이다.
    answered = "만족도 버튼의 형태는? (R-15)"
    _run(supplement.save(s3, supplement.Supplement(open_answers={
        supplement.open_key(answered): supplement.OpenAnswer(question=answered, text="5점 척도")})))
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls, doc=_TRIAGED), paths=B + _SRC))
    previous = _facts(dict(calls)["open"])["open decisions in the previous version"]
    assert [r["질문"] for r in previous] == [
        "증빙 기록의 보존 기간은 얼마인가? (R-08, C-05)", "개인 링크의 유효 기간은? (R-04)",
        "만족도 버튼의 형태는? (R-14)", "파일럿 개발 규모는? (C-08)"]
    assert previous[2]["시점"] == "빌드 중" and previous[3]["답할 사람"] == "개발팀"
    assert "Carry every one of them over" in dict(calls)["open"]
    assert done.carried == ["증빙 기록의 보존 기간은 얼마인가? (R-08, C-05)",
                            "개인 링크의 유효 기간은? (R-04)", "만족도 버튼의 형태는? (R-14)"]
    # 보존 기간·링크 유효 기간은 새 판에 있고(관련 ID는 보지 않는다), 만족도 버튼은 답했다.
    assert _kinds(done.findings, "dropped") == []

    # 다음 판에서 링크 유효 기간이 사라지면 알린다. 이번엔 직전 판이 열린 결정 문서다.
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
                D + "prototype/design-context.md": "# 색\n",
                "prototype/src/index.ts": "export {}\n"})
    _seed(client, "pkg-b", files)
    client.portal.call(s3.put_bytes, "uploads/ab12cd34/동의서.pdf", b"%PDF-1.7 bytes")

    r = client.post("/projects/pkg-b/handoff/package")
    assert r.status_code == 202 and r.json()["status"] == "generating"
    assert [s["status"] for s in r.json()["steps"]] == ["pending"] * len(package.STEPS)
    body = _wait_settled(client, "pkg-b")
    assert body["manifest"]["status"] == "ready"
    assert body["files"]["open-decisions.md"].startswith("# 열린 결정")
    assert body["stale"] == []

    r = client.get("/projects/pkg-b/handoff/package/archive")
    assert r.status_code == 200
    assert "pkg-b-handoff.zip" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert {"README.md", "open-decisions.md", "decisions.md"} <= set(names)
    assert "discovery/use-case-intake/use-cases.md" in names
    assert zf.read("uploads/ab12cd34/동의서.pdf") == b"%PDF-1.7 bytes"   # 업로드는 바이트 그대로
    # 빌드 지시서·디자인 컨텍스트·프로토타입 소스·감사 로그·보기·이전 구성의 문서는 넣지 않는다.
    assert not any(n.endswith(("build-instructions.md", "design-context.md", "audit.md"))
                   or n.startswith("prototype/") for n in names)
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
    # 답하면 패키지가 낡는다 — 다음 생성이 그 답을 쓴다.
    assert client.get("/projects/pkg-open/handoff/package").json()["supplement_changed"] is True


# ---- 초기화 ----

def test_route_reset_wipes_the_package_the_old_prd_and_the_answers_but_not_discovery(env):
    """다시 생성은 직전 판과 보완 답을 이어받는다. 초기화는 그 둘까지 지워 처음 상태로 돌린다."""
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
    # Discovery 산출물은 그대로다.
    ws = registry.get("pkg-reset")
    assert client.portal.call(ws.runner.read_file, B[0]) == files[B[0]]

    # 다음 생성은 이어받을 직전 판이 없다.
    calls: list = []
    env[2]["call"] = _writer(calls)
    client.post("/projects/pkg-reset/handoff/package")
    _wait_settled(client, "pkg-reset")
    assert "open decisions in the previous version" not in _facts(dict(calls)["open"])


def test_route_reset_is_refused_while_generating(env, monkeypatch):
    client, s3, _ = env
    _seed(client, "pkg-busy", {p: f"# {p}\n" for p in B})
    client.portal.call(s3.put, package.PACKAGE_PREFIX + "open-decisions.md", _OPEN)
    monkeypatch.setattr(package.jobs, "running", lambda pid: True)
    assert client.delete("/projects/pkg-busy/handoff").status_code == 409
    assert package.PACKAGE_PREFIX + "open-decisions.md" in s3.blobs
