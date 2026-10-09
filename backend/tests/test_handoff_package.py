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

DOCS = {
    "prd": "# 매장 고객 상담 에이전트\n\n## 5. 요구사항과 수용 기준\n- R-01 상담원은 고객의 최근 주문 상태를 본다\n",
    "validation": "# 검증 보고\n설문 3명. R-01 증명됨.\n",
    "scope": "# 남은 작업\n- B-01 프로토타입에서는 고정된 주문 목록을 보여 줬다.\n",
}


def _step_of(prompt: str) -> str:
    for name, file in package.STEPS:
        if f"## Write {file}" in prompt:
            return name
    raise AssertionError("prompt names no step")


def _writer(calls: list | None = None, *, fail_at: str | None = None, error=None):
    """단계마다 그 문서를 돌려주는 가짜 모델. `fail_at` 단계에서 실패한다."""
    async def call(prompt, progress=None):
        step = _step_of(prompt)
        if calls is not None:
            calls.append((step, prompt))
        if step == fail_at:
            raise error or RuntimeError("AccessDenied: arn:aws:iam::123:role/x")
        return "머리말은 버려진다.\n" + DOCS[step]
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


# ---- 재료와 프롬프트 ----

def test_sources_skip_build_instructions_design_and_survey_forms_and_lead_with_the_document():
    paths = [D + "prototype/build-instructions.md", D + "prototype/design-context.md",
             D + "prototype/validation-questionnaire.md", D + "prototype/prototype-spec.md",
             D + "envision/pain-point-questions.md", D + "discovery-document.md",
             "aiplc-docs/audit.md", "prototype/src/app.ts"]
    assert package.select_sources(paths) == [
        D + "discovery-document.md", D + "prototype/prototype-spec.md",
        D + "envision/pain-point-questions.md"]


def test_prompt_carries_files_facts_and_confirmations():
    path = D + "use-case-intake/use-case-intake-questions.md"
    state = assess(B + [path], {path: QUESTIONS})
    record = supplement.Supplement(
        answers={"problem.evidence": supplement.Answer(text="인터뷰 5명"),
                 "assumptions.failure_reasons": supplement.Answer(unknown=True)},
        confirmed={path + "#1": "t"})
    prompt = package.build_prompt("prd", language="ko", readiness=state, record=record,
                                  sources={D + "use-case-intake/use-cases.md": "UC1 고객 문의"})
    assert "<<<FILE aiplc-docs/discovery/use-case-intake/use-cases.md>>>" in prompt
    assert "UC1 고객 문의" in prompt
    assert '"재료 없음"' in prompt and "Write in Korean" in prompt
    assert "## Write PRD.md" in prompt and "9. 열린 질문" in prompt
    facts = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert facts["origin"] == "B"
    assert facts["handoff_answers"] == {"problem.evidence": {"text": "인터뷰 5명"},
                                        "assumptions.failure_reasons": {"unknown": True}}
    assert [a["confirmed_by_pm"] for a in facts["ai_suggestions_accepted"]] == [True, False, False]


def test_later_steps_get_the_prd_so_ids_match():
    prompt = package.build_prompt("scope", language="ko", readiness=assess(B, {}),
                                  record=supplement.Supplement(), sources={}, prd=DOCS["prd"])
    assert "## Write build-scope.md" in prompt
    assert "<<<PRD>>>\n# 매장 고객 상담 에이전트" in prompt
    first = package.build_prompt("prd", language="ko", readiness=assess(B, {}),
                                 record=supplement.Supplement(), sources={})
    assert "<<<PRD>>>" not in first


def test_each_step_is_told_its_length_budget():
    """상한 없이 쓴 PRD가 25,000자를 넘어 출력 상한에 잘렸다(실측 industry-safe-law)."""
    for step, budget in (("prd", "12,000"), ("validation", "5,000"), ("scope", "6,000")):
        prompt = package.build_prompt(step, language="ko", readiness=assess(B, {}),
                                      record=supplement.Supplement(), sources={})
        assert f"At most about {budget} characters" in prompt


def test_english_projects_get_english_labels():
    prompt = package.build_prompt("prd", language="en", readiness=assess(B, {}),
                                  record=supplement.Supplement(), sources={})
    assert '"No source material"' in prompt and "Write in English" in prompt


def test_clean_output_drops_a_preface_and_refuses_an_empty_document():
    assert package.clean_output("네, 작성했습니다.\n\n# 제목\n본문\n") == "# 제목\n본문\n"
    assert package.clean_output("제목 없는 본문\n") == "제목 없는 본문\n"
    with pytest.raises(ValueError):
        package.clean_output("   \n ")


# ---- 검사 ----

def test_lint_finds_technology_and_vague_words():
    files = {"PRD.md": "\n".join([
        "상담 이력을 PostgreSQL에 저장한다",
        "Bedrock 호출 없이 고정 문장으로 답했다",
        "모델은 us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "포트 3000에서 띄운다",
        "리액트로 화면을 만든다",
        "고객이 빠르게 답을 받는다",
        "The flow is user-friendly",
    ])}
    found = {(f.line, f.kind, f.term) for f in package.lint(files)}
    assert (1, "tech", "PostgreSQL") in found
    assert (2, "tech", "Bedrock") in found
    assert any(line == 3 and "anthropic" in term for line, _, term in found)
    assert (4, "tech", "포트 3000") in found
    assert (5, "tech", "리액트") in found
    assert (6, "vague", "빠르게") in found
    assert (7, "vague", "user-friendly") in found


def test_lint_leaves_ordinary_product_language_alone():
    files = {"PRD.md": "\n".join([
        "생성 10/08 14:20 · Express delivery 고객",
        "매장 300곳의 상담원이 질문 후 3초 안에 첫 답을 본다",
        "근거: `aiplc-docs/discovery/prototypes/a/PROTOTYPE-a.md`",
    ])}
    assert package.lint(files) == []


# ---- 단계별 생성 ----

def test_steps_run_in_order_and_each_document_is_saved():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls)))
    assert done.status == "ready" and done.origin == "B"
    assert [step for step, _ in calls] == ["prd", "validation", "scope"]
    assert [s.status for s in done.steps] == ["done", "done", "done"]
    assert done.files == list(package.FILES)
    assert set(done.sources) == set(package.select_sources(B))
    assert s3.blobs[package.PACKAGE_PREFIX + "PRD.md"].startswith("# 매장")  # 머리말 제거
    assert s3.blobs[package.PACKAGE_PREFIX + "README.md"].startswith("# 개발 인계 패키지")
    # 뒤의 두 단계는 앞에서 쓴 PRD를 받는다.
    assert "<<<PRD>>>" not in calls[0][1]
    assert all(DOCS["prd"].strip() in prompt for _, prompt in calls[1:])
    # 빌드 지시서는 재료가 아니다.
    assert all("build-instructions" not in prompt for _, prompt in calls)


@pytest.mark.parametrize("error, reason", [
    (RuntimeError("AccessDenied: arn:aws:iam::123:role/x"), "generation_failed"),
    (ValueError("empty document"), "malformed_output"),
])
def test_a_failed_step_keeps_the_finished_ones_and_hides_the_raw_error(error, reason):
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    failed = _run(_generate(s3, files, _writer(fail_at="validation", error=error)))
    assert failed.status == "failed" and failed.error == reason
    assert [(s.status, s.error) for s in failed.steps] == [
        ("done", None), ("failed", reason), ("pending", None)]
    assert package.PACKAGE_PREFIX + "PRD.md" in s3.blobs
    assert package.PACKAGE_PREFIX + "validation-report.md" not in s3.blobs
    assert "arn:aws" not in s3.blobs[package.MANIFEST_KEY]


def test_resuming_runs_only_the_steps_that_did_not_finish():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _run(_generate(s3, files, _writer(fail_at="validation")))
    calls: list = []
    done = _run(_generate(s3, files, _writer(calls), resume=True))
    assert done.status == "ready"
    assert [step for step, _ in calls] == ["validation", "scope"]
    # 이어서 쓰는 단계도 저장돼 있던 PRD를 받는다.
    assert DOCS["prd"].strip() in calls[0][1]


def test_resuming_after_the_sources_changed_starts_over():
    """바뀐 원본으로 쓴 문서와 옛 원본으로 쓴 문서가 한 패키지에 섞이면 안 된다."""
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _run(_generate(s3, files, _writer(fail_at="scope")))
    files[D + "use-case-intake/use-cases.md"] = "# 바뀜\n"
    calls: list = []
    _run(_generate(s3, files, _writer(calls), resume=True))
    assert [step for step, _ in calls] == ["prd", "validation", "scope"]


def test_starting_without_resume_discards_the_finished_steps():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    _run(_generate(s3, files, _writer(fail_at="scope")))
    calls: list = []
    _run(_generate(s3, files, _writer(calls)))
    assert [step for step, _ in calls] == ["prd", "validation", "scope"]


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
        if _step_of(prompt) == "prd":
            progress(0, True)
            await asyncio.sleep(0.05)
            progress(500, False)
            await asyncio.sleep(0.05)
        return DOCS[_step_of(prompt)]

    _run(_generate(s3, files, call))
    prd = [m["steps"][0] for m in snapshots if m["steps"] and m["steps"][0]["status"] == "running"]
    assert any(s["thinking"] and s["chars"] == 0 for s in prd)
    assert any(s["chars"] == 500 and not s["thinking"] for s in prd)
    # 진행 기록이 늦게 도착해 "완료"를 덮지 않는다.
    assert snapshots[-1]["status"] == "ready"
    assert [s["status"] for s in snapshots[-1]["steps"]] == ["done", "done", "done"]


def test_view_reports_an_interrupted_run_as_resumable():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}

    async def go():
        started = await package.start(s3, assess(B, {}))
        # 재시작이 두 번째 단계 도중을 끊은 모양을 만든다.
        steps = package.fresh_steps()
        steps[0] = steps[0].model_copy(update={"status": "done"})
        steps[1] = steps[1].model_copy(update={"status": "running"})
        await s3.put(package.PACKAGE_PREFIX + "PRD.md", DOCS["prd"])
        sources = await package.read_sources(_workspace_reader(files), package.select_sources(B))
        record = await supplement.load(s3)
        await s3.put(package.MANIFEST_KEY, started.model_copy(update={
            "steps": steps, "sources": {p: package.digest(t) for p, t in sources.items()},
            "supplement": package.digest(record.model_dump_json())}).model_dump_json())
        return await package.view(s3, read=_workspace_reader(files), paths=B, running=False)
    v = _run(go())
    assert v.manifest.status == "interrupted"
    assert [(s.status, s.error) for s in v.manifest.steps] == [
        ("done", None), ("failed", "interrupted"), ("pending", None)]
    assert v.resumable


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


def test_a_failed_run_whose_sources_changed_is_not_resumable():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}

    async def go():
        await _generate(s3, files, _writer(fail_at="scope"))
        assert (await package.view(s3, read=_workspace_reader(files), paths=B,
                                   running=False)).resumable
        files[D + "use-case-intake/use-cases.md"] = "# 바뀜\n"
        return await package.view(s3, read=_workspace_reader(files), paths=B, running=False)
    v = _run(go())
    assert not v.resumable and v.stale


def test_the_package_lives_outside_the_agent_workspace():
    from aipds.runner import AgentRunner
    from aipds.workspace_sync import is_synced_key
    for name in package.FILES + ("manifest.json",):
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
    failed = _run(_generate(s3, {}, _writer(fail_at="prd", error=make())))
    assert failed.error == "timeout"
    assert failed.steps[0].error == "timeout"


def test_hitting_the_output_cap_is_reported_as_too_long():
    class MaxTokensReachedException(Exception):
        pass
    s3 = FakeS3Store()
    failed = _run(_generate(s3, {}, _writer(fail_at="prd", error=_chain(
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
    client, _, _ = env
    files = {p: f"# {p}\n" for p in B} | {"prototype/src/index.ts": "export {}\n"}
    _seed(client, "pkg-b", files)

    r = client.post("/projects/pkg-b/handoff/package")
    assert r.status_code == 202 and r.json()["status"] == "generating"
    assert [s["status"] for s in r.json()["steps"]] == ["pending", "pending", "pending"]
    body = _wait_settled(client, "pkg-b")
    assert body["manifest"]["status"] == "ready"
    assert body["files"]["PRD.md"].startswith("# 매장")
    assert body["stale"] == []

    r = client.get("/projects/pkg-b/handoff/package/archive")
    assert r.status_code == 200
    assert "pkg-b-handoff.zip" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"PRD.md", "validation-report.md", "build-scope.md", "README.md"} <= set(names)
    assert "discovery/use-case-intake/use-cases.md" in names
    # 프로토타입 소스와 감사 로그는 넣지 않는다.
    assert not any(n.startswith("prototype/") or n.endswith("audit.md") for n in names)


def test_route_resumes_from_the_failed_step(env):
    client, _, writer = env
    _seed(client, "pkg-resume", {p: f"# {p}\n" for p in B})
    writer["call"] = _writer(fail_at="scope")
    client.post("/projects/pkg-resume/handoff/package")
    failed = _wait_settled(client, "pkg-resume")
    assert failed["manifest"]["status"] == "failed" and failed["resumable"]

    calls: list = []
    writer["call"] = _writer(calls)
    r = client.post("/projects/pkg-resume/handoff/package", json={"resume": True})
    assert [s["status"] for s in r.json()["steps"]] == ["done", "done", "pending"]
    assert _wait_settled(client, "pkg-resume")["manifest"]["status"] == "ready"
    assert [step for step, _ in calls] == ["scope"]


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


# ---- PRD가 인용한 AI 제안 ----

_PRD_WITH_CITATIONS = """# 메가마트 안전ON PRD

## 3. 목표와 성공 지표

| ID | 목표 | 등급 · 출처 |
|---|---|---|
| G-01 | 담당자 투입 시간 절감: 파일럿 90일 30% 이상 | AI 제안 수락 · aiplc-docs/discovery/product-strategy/strategy-questions.md Q1 |
| G-02 | 건별 완료율 95% 이상 | PM 결정 · aiplc-docs/discovery/product-strategy/strategy-questions.md Q3 |

## 5. 요구사항과 수용 기준

- 2027년 상반기 3개 지점 파일럿 [AI 제안 수락 · aiplc-docs/discovery/product-strategy/strategy-questions.md Q2·Q4]
- R-03 엑셀 명단 업로드 [AI 제안 수락 · strategy-questions.md Q99]
"""


def test_the_prd_citations_pick_out_which_ai_suggestions_became_requirements():
    """실측(industry-safe-law): PRD의 "AI 제안 수락" 26줄이 40개 중 19개 질문을 가리켰다."""
    path = D + "product-strategy/strategy-questions.md"
    state = assess([path], {path: QUESTIONS})
    cited = package.cited_suggestions(_PRD_WITH_CITATIONS, state, "ko")
    assert cited == {
        path + "#1": ["G-01 담당자 투입 시간 절감: 파일럿 90일 30% 이상"],
        path + "#2": ["2027년 상반기 3개 지점 파일럿"],
        path + "#4": ["2027년 상반기 3개 지점 파일럿"],
    }
    # Q3은 PM 결정으로 인용됐고, Q99는 수락 목록에 없다 — 둘 다 확인 대상이 아니다.


def test_the_form_lists_the_citations_of_the_saved_prd():
    s3 = FakeS3Store()
    path = D + "product-strategy/strategy-questions.md"
    paths = B + [path]
    files = {p: f"# {p}\n" for p in B} | {path: QUESTIONS}

    async def call(prompt, progress=None):
        step = _step_of(prompt)
        return _PRD_WITH_CITATIONS if step == "prd" else DOCS[step]

    async def go():
        state = assess(paths, {path: QUESTIONS})
        started = await package.start(s3, state)
        return await package.run(s3, read=_workspace_reader(files), paths=paths, readiness=state,
                                 language="ko", call=call, started=started), state
    done, state = _run(go())
    assert done.status == "ready"
    cited = package.cited_suggestions(s3.blobs[package.PACKAGE_PREFIX + "PRD.md"], state, "ko")
    assert set(cited) == {path + "#1", path + "#2", path + "#4"}

    view = supplement.view(state, supplement.Supplement(), cited)
    assert view.has_package
    by_number = {c.number: c.in_prd for c in view.confirmations}
    assert by_number == {1: ["G-01 담당자 투입 시간 절감: 파일럿 90일 30% 이상"],
                         2: ["2027년 상반기 3개 지점 파일럿"],
                         4: ["2027년 상반기 3개 지점 파일럿"]}
    assert not supplement.view(state, supplement.Supplement()).has_package


def test_route_supplement_reads_citations_from_the_saved_prd(env):
    """이 기능 이전에 만든 패키지도 PRD 파일만 있으면 인용을 보인다 — 저장된 값에 기대지 않는다."""
    client, s3, writer = env
    path = D + "product-strategy/strategy-questions.md"
    _seed(client, "pkg-cited", {p: f"# {p}\n" for p in B} | {path: QUESTIONS})
    assert client.get("/projects/pkg-cited/handoff/supplement").json()["has_package"] is False

    async def call(prompt, progress=None):
        step = _step_of(prompt)
        return _PRD_WITH_CITATIONS if step == "prd" else DOCS[step]
    writer["call"] = call
    client.post("/projects/pkg-cited/handoff/package")
    assert _wait_settled(client, "pkg-cited")["manifest"]["status"] == "ready"

    body = client.get("/projects/pkg-cited/handoff/supplement").json()
    assert body["has_package"] is True
    assert {c["number"]: bool(c["in_prd"]) for c in body["confirmations"]} == {1: True, 2: True, 4: True}
