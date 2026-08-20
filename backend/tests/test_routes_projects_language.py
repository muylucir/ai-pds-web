# backend/tests/test_routes_projects_language.py
#
# 생성 시점의 language 검증과 조회. model_id와 같은 배관을 쓰지만 검증 기준이
# 다르다 — 카탈로그가 아니라 고정된 두 값이다.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pl-1", "pl-2", "pl-3", "pl-4", "pl-5"):
        app_module.registry.remove(pid)


def test_create_accepts_en_and_records_it(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert json.loads(fake.blobs["pl-1/project.json"])["language"] == "en"
    assert app_module.registry.get_language("pl-1") == "en"


def test_create_without_a_language_defaults_to_ko(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-2"})
    assert r.status_code == 200
    # 응답은 실제로 돌게 될 언어를 말한다 — 미지정을 null로 돌려주면 프론트가
    # 폴백 규칙을 또 알아야 한다.
    assert r.json()["language"] == "ko"
    assert app_module.registry.get_language("pl-2") == "ko"


def test_create_rejects_an_unknown_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-3", "language": "ja"})
    assert r.status_code == 400
    # detail은 안정적 코드다 — 프론트 딕셔너리가 이 값으로 문구를 찾는다.
    assert r.json()["detail"] == "language_unsupported"
    assert not app_module.registry.is_registered("pl-3")


def test_get_project_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    app_module.registry.register("pl-4", "이름",
                                 created_at="2026-08-03T00:00:00+00:00",
                                 language="en")
    body = client.get("/projects/pl-4").json()
    assert body["language"] == "en"


def test_list_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pl-5", "language": "en"})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pl-5")
    assert row["language"] == "en"


def test_project_language_helper_reads_the_registry():
    app_module.registry.register("pl-1", None, language="en")
    assert app_module.project_language("pl-1") == "en"
    # 미등록도 ko — 레지스트리가 확정하는 것을 그대로 통과시킨다.
    assert app_module.project_language("never-existed") == "ko"


# ---- 생성 순서: 드라이버가 실제로 그 언어로 만들어지는가 ----


def test_the_created_projects_driver_gets_the_chosen_language(monkeypatch):
    """**이것이 2026-08-04에 남아 있던 결함이다.**

    위의 테스트들은 매니페스트와 레지스트리만 본다 — 둘 다 "en"이 맞게 들어간다.
    그런데 실제로 대화를 도는 **드라이버**는 그 값을 못 받았다.

    create_project의 순서가 원인이었다:

        workspace = await app_module.make_workspace(...)   # driver_factory ->
                                                          # project_language(pid)
        ...
        app_module.registry.register(..., language=...)    # 값이 여기서 들어온다

    make_workspace가 register보다 **먼저** 돌므로, driver_factory가 부르는
    registry.get_language(pid)는 아직 아무것도 모르는 상태에서 폴백 "ko"를
    돌려준다. 그리고 그 드라이버는 프로세스 수명 내내 캐시되므로(레지스트리에
    attach된 Workspace가 들고 있다), 새로 만든 영어 프로젝트의 **모든 턴**이
    한국어 지시로 돈다.

    증상이 특히 헷갈렸던 이유: 헤더의 언어 배지는 매니페스트를 읽으므로 "English"로
    맞게 뜬다. 화면은 영어인데 대화만 한국어인 상태가 이것이다.

    같은 순서 문제가 model_id에도 있다(test_the_created_projects_driver_gets_
    the_chosen_model). 언어와 달리 모델은 env 폴백이 있어 조용히 다른 모델로 돌았다.
    """
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    seen: dict = {}

    def spy_factory(pid, local_root):
        seen["language"] = app_module.project_language(pid)
        return object()

    monkeypatch.setattr(app_module, "driver_factory", spy_factory)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert seen["language"] == "en", (
        "드라이버가 'ko'로 만들어졌다 — 매니페스트와 배지는 en인데 대화는 한국어로 "
        "돈다. make_workspace가 registry.register보다 먼저 도는 순서 문제다.")


def test_the_created_projects_driver_gets_the_chosen_model(monkeypatch):
    """같은 순서 결함의 model_id 판. 언어보다 찾기 어렵다 — project_model은 env
    폴백(ANTHROPIC_MODEL)이 있어서, 고른 모델이 아니라 배포 기본 모델로 조용히
    돈다(에러도, 로그도 없다)."""
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-default-model")
    monkeypatch.setattr(app_module, "_validate_model_id",
                        lambda *a, **k: None, raising=False)

    import aipds.routes.projects as routes_projects
    async def ok(_model_id):
        return None
    monkeypatch.setattr(routes_projects, "_validate_model_id", ok)

    seen: dict = {}

    def spy_factory(pid, local_root):
        seen["model"] = app_module.project_model(pid)
        return object()

    monkeypatch.setattr(app_module, "driver_factory", spy_factory)
    r = client.post("/projects", json={"project_id": "pl-2",
                                       "model_id": "chosen-model"})
    assert r.status_code == 200
    assert seen["model"] == "chosen-model", (
        f"드라이버가 {seen['model']!r}로 만들어졌다 — 고른 모델이 무시되고 env "
        "기본값으로 돈다.")
