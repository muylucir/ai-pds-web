from fastapi.testclient import TestClient
from aipds.app import app

client = TestClient(app)


def test_create_project_without_name_returns_null_name():
    r = client.post("/projects", json={"project_id": "plist-noname"})
    assert r.status_code == 200
    # language는 미지정이어도 응답에 실린다 — 실제로 돌게 될 언어("ko")를
    # 말해야 프론트가 폴백 규칙을 또 알 필요가 없다.
    assert r.json() == {"project_id": "plist-noname", "name": None, "model_id": None,
                        "language": "ko"}


def test_create_project_with_name_returns_it():
    r = client.post("/projects", json={"project_id": "plist-named", "name": "기획전 AI 어시스턴트"})
    assert r.status_code == 200
    assert r.json() == {"project_id": "plist-named", "name": "기획전 AI 어시스턴트",
                        "model_id": None, "language": "ko"}


def test_list_projects_includes_created_projects_with_names():
    client.post("/projects", json={"project_id": "plist-a", "name": "Project A"})
    client.post("/projects", json={"project_id": "plist-b"})
    r = client.get("/projects")
    assert r.status_code == 200
    by_id = {p["project_id"]: p["name"] for p in r.json()["projects"]}
    assert by_id["plist-a"] == "Project A"
    assert by_id["plist-b"] is None


def test_list_projects_is_empty_capable():
    # Not asserting exact emptiness (other tests in the module-level registry
    # may have created projects already) — asserting the shape is always a list.
    r = client.get("/projects")
    assert r.status_code == 200
    assert isinstance(r.json()["projects"], list)


# ---- 페이지네이션 + progress (2026-07-21-project-list-table spec) ----
from aipds import app as app_module
from fakes.in_memory_s3 import FakeS3Store

_STATE_MD = """# AI-PLC State Tracking
- **Current Stage**: DISCOVERY - Envision

## Stage Progress
- [x] Workspace Detection — done
- [x] Discovery Mode Selection — done
- [ ] Envision
- [ ] Solution Analysis
"""


def _register(*pids):
    for pid in pids:
        app_module.registry.register(pid, None)


def test_list_paginates_and_reports_total():
    _register("pg-a", "pg-b", "pg-c")
    r1 = client.get("/projects", params={"page": 1, "size": 2})
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["total"] == 3 and body1["page"] == 1 and body1["size"] == 2
    assert [p["project_id"] for p in body1["projects"]] == ["pg-a", "pg-b"]
    r2 = client.get("/projects", params={"page": 2, "size": 2})
    assert [p["project_id"] for p in r2.json()["projects"]] == ["pg-c"]
    r3 = client.get("/projects", params={"page": 3, "size": 2})
    assert r3.json()["projects"] == []          # 범위 초과는 빈 배열, 200


def test_page_and_size_bounds_are_422():
    assert client.get("/projects", params={"page": 0}).status_code == 422
    assert client.get("/projects", params={"size": 0}).status_code == 422
    assert client.get("/projects", params={"size": 51}).status_code == 422


def test_no_params_defaults_to_page1_size10():
    _register("pg-default")
    r = client.get("/projects")
    body = r.json()
    assert body["page"] == 1 and body["size"] == 10
    assert "total" in body


def test_progress_read_from_s3_state(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bkt")
    fake = FakeS3Store()
    fake.blobs["aiplc-docs/aiplc-state.md"] = _STATE_MD
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: fake)
    _register("pg-state")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-state")
    assert row["progress"] == {
        "current_stage": "DISCOVERY - Envision", "completed": 2, "total": 4}


def test_progress_null_when_state_missing(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bkt")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    _register("pg-nostate")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-nostate")
    assert row["progress"] is None


def test_progress_null_when_bucket_unset(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    _register("pg-nobucket")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-nobucket")
    assert row["progress"] is None


def test_progress_null_when_s3_raises(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bkt")
    class Boom:
        async def get(self, key):
            raise RuntimeError("s3 down")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: Boom())
    _register("pg-s3err")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-s3err")
    assert row["progress"] is None                    # fail-soft, 200 유지


def test_listing_does_not_initialize_workspaces(monkeypatch):
    # 목록 조회가 ensure_workspace/lazy 초기화를 유발하면 안 된다 — 복원 직후
    # 프로젝트 100개가 등록만 된 상태에서 목록을 열어도 워크스페이스는 그대로
    # 비어 있어야 한다.
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bkt")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    _register("pg-lazy")
    client.get("/projects", params={"size": 50})
    assert not app_module.registry.has_workspace("pg-lazy")


# ---- created_at 오름차순 정렬 (2026-07-22) ----

def test_list_sorted_by_created_at_ascending():
    # 등록 순서를 뒤섞어도 created_at 오름차순(오래된 것 먼저)으로 나온다.
    app_module.registry.register("sort-c", None, created_at="2026-07-22T03:00:00+00:00")
    app_module.registry.register("sort-a", None, created_at="2026-07-22T01:00:00+00:00")
    app_module.registry.register("sort-b", None, created_at="2026-07-22T02:00:00+00:00")
    r = client.get("/projects", params={"size": 50})
    ids = [p["project_id"] for p in r.json()["projects"] if p["project_id"].startswith("sort-")]
    assert ids == ["sort-a", "sort-b", "sort-c"]


def test_list_includes_created_at_field():
    app_module.registry.register("sort-ts", None, created_at="2026-07-22T05:00:00+00:00")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "sort-ts")
    assert row["created_at"] == "2026-07-22T05:00:00+00:00"


def test_missing_created_at_sorts_first_and_is_null():
    # 구 매니페스트(created_at 없음) 호환: None은 맨 앞 + 응답에 null.
    app_module.registry.register("sort-old", None)  # created_at 미지정
    app_module.registry.register("sort-new", None, created_at="2026-07-22T09:00:00+00:00")
    r = client.get("/projects", params={"size": 50})
    rows = [p["project_id"] for p in r.json()["projects"] if p["project_id"].startswith("sort-")]
    assert rows.index("sort-old") < rows.index("sort-new")
    old = next(p for p in r.json()["projects"] if p["project_id"] == "sort-old")
    assert old["created_at"] is None
