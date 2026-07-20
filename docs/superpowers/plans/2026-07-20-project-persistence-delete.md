# 프로젝트 목록 영속화 + 프로젝트 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로젝트 목록·이름을 S3 매니페스트(`projects/<pid>/project.json`)로 영속화해 백엔드 재시작 후에도 복원하고, 프로젝트 전체 삭제(레지스트리+VM+S3) API와 확인 다이얼로그 UI를 추가한다.

**Architecture:** `ProjectRegistry`를 "등록(names)"과 "살아있는 워크스페이스(workspaces)"로 분리하고, FastAPI lifespan에서 `projects/` prefix를 스캔해 목록만 복원한다. sandbox는 `deps.ensure_workspace`가 첫 요청 때 pid별 lock 아래 lazy 부팅한다. 삭제는 VM stop(베스트에포트) → S3 `delete_prefix` 배치 삭제(실패 시 500, 멱등) → 레지스트리 제거 순서.

**Tech Stack:** FastAPI lifespan, asyncio.gather/Lock, boto3 delete_objects(1000개 배치), Next.js 15 + vitest/msw.

**Spec:** `docs/superpowers/specs/2026-07-20-project-persistence-delete-design.md`

## Global Constraints

- 매니페스트 키는 정확히 `projects/<pid>/project.json`, 내용은 `{"project_id": str, "name": str | null, "created_at": "<ISO8601 UTC>"}`.
- `PATHFINDER_S3_BUCKET`이 빈 값이면(로컬/테스트) 매니페스트 쓰기·복원·S3 삭제를 **전부 생략** — 기존 인메모리 동작과 기존 테스트가 무변경으로 통과해야 한다.
- 매니페스트 put 실패 시 `POST /projects`는 **500** (sandbox는 베스트에포트 정리, 레지스트리 미등록).
- 삭제 시 sandbox stop 실패는 **로그만 하고 계속**; S3 삭제 실패는 **500 + 레지스트리 유지**(멱등 재시도 가능).
- lazy 부팅 실패는 **503** + 등록 유지. 미등록 pid는 기존과 동일하게 **404 "unknown project"**.
- 삭제 확인 다이얼로그 문구(정확히): 제목 `'{name ?? project_id}' 프로젝트 삭제`, 본문 `채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.`, 버튼 `삭제`/`취소`.
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest`, 프론트: `cd frontend && npx vitest run`.

---

### Task 1: ProjectRegistry 분리 (register/attach/remove)

**Files:**
- Modify: `backend/pathfinder/workspace.py:59-83` (ProjectRegistry 클래스 교체)
- Modify: `backend/pathfinder/routes/projects.py:12-21` (`create` 호출부를 `register`+`attach`로)
- Test: `backend/tests/test_registry.py` (신규)

**Interfaces:**
- Consumes: 기존 `Workspace`, `Sandbox` (변경 없음)
- Produces: `ProjectRegistry.register(project_id: str, name: str | None = None) -> None`, `attach(project_id: str, sandbox: Sandbox) -> Workspace` (미등록 pid면 KeyError), `get(project_id) -> Workspace` (워크스페이스 없으면 KeyError), `is_registered(pid) -> bool`, `has_workspace(pid) -> bool`, `remove(pid) -> Workspace | None`, `list_ids() -> list[str]`, `get_name(pid) -> str | None` (미등록이면 KeyError). **`create()`는 제거.**

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_registry.py`

```python
# backend/tests/test_registry.py
import pytest
from pathfinder.workspace import ProjectRegistry


class _FakeSandbox:  # Registry는 sandbox를 불투명 객체로만 다룬다
    pass


def test_register_then_attach_and_get():
    reg = ProjectRegistry()
    reg.register("p1", name="이름")
    ws = reg.attach("p1", _FakeSandbox())
    assert reg.get("p1") is ws
    assert reg.is_registered("p1") and reg.has_workspace("p1")
    assert reg.get_name("p1") == "이름"


def test_register_only_is_listed_but_has_no_workspace():
    reg = ProjectRegistry()
    reg.register("p2")  # 복원된 프로젝트 상태
    assert reg.list_ids() == ["p2"]
    assert reg.is_registered("p2") and not reg.has_workspace("p2")
    assert reg.get_name("p2") is None
    with pytest.raises(KeyError):
        reg.get("p2")  # 워크스페이스는 아직 없음


def test_attach_without_register_raises():
    reg = ProjectRegistry()
    with pytest.raises(KeyError):
        reg.attach("ghost", _FakeSandbox())


def test_remove_clears_both_and_returns_workspace():
    reg = ProjectRegistry()
    reg.register("p3")
    ws = reg.attach("p3", _FakeSandbox())
    assert reg.remove("p3") is ws
    assert not reg.is_registered("p3") and not reg.has_workspace("p3")
    assert reg.remove("p3") is None  # 멱등


def test_unknown_pid_raises_keyerror():
    reg = ProjectRegistry()
    with pytest.raises(KeyError):
        reg.get_name("nope")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py -q`
Expected: FAIL — `AttributeError: 'ProjectRegistry' object has no attribute 'register'` (또는 is_registered 등)

- [ ] **Step 3: ProjectRegistry 교체** — `backend/pathfinder/workspace.py`의 `class ProjectRegistry` 전체(59-83행)를 다음으로:

```python
class ProjectRegistry:
    """'아는 프로젝트'(_names)와 '살아있는 워크스페이스'(_workspaces)를 분리.

    S3 매니페스트에서 복원된 프로젝트는 register만 된 상태(목록에는 보이지만
    sandbox 없음)로 시작하고, 첫 요청 시 deps.ensure_workspace가 attach한다."""

    def __init__(self):
        self._names: dict[str, str | None] = {}
        self._workspaces: dict[str, Workspace] = {}

    def register(self, project_id: str, name: str | None = None) -> None:
        self._names[project_id] = name

    def attach(self, project_id: str, sandbox: Sandbox) -> Workspace:
        if project_id not in self._names:
            raise KeyError(project_id)  # 등록 없이 연결 금지 — 호출 순서 버그를 조기 검출
        ws = Workspace(sandbox)
        self._workspaces[project_id] = ws
        return ws

    def get(self, project_id: str) -> Workspace:
        return self._workspaces[project_id]

    def is_registered(self, project_id: str) -> bool:
        return project_id in self._names

    def has_workspace(self, project_id: str) -> bool:
        return project_id in self._workspaces

    def remove(self, project_id: str) -> Workspace | None:
        """등록·워크스페이스 모두 제거. 있던 Workspace를 반환(없으면 None). 멱등."""
        self._names.pop(project_id, None)
        return self._workspaces.pop(project_id, None)

    def list_ids(self) -> list[str]:
        # dict는 삽입 순서를 보존 — 등록(생성/복원) 순서 그대로 노출
        return list(self._names.keys())

    def get_name(self, project_id: str) -> str | None:
        if project_id not in self._names:
            raise KeyError(project_id)
        return self._names[project_id]
```

- [ ] **Step 4: 호출부 갱신** — `backend/pathfinder/routes/projects.py`의 `create_project`를 다음으로 (아직 매니페스트 없음 — Task 5에서 추가):

```python
@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    sandbox = await app_module.make_sandbox(body.project_id)
    app_module.registry.register(body.project_id, body.name)
    app_module.registry.attach(body.project_id, sandbox)
    return {"project_id": body.project_id, "name": body.name}
```

- [ ] **Step 5: 전체 백엔드 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS (기존 라우트 테스트는 registry 내부 구조에 의존하지 않음 — POST/GET 계약 동일)

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/workspace.py backend/pathfinder/routes/projects.py backend/tests/test_registry.py
git commit -m "refactor(backend): split ProjectRegistry into register/attach — groundwork for restore+lazy boot"
```

---

### Task 2: S3Store.delete_prefix (+ FakeS3Store)

**Files:**
- Modify: `backend/pathfinder/sandbox/s3store.py` (Protocol과 S3Store에 delete_prefix 추가)
- Modify: `backend/tests/fakes/in_memory_s3.py` (동일 메서드)
- Test: `backend/tests/test_s3store_delete.py` (신규)

**Interfaces:**
- Produces: `async def delete_prefix(self, prefix: str) -> int` — 스토어 네임스페이스(`self._prefix`) 안의 상대 prefix 이하 오브젝트 전부 삭제, 삭제 개수 반환. `S3StoreLike` Protocol에도 추가.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_s3store_delete.py`

```python
# backend/tests/test_s3store_delete.py
import pytest
from pathfinder.sandbox.s3store import S3Store
from tests.fakes.in_memory_s3 import FakeS3Store


class _StubS3Client:
    """list_objects_v2 페이지네이터 + delete_objects만 흉내내는 최소 스텁."""

    def __init__(self, keys: list[str]):
        self.objects = {k: "x" for k in keys}
        self.delete_calls: list[int] = []  # 호출당 배치 크기 기록

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class _P:
            def paginate(self, Bucket, Prefix):
                matched = [k for k in sorted(client.objects) if k.startswith(Prefix)]
                # 1000개 초과를 시뮬레이션하려고 페이지를 700개 단위로 쪼갬
                for i in range(0, len(matched), 700):
                    yield {"Contents": [{"Key": k} for k in matched[i:i + 700]]}
                if not matched:
                    yield {}

        return _P()

    def delete_objects(self, Bucket, Delete):
        batch = [o["Key"] for o in Delete["Objects"]]
        assert len(batch) <= 1000  # S3 API 상한
        self.delete_calls.append(len(batch))
        for k in batch:
            self.objects.pop(k, None)
        return {}


@pytest.mark.asyncio
async def test_delete_prefix_removes_only_namespaced_prefix():
    stub = _StubS3Client(["sessions/session_a/m1.json", "sessions/session_a/m2.json",
                          "sessions/session_b/m1.json"])
    store = S3Store(bucket="b", prefix="sessions/", client=stub)
    n = await store.delete_prefix("session_a/")
    assert n == 2
    assert list(stub.objects) == ["sessions/session_b/m1.json"]


@pytest.mark.asyncio
async def test_delete_prefix_batches_over_1000():
    keys = [f"projects/p1/f{i:04}" for i in range(1500)]
    stub = _StubS3Client(keys)
    store = S3Store(bucket="b", prefix="projects/", client=stub)
    n = await store.delete_prefix("p1/")
    assert n == 1500
    assert sum(stub.delete_calls) == 1500
    assert max(stub.delete_calls) <= 1000 and len(stub.delete_calls) >= 2


@pytest.mark.asyncio
async def test_fake_store_delete_prefix_matches_contract():
    fake = FakeS3Store()
    fake.blobs["session_a/m1"] = "x"
    fake.blobs["session_a/m2"] = "x"
    fake.blobs["session_b/m1"] = "x"
    assert await fake.delete_prefix("session_a/") == 2
    assert list(fake.blobs) == ["session_b/m1"]
    assert await fake.delete_prefix("session_a/") == 0  # 멱등
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_s3store_delete.py -q`
Expected: FAIL — `AttributeError: 'S3Store' object has no attribute 'delete_prefix'`

- [ ] **Step 3: 구현** — `backend/pathfinder/sandbox/s3store.py`

Protocol에 한 줄 추가:

```python
class S3StoreLike(Protocol):
    async def get(self, key: str) -> str: ...
    async def put(self, key: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete_prefix(self, prefix: str) -> int: ...
```

S3Store 클래스 끝(`list` 아래)에 추가:

```python
    async def delete_prefix(self, prefix: str) -> int:
        """네임스페이스 내 상대 prefix 이하 오브젝트 전량 삭제(1000개 배치).

        프로젝트 삭제 경로 전용 — list 후 delete_objects라 원자적이진 않지만
        삭제는 멱등이므로 부분 실패 시 재호출로 수렴한다."""
        def _delete() -> int:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys = [obj["Key"]
                    for page in paginator.paginate(Bucket=self._bucket, Prefix=full)
                    for obj in page.get("Contents", [])]
            for i in range(0, len(keys), 1000):  # S3 delete_objects 상한
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                            "Quiet": True})
            return len(keys)

        return await asyncio.to_thread(_delete)
```

`backend/tests/fakes/in_memory_s3.py`의 클래스 끝에 추가:

```python
    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self.blobs if k.startswith(prefix)]
        for k in doomed:
            del self.blobs[k]
        return len(doomed)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_s3store_delete.py -q`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/sandbox/s3store.py backend/tests/fakes/in_memory_s3.py backend/tests/test_s3store_delete.py
git commit -m "feat(backend): S3Store.delete_prefix — batched delete for project removal"
```

---

### Task 3: project_store 모듈 (매니페스트 쓰기/복원/삭제)

**Files:**
- Create: `backend/pathfinder/project_store.py`
- Test: `backend/tests/test_project_store.py` (신규)

**Interfaces:**
- Consumes: `S3StoreLike` (Task 2의 delete_prefix 포함). "root 스토어"는 **prefix가 `projects/`인 스토어** (Task 4의 `projects_root_s3_factory`가 공급).
- Produces:
  - `async def write_manifest(root: S3StoreLike, project_id: str, name: str | None) -> None` — `{pid}/project.json` put. 예외는 전파(호출부가 500 처리).
  - `async def restore_projects(root: S3StoreLike) -> list[tuple[str, str | None]]` — `<pid>/project.json` 패턴만 병렬 GET, 손상 항목은 건너뜀.
  - `async def delete_project_data(sessions: S3StoreLike, root: S3StoreLike, project_id: str) -> None` — `session_{pid}/` + `{pid}/` delete_prefix. 예외 전파.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_project_store.py`

```python
# backend/tests/test_project_store.py
import json
import pytest
from pathfinder.project_store import write_manifest, restore_projects, delete_project_data
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_write_manifest_puts_expected_key_and_shape():
    root = FakeS3Store()
    await write_manifest(root, "p1", "이름")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["project_id"] == "p1" and d["name"] == "이름"
    assert d["created_at"].endswith("+00:00") or d["created_at"].endswith("Z")  # UTC ISO8601


@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps({"project_id": "pa", "name": "A"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # 손상 → 건너뜀
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # 매니페스트 아님 → 무시
    restored = dict(await restore_projects(root))
    assert restored == {"pa": "A", "pb": None}


@pytest.mark.asyncio
async def test_restore_empty_store_returns_empty():
    assert await restore_projects(FakeS3Store()) == []


@pytest.mark.asyncio
async def test_delete_project_data_removes_both_prefixes():
    sessions, root = FakeS3Store(), FakeS3Store()
    sessions.blobs["session_p1/agents/agent_default/messages/message_0.json"] = "{}"
    sessions.blobs["session_p2/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs["p1/project.json"] = "{}"
    root.blobs["p1/aiplc-docs/audit.md"] = "x"
    root.blobs["p2/project.json"] = "{}"
    await delete_project_data(sessions, root, "p1")
    assert list(sessions.blobs) == ["session_p2/agents/agent_default/messages/message_0.json"]
    assert list(root.blobs) == ["p2/project.json"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.project_store'`

- [ ] **Step 3: 구현** — `backend/pathfinder/project_store.py` (신규)

```python
# backend/pathfinder/project_store.py
"""프로젝트 목록의 S3 영속화 (스펙 2026-07-20-project-persistence-delete).

매니페스트는 프로젝트 데이터와 같은 prefix(projects/<pid>/)에 산다 — 삭제가
prefix 하나로 원자적이 되도록. 'root'는 prefix가 projects/ 인 S3StoreLike."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from pathfinder.sandbox.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MANIFEST = re.compile(r"^([^/]+)/project\.json$")


async def write_manifest(root: S3StoreLike, project_id: str, name: str | None) -> None:
    body = json.dumps(
        {"project_id": project_id, "name": name,
         "created_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)


async def restore_projects(root: S3StoreLike) -> list[tuple[str, str | None]]:
    """projects/ 스캔 → 매니페스트 병렬 GET → [(pid, name)]. 손상 항목은 로그
    후 건너뜀 — 하나가 썩어도 나머지 복원을 막지 않는다."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[tuple[str, str | None]] = []
    for key, body in zip(keys, bodies):
        if isinstance(body, BaseException):
            _log.warning("manifest read failed for %s: %r", key, body)
            continue
        try:
            d = json.loads(body)
            pid = d.get("project_id") or _MANIFEST.match(key).group(1)  # type: ignore[union-attr]
            out.append((pid, d.get("name")))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out


async def delete_project_data(sessions: S3StoreLike, root: S3StoreLike,
                              project_id: str) -> None:
    """세션 + 산출물(매니페스트 포함) 전량 삭제. 예외는 전파 — 호출부(라우트)가
    500으로 변환하고 레지스트리를 유지해 재시도를 가능하게 한다."""
    await sessions.delete_prefix(f"session_{project_id}/")
    await root.delete_prefix(f"{project_id}/")
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_store.py -q`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/project_store.py backend/tests/test_project_store.py
git commit -m "feat(backend): project_store — S3 manifest write/restore/delete helpers"
```

---

### Task 4: app.py 배선 — root 팩토리 + lifespan 복원

**Files:**
- Modify: `backend/pathfinder/app.py` (팩토리 2개 추가, lifespan 신설, FastAPI 생성부 변경)
- Test: `backend/tests/test_app_lifespan_restore.py` (신규)

**Interfaces:**
- Consumes: Task 1 `registry.register`, Task 3 `restore_projects`
- Produces:
  - `def durable_projects_enabled() -> bool` — `bool(os.environ.get("PATHFINDER_S3_BUCKET"))`
  - `def projects_root_s3_factory() -> S3StoreLike` — prefix `projects/` 스토어 (테스트에서 monkeypatch)
  - lifespan: 기동 시 `durable_projects_enabled()`면 복원 → `registry.register(pid, name)`; 실패는 로그 + 빈 목록 기동

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_app_lifespan_restore.py`

```python
# backend/tests/test_app_lifespan_restore.py
import json
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store


def test_lifespan_restores_registered_projects(monkeypatch):
    fake = FakeS3Store()
    fake.blobs["restored-1/project.json"] = json.dumps(
        {"project_id": "restored-1", "name": "복원된 프로젝트"})
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    # with-구문이 lifespan을 실행한다 (모듈 레벨 TestClient는 실행 안 함)
    with TestClient(app_module.app) as client:
        r = client.get("/projects")
        assert r.status_code == 200
        by_id = {p["project_id"]: p["name"] for p in r.json()["projects"]}
        assert by_id["restored-1"] == "복원된 프로젝트"
    # 복원된 프로젝트는 목록에만 있고 sandbox는 없다 (lazy는 Task 7)
    assert app_module.registry.is_registered("restored-1")
    assert not app_module.registry.has_workspace("restored-1")


def test_lifespan_skips_restore_without_bucket(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("must not be called")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", _boom)
    with TestClient(app_module.app):
        pass
    assert called["n"] == 0


def test_lifespan_survives_restore_failure(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")

    class _ExplodingStore:
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _ExplodingStore())
    with TestClient(app_module.app) as client:  # 기동이 죽으면 여기서 예외
        assert client.get("/projects").status_code == 200
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_app_lifespan_restore.py -q`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute 'projects_root_s3_factory'`

- [ ] **Step 3: 구현** — `backend/pathfinder/app.py`

`import boto3` 근처의 import 블록에 추가:

```python
import logging
from contextlib import asynccontextmanager
```

(파일 하단 import 그룹의) `from pathfinder.sandbox.s3store import S3Store, S3StoreLike` 아래에:

```python
from pathfinder.project_store import restore_projects

_log = logging.getLogger(__name__)
```

`session_s3_factory` 아래에 팩토리·가드 추가:

```python
# 매니페스트/삭제용 — projects/ 전체를 보는 root 스토어. 테스트에서 monkeypatch.
def projects_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="projects/", client=client)


def durable_projects_enabled() -> bool:
    """버킷 미설정(로컬/테스트)이면 목록 영속화 전체를 생략한다."""
    return bool(os.environ.get("PATHFINDER_S3_BUCKET"))
```

`app = FastAPI(title="Pathfinder")` (140행 부근)를 다음으로 교체:

```python
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 기동 시 S3 매니페스트에서 프로젝트 '목록'만 복원한다. sandbox는 첫
    # 요청에서 lazy 부팅(deps.ensure_workspace) — 기동을 빠르게 유지하고
    # 안 쓰는 프로젝트의 VM을 띄우지 않는다. 복원 실패는 기동을 막지 않는다.
    if durable_projects_enabled():
        try:
            for pid, name in await restore_projects(projects_root_s3_factory()):
                registry.register(pid, name)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
    yield


app = FastAPI(title="Pathfinder", lifespan=_lifespan)
```

- [ ] **Step 4: 통과 확인 (신규 + 전체)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_app_lifespan_restore.py -q && .venv/bin/python -m pytest -q`
Expected: 신규 3 passed, 전체 PASS (기존 테스트는 버킷 미설정이라 복원 생략)

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/app.py backend/tests/test_app_lifespan_restore.py
git commit -m "feat(backend): restore project list from S3 manifests on startup (lifespan)"
```

---

### Task 5: POST /projects 매니페스트 쓰기 (실패 시 500)

**Files:**
- Modify: `backend/pathfinder/routes/projects.py`
- Test: `backend/tests/test_routes_projects_persist.py` (신규)

**Interfaces:**
- Consumes: Task 3 `write_manifest`, Task 4 `durable_projects_enabled`/`projects_root_s3_factory`
- Produces: POST 계약 불변(200 `{project_id, name}` / 409). 버킷 설정 시 매니페스트 put; put 실패 → sandbox.stop() 베스트에포트 → 500 `"project persistence failed"`, 미등록.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_routes_projects_persist.py`

```python
# backend/tests/test_routes_projects_persist.py
import json
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


def test_create_writes_manifest_when_durable(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "persist-1", "name": "이름"})
    assert r.status_code == 200
    d = json.loads(fake.blobs["persist-1/project.json"])
    assert d["project_id"] == "persist-1" and d["name"] == "이름"


def test_create_without_bucket_writes_no_manifest(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "persist-2"})
    assert r.status_code == 200  # 로컬 모드: 매니페스트 생략, 기존 동작


def test_create_fails_500_when_manifest_write_fails(monkeypatch):
    class _ExplodingStore:
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    stopped = {"n": 0}

    class _FakeSandbox:
        async def stop(self):
            stopped["n"] += 1

    async def _fake_make_sandbox(pid):
        return _FakeSandbox()

    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _ExplodingStore())
    monkeypatch.setattr(app_module, "make_sandbox", _fake_make_sandbox)
    r = client.post("/projects", json={"project_id": "persist-3"})
    assert r.status_code == 500
    assert stopped["n"] == 1                                # 베스트에포트 정리
    assert not app_module.registry.is_registered("persist-3")  # 조용한 휘발 프로젝트 금지
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_persist.py -q`
Expected: 1·3번 테스트 FAIL (매니페스트 미작성 / 200 반환)

- [ ] **Step 3: 구현** — `backend/pathfinder/routes/projects.py`

파일 상단 import에 추가:

```python
import logging
from pathfinder.project_store import write_manifest

_log = logging.getLogger(__name__)
```

`create_project`를 다음으로:

```python
@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    sandbox = await app_module.make_sandbox(body.project_id)
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name)
        except Exception:
            # 스펙 결정: 재시작하면 사라질 프로젝트를 조용히 만들지 않는다.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await sandbox.stop()
            except Exception:
                _log.exception("sandbox cleanup after manifest failure failed")
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.register(body.project_id, body.name)
    app_module.registry.attach(body.project_id, sandbox)
    return {"project_id": body.project_id, "name": body.name}
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_persist.py tests/test_routes_projects_list.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/routes/projects.py backend/tests/test_routes_projects_persist.py
git commit -m "feat(backend): write project manifest on create; 500 on persistence failure"
```

---

### Task 6: DELETE /projects/{pid}

**Files:**
- Modify: `backend/pathfinder/routes/projects.py`
- Test: `backend/tests/test_routes_projects_delete.py` (신규)

**Interfaces:**
- Consumes: Task 1 `has_workspace/get/remove/is_registered`, Task 3 `delete_project_data`, Task 4 가드/팩토리 + `session_s3_factory`(기존)
- Produces: `DELETE /projects/{pid}` → 200 `{"deleted": true}` / 404 / 500. 순서: stop(베스트에포트) → S3 삭제(실패 시 500·레지스트리 유지) → remove.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_routes_projects_delete.py`

```python
# backend/tests/test_routes_projects_delete.py
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


class _FakeSandbox:
    def __init__(self):
        self.stopped = 0

    async def stop(self):
        self.stopped += 1


def _seed_project(pid: str, sessions: FakeS3Store, root: FakeS3Store) -> _FakeSandbox:
    sb = _FakeSandbox()
    app_module.registry.register(pid)
    app_module.registry.attach(pid, sb)
    sessions.blobs[f"session_{pid}/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs[f"{pid}/project.json"] = "{}"
    root.blobs[f"{pid}/aiplc-docs/audit.md"] = "x"
    return sb


def test_delete_removes_registry_vm_and_s3(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    sb = _seed_project("del-1", sessions, root)

    r = client.delete("/projects/del-1")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert sb.stopped == 1
    assert not any(k.startswith("session_del-1/") for k in sessions.blobs)
    assert not any(k.startswith("del-1/") for k in root.blobs)
    assert not app_module.registry.is_registered("del-1")


def test_delete_unknown_project_404():
    assert client.delete("/projects/no-such").status_code == 404


def test_delete_continues_when_stop_fails(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    sb = _seed_project("del-2", sessions, root)

    async def _boom():
        raise RuntimeError("vm stuck")

    sb.stop = _boom  # type: ignore[assignment]
    r = client.delete("/projects/del-2")
    assert r.status_code == 200  # stop 실패는 삭제를 막지 않는다
    assert not app_module.registry.is_registered("del-2")


def test_delete_returns_500_and_keeps_registry_on_s3_failure(monkeypatch):
    class _ExplodingStore(FakeS3Store):
        async def delete_prefix(self, prefix):
            raise RuntimeError("s3 down")

    sessions, root = _ExplodingStore(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    _seed_project("del-3", sessions, root)

    r = client.delete("/projects/del-3")
    assert r.status_code == 500
    assert app_module.registry.is_registered("del-3")  # 유지 → 재시도 가능


def test_delete_registered_but_unbooted_project(monkeypatch):
    # 복원 직후(sandbox 없음) 상태에서도 삭제 가능해야 한다
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    app_module.registry.register("del-4")
    root.blobs["del-4/project.json"] = "{}"

    r = client.delete("/projects/del-4")
    assert r.status_code == 200
    assert not app_module.registry.is_registered("del-4")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_delete.py -q`
Expected: FAIL — 405 Method Not Allowed (DELETE 라우트 없음)

- [ ] **Step 3: 구현** — `backend/pathfinder/routes/projects.py` 파일 끝에 추가 (import에 `delete_project_data` 추가: `from pathfinder.project_store import write_manifest, delete_project_data`)

```python
@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    """전부 삭제(스펙 결정): VM stop(베스트에포트) → S3 세션+산출물 삭제
    (실패 시 500, 멱등 재시도) → 레지스트리 제거."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    if app_module.registry.has_workspace(pid):
        try:
            await app_module.registry.get(pid).sandbox.stop()
        except Exception:
            _log.exception("sandbox stop failed for %s during delete (continuing)", pid)
    if app_module.durable_projects_enabled():
        try:
            await delete_project_data(app_module.session_s3_factory(),
                                      app_module.projects_root_s3_factory(), pid)
        except Exception:
            _log.exception("S3 delete failed for %s", pid)
            raise HTTPException(status_code=500, detail="project delete failed")
    app_module.registry.remove(pid)
    return {"deleted": True}
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_delete.py -q`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/routes/projects.py backend/tests/test_routes_projects_delete.py
git commit -m "feat(backend): DELETE /projects/{pid} — stop VM, purge S3, deregister"
```

---

### Task 7: lazy sandbox — ensure_workspace + 호출부 전환

**Files:**
- Modify: `backend/pathfinder/routes/deps.py` (전면 교체)
- Modify: `backend/pathfinder/routes/history.py:13`, `answers.py:19`, `uploads.py:10`, `discovery.py:9,15`, `artifacts.py:10,14,18,23,35`, `turns.py:29,35,43,57`
- Test: `backend/tests/test_deps_ensure_workspace.py` (신규)

**Interfaces:**
- Consumes: Task 1 registry API, `app_module.make_sandbox`
- Produces: `async def ensure_workspace(pid: str) -> Workspace` — 살아있으면 반환 / 등록만이면 lock 아래 부팅+attach / 미등록 404 / 부팅 실패 503("project workspace unavailable"). **`get_workspace`(sync)는 제거.**

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_deps_ensure_workspace.py`

```python
# backend/tests/test_deps_ensure_workspace.py
import asyncio
import pytest
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.routes.deps import ensure_workspace


class _FakeSandbox:
    pass


@pytest.mark.asyncio
async def test_unknown_project_404():
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-none")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_live_workspace_returned_without_boot(monkeypatch):
    app_module.registry.register("ew-live")
    ws = app_module.registry.attach("ew-live", _FakeSandbox())

    async def _no_boot(pid):
        raise AssertionError("must not boot")

    monkeypatch.setattr(app_module, "make_sandbox", _no_boot)
    assert await ensure_workspace("ew-live") is ws


@pytest.mark.asyncio
async def test_registered_project_lazy_boots_once_even_concurrently(monkeypatch):
    app_module.registry.register("ew-lazy", name="레이지")
    boots = {"n": 0}

    async def _slow_boot(pid):
        boots["n"] += 1
        await asyncio.sleep(0.02)  # 두 요청이 겹치도록
        return _FakeSandbox()

    monkeypatch.setattr(app_module, "make_sandbox", _slow_boot)
    a, b = await asyncio.gather(ensure_workspace("ew-lazy"), ensure_workspace("ew-lazy"))
    assert a is b            # 같은 Workspace
    assert boots["n"] == 1   # 이중 부팅 없음 (pid별 lock)
    assert app_module.registry.has_workspace("ew-lazy")


@pytest.mark.asyncio
async def test_boot_failure_503_keeps_registration(monkeypatch):
    app_module.registry.register("ew-fail")

    async def _boom(pid):
        raise RuntimeError("boot failed")

    monkeypatch.setattr(app_module, "make_sandbox", _boom)
    with pytest.raises(HTTPException) as e:
        await ensure_workspace("ew-fail")
    assert e.value.status_code == 503
    assert app_module.registry.is_registered("ew-fail")   # 다음 요청이 재시도
    assert not app_module.registry.has_workspace("ew-fail")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deps_ensure_workspace.py -q`
Expected: FAIL — `ImportError: cannot import name 'ensure_workspace'`

- [ ] **Step 3: deps.py 교체** — `backend/pathfinder/routes/deps.py` 전체:

```python
# backend/pathfinder/routes/deps.py
import asyncio
import logging
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.workspace import Workspace

_log = logging.getLogger(__name__)
# pid별 부팅 lock — 복원 직후 동시 요청 2건이 VM을 두 번 띄우는 것을 막는다.
_boot_locks: dict[str, asyncio.Lock] = {}


async def ensure_workspace(pid: str) -> Workspace:
    """살아있는 워크스페이스를 반환하고, 복원-등록만 된 프로젝트면 이 자리에서
    sandbox를 lazy 부팅한다(스펙: 복원 시점 = 첫 접근). 미등록 404, 부팅 실패
    503(등록은 유지 — 다음 요청이 재시도)."""
    try:
        return app_module.registry.get(pid)
    except KeyError:
        pass
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    lock = _boot_locks.setdefault(pid, asyncio.Lock())
    async with lock:
        try:
            return app_module.registry.get(pid)  # double-check: 앞선 요청이 이미 부팅
        except KeyError:
            pass
        try:
            sandbox = await app_module.make_sandbox(pid)
        except Exception:
            _log.exception("lazy sandbox boot failed for %s", pid)
            raise HTTPException(status_code=503, detail="project workspace unavailable")
        return app_module.registry.attach(pid, sandbox)
```

- [ ] **Step 4: 호출부 전환 (6개 파일, 기계적)**

각 파일에서 `from pathfinder.routes.deps import get_workspace` → `from pathfinder.routes.deps import ensure_workspace`로 바꾸고:

`history.py:13`:
```python
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
```

`answers.py:19`:
```python
        return await (await ensure_workspace(pid)).put_answers(name, answers)
```

`uploads.py:10`:
```python
    ws = await ensure_workspace(pid)
```

`discovery.py:9,15`:
```python
    paths = await (await ensure_workspace(pid)).list_question_files()
    # …
    paths = await (await ensure_workspace(pid)).list_artifacts()
```

`artifacts.py:10,14,18,23,35` — 한 줄 체이닝이 어색한 곳은 2줄로:
```python
    return await (await ensure_workspace(pid)).get_state()
    # …
    return await (await ensure_workspace(pid)).get_audit()
    # …
    return {"markdown": await (await ensure_workspace(pid)).get_document()}
    # …(questions)
        return await (await ensure_workspace(pid)).get_questions(name)
    # …(files)
        ws = await ensure_workspace(pid)
        content = await ws.sandbox.read_file(path)
```

`turns.py:29,35,43,57` — 4곳 모두:
```python
    ws = await ensure_workspace(pid)
```

- [ ] **Step 5: 전체 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS (기존 라우트 테스트는 POST로 생성한 프로젝트를 쓰므로 fast path로 동일 동작)

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/routes/
git add backend/tests/test_deps_ensure_workspace.py
git commit -m "feat(backend): lazy sandbox boot via ensure_workspace — restored projects come alive on first request"
```

---

### Task 8: 프론트 — deleteProject API + 카드 삭제 버튼/확인 다이얼로그

**Files:**
- Modify: `frontend/lib/api/client.ts` (deleteProject 추가)
- Modify: `frontend/components/ProjectList.tsx` (삭제 UI)
- Modify: `frontend/app/page.tsx` (`onDeleted={reload}` 배선)
- Test: `frontend/components/ProjectList.test.tsx` (신규)

**Interfaces:**
- Consumes: Task 6의 `DELETE /projects/{pid}` (200 `{"deleted": true}`)
- Produces: `deleteProject(pid: string): Promise<void>`; `ProjectList` props `{ projects, onDeleted: () => void }`

- [ ] **Step 1: 실패 테스트 작성** — `frontend/components/ProjectList.test.tsx`

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ProjectList } from "./ProjectList";

const PROJECTS = [
  { project_id: "p1", name: "워크숍 A" },
  { project_id: "p2", name: null },
];

describe("ProjectList delete", () => {
  it("shows a delete button per card and opens the confirm dialog with the warning copy", async () => {
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: /프로젝트 삭제/ });
    expect(buttons).toHaveLength(2);

    await userEvent.setup().click(buttons[0]);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("'워크숍 A' 프로젝트 삭제");
    expect(dialog).toHaveTextContent(
      "채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.",
    );
  });

  it("cancel closes the dialog without calling DELETE", async () => {
    let called = 0;
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () => {
        called++;
        return HttpResponse.json({ deleted: true });
      }),
    );
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(called).toBe(0);
  });

  it("confirm calls DELETE and then onDeleted", async () => {
    let called = 0;
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () => {
        called++;
        return HttpResponse.json({ deleted: true });
      }),
    );
    const onDeleted = vi.fn();
    render(<ProjectList projects={PROJECTS} onDeleted={onDeleted} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "삭제" }));
    expect(called).toBe(1);
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows an error and keeps the dialog when DELETE fails", async () => {
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () =>
        HttpResponse.json({ detail: "project delete failed" }, { status: 500 }),
      ),
    );
    const onDeleted = vi.fn();
    render(<ProjectList projects={PROJECTS} onDeleted={onDeleted} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByText(/삭제에 실패했습니다/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("delete button does not navigate the card link", async () => {
    // 버튼은 Link 밖에 있으므로 클릭이 내비게이션(링크 href 이동)을 유발하지
    // 않아야 한다 — jsdom에서는 dialog가 열렸고 링크 클릭 핸들러가 없음을 확인.
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
    await userEvent.setup().click(
      screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0],
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run components/ProjectList.test.tsx`
Expected: FAIL — `프로젝트 삭제` 버튼 없음 (그리고 onDeleted prop 타입 에러)

- [ ] **Step 3: client.ts에 deleteProject 추가** — `listProjects` 아래:

```ts
export async function deleteProject(projectId: string): Promise<void> {
  await request<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
}
```

- [ ] **Step 4: ProjectList.tsx 교체** — 전체:

```tsx
"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ProjectSummary } from "@/lib/api/types";
import { deleteProject } from "@/lib/api/client";

export function ProjectList({
  projects,
  onDeleted,
}: {
  projects: ProjectSummary[];
  onDeleted: () => void;
}) {
  // 삭제 확인 다이얼로그 대상 (null = 닫힘)
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape로 닫기 — 워크스페이스 bottom-sheet와 동일한 최소 접근성 패턴
  useEffect(() => {
    if (!target) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) setTarget(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [target, busy]);

  async function confirmDelete() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await deleteProject(target.project_id);
      setTarget(null);
      onDeleted();
    } catch {
      setError("삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  if (projects.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }
  return (
    <>
      <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map((p) => (
          <li key={p.project_id} className="relative">
            <Link
              href={`/projects/${p.project_id}/dashboard`}
              className="block bg-white rounded-xl border border-slate-200 p-5 hover:border-violet-300 hover:shadow-sm transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 rounded-lg bg-violet-100 text-violet-700 flex items-center justify-center text-sm font-bold">
                  🟣
                </span>
                <p className="font-bold truncate pr-8">{p.name ?? p.project_id}</p>
              </div>
              <p className="text-xs text-slate-400 mt-2">ID: {p.project_id}</p>
            </Link>
            {/* Link 밖(li 안) absolute 배치 — 카드 내비게이션과 클릭 충돌 방지 */}
            <button
              type="button"
              aria-label={`${p.name ?? p.project_id} 프로젝트 삭제`}
              onClick={() => {
                setError(null);
                setTarget(p);
              }}
              className="absolute top-3 right-3 w-8 h-8 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 flex items-center justify-center"
            >
              🗑
            </button>
          </li>
        ))}
      </ul>

      {target && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => !busy && setTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="프로젝트 삭제 확인"
            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">
              &apos;{target.name ?? target.project_id}&apos; 프로젝트 삭제
            </h2>
            <p className="text-sm text-slate-600 mt-2">
              채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.
            </p>
            {error && <p className="text-sm text-rose-600 mt-3">{error}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setTarget(null)}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50"
              >
                삭제
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 5: page.tsx 배선** — `frontend/app/page.tsx`의 `{data && <ProjectList projects={data} />}`를:

```tsx
        {data && <ProjectList projects={data} onDeleted={reload} />}
```

- [ ] **Step 6: 통과 확인 (신규 + 전체)**

Run: `cd frontend && npx vitest run components/ProjectList.test.tsx && npx vitest run`
Expected: 신규 5 passed, 전체 PASS (app/page.test.tsx가 ProjectList를 쓰면 onDeleted 누락 컴파일 에러가 없는지 확인 — page.tsx 경유라 자동 해결)

- [ ] **Step 7: 커밋**

```bash
git add frontend/lib/api/client.ts frontend/components/ProjectList.tsx frontend/components/ProjectList.test.tsx frontend/app/page.tsx
git commit -m "feat(frontend): project delete — card trash button + irreversible-delete confirm dialog"
```

---

### Task 9: 최종 검증 (전체 스위트 + 스펙 대조)

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 2: 프론트 전체**

Run: `cd frontend && npx vitest run`
Expected: 전부 PASS

- [ ] **Step 3: 스펙 에러표 대조** — 스펙의 "에러 처리 표" 6행 각각에 대응 테스트가 존재하는지 확인:
기동 복원 실패(`test_lifespan_survives_restore_failure`), 매니페스트 put 실패(`test_create_fails_500_when_manifest_write_fails`), lazy 부팅 실패(`test_boot_failure_503_keeps_registration`), stop 실패(`test_delete_continues_when_stop_fails`), S3 삭제 실패(`test_delete_returns_500_and_keeps_registry_on_s3_failure`), 미등록 DELETE(`test_delete_unknown_project_404`).
