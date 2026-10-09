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

OUTPUT = """여기부터 문서입니다.
===== PRD.md =====
# 매장 고객 상담 에이전트

## 5. 요구사항과 수용 기준
- R-01 상담원은 고객의 최근 주문 상태를 본다 — 사용자 검증됨
===== validation-report.md =====
# 검증 보고
설문 3명.
===== build-scope.md =====
# 남은 작업
- B-01 프로토타입에서는 고정된 주문 목록을 보여 줬다.
"""


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
    prompt = package.build_prompt(language="ko", readiness=state, record=record,
                                  sources={D + "use-case-intake/use-cases.md": "UC1 고객 문의"})
    assert "<<<FILE aiplc-docs/discovery/use-case-intake/use-cases.md>>>" in prompt
    assert "UC1 고객 문의" in prompt
    assert '"재료 없음"' in prompt and "Write in Korean" in prompt
    facts = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert facts["origin"] == "B"
    assert facts["handoff_answers"] == {"problem.evidence": {"text": "인터뷰 5명"},
                                        "assumptions.failure_reasons": {"unknown": True}}
    assert [a["confirmed_by_pm"] for a in facts["ai_suggestions_accepted"]] == [True, False, False]
    for name in package.WRITTEN:
        assert f"===== {name} =====" in prompt


def test_english_projects_get_english_labels():
    prompt = package.build_prompt(language="en", readiness=assess(B, {}),
                                  record=supplement.Supplement(), sources={})
    assert '"No source material"' in prompt and "Write in English" in prompt


def test_split_output_cuts_on_markers():
    files = package.split_output(OUTPUT)
    assert list(files) == list(package.WRITTEN)
    assert files["PRD.md"].startswith("# 매장 고객 상담 에이전트")
    assert "여기부터" not in files["PRD.md"]


@pytest.mark.parametrize("text", [
    OUTPUT.replace("===== build-scope.md =====", ""),
    OUTPUT.split("===== build-scope.md =====")[0] + "===== build-scope.md =====\n  \n",
])
def test_split_output_rejects_a_missing_or_empty_document(text):
    with pytest.raises(ValueError):
        package.split_output(text)


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


def _workspace_reader(files):
    async def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]
    return read


def _run(coro):
    return asyncio.run(coro)


def test_run_writes_the_package_and_records_what_it_was_built_from():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    state = assess(B, {})
    prompts = []

    async def call(prompt):
        prompts.append(prompt)
        return OUTPUT

    async def go():
        started = await package.start(s3, state, now="t0")
        return await package.run(s3, read=_workspace_reader(files), paths=B, readiness=state,
                                 language="ko", call=call, started=started)
    done = _run(go())
    assert done.status == "ready" and done.origin == "B"
    assert done.files == list(package.FILES)
    assert set(done.sources) == set(package.select_sources(B))
    assert s3.blobs[package.PACKAGE_PREFIX + "README.md"].startswith("# 개발 인계 패키지")
    assert s3.blobs[package.PACKAGE_PREFIX + "PRD.md"].startswith("# 매장")
    assert json.loads(s3.blobs[package.MANIFEST_KEY])["status"] == "ready"
    # 빌드 지시서는 재료가 아니다.
    assert "build-instructions" not in prompts[0]


@pytest.mark.parametrize("call_result, reason", [
    (RuntimeError("AccessDenied: arn:aws:iam::123:role/x"), "generation_failed"),
    ("이것은 구분선이 없는 응답", "malformed_output"),
])
def test_a_failed_generation_leaves_a_failed_manifest_without_the_raw_error(call_result, reason):
    s3 = FakeS3Store()
    state = assess(B, {})

    async def call(prompt):
        if isinstance(call_result, Exception):
            raise call_result
        return call_result

    async def go():
        started = await package.start(s3, state)
        return await package.run(s3, read=_workspace_reader({}), paths=B, readiness=state,
                                 language="ko", call=call, started=started)
    failed = _run(go())
    assert failed.status == "failed" and failed.error == reason
    assert "arn:aws" not in s3.blobs[package.MANIFEST_KEY]
    assert not any(k.endswith("PRD.md") for k in s3.blobs)


def test_view_reports_interrupted_stale_and_supplement_changes():
    s3 = FakeS3Store()
    files = {p: f"# {p}\n" for p in B}
    state = assess(B, {})

    async def call(prompt):
        return OUTPUT

    async def go():
        started = await package.start(s3, state)
        assert (await package.view(s3, read=_workspace_reader(files), paths=B,
                                   running=False)).manifest.status == "interrupted"
        await package.run(s3, read=_workspace_reader(files), paths=B, readiness=state,
                          language="ko", call=call, started=started)
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


def test_the_package_lives_outside_the_agent_workspace():
    from aipds.runner import AgentRunner
    from aipds.workspace_sync import is_synced_key
    for name in package.FILES + ("manifest.json",):
        key = package.PACKAGE_PREFIX + name
        assert not is_synced_key(key)
        assert not key.startswith(AgentRunner._RESTORE_PREFIXES)


# ---- routes ----

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()
    monkeypatch.setattr(app_module, "s3_store_factory", lambda project_id: s3)

    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)

    async def call(prompt):
        return OUTPUT
    monkeypatch.setattr(app_module, "handoff_writer_factory", lambda pid: call)
    with TestClient(app) as client:
        yield client, s3


def _seed(client, pid, files):
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)

    async def seed():
        for path, content in files.items():
            await ws.runner.write_file(path, content)
    client.portal.call(seed)


def _wait_ready(client, pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/projects/{pid}/handoff/package").json()
        if body["manifest"] and body["manifest"]["status"] != "generating":
            return body
        time.sleep(0.02)
    raise AssertionError("package never finished")


def test_route_generates_views_and_downloads(env):
    client, _ = env
    files = {p: f"# {p}\n" for p in B} | {"prototype/src/index.ts": "export {}\n"}
    _seed(client, "pkg-b", files)

    r = client.post("/projects/pkg-b/handoff/package")
    assert r.status_code == 202 and r.json()["status"] == "generating"
    body = _wait_ready(client, "pkg-b")
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


def test_route_refuses_to_generate_without_a_spec(env):
    client, _ = env
    _seed(client, "pkg-empty", {D + "envision/pain-point-analysis.md": "# x\n"})
    r = client.post("/projects/pkg-empty/handoff/package")
    assert r.status_code == 409
    assert r.json()["detail"]["blockers"] == ["no_spec"]


def test_route_archive_is_404_before_a_package_exists(env):
    client, _ = env
    _seed(client, "pkg-none", {p: "# x\n" for p in B})
    assert client.get("/projects/pkg-none/handoff/package/archive").status_code == 404
    assert client.get("/projects/pkg-none/handoff/package").json()["manifest"] is None
