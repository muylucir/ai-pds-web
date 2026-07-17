# Pathfinder API Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three list/discovery endpoints the frontend (`files/ui/01-04`) needs but Phase 1 didn't build: `GET /projects` (project listing), `GET /projects/{pid}/questions` (question-file listing), and `GET /projects/{pid}/artifacts` (artifact-file listing).

**Architecture:** These are pure read/listing endpoints layered on top of the Phase 1 `Workspace` facade and `ProjectRegistry`. `ProjectRegistry` gains an in-memory name field and a `list_ids()` method (backward-compatible — existing `create(pid, sandbox)` calls keep working via a default). `Workspace` gains `list_artifacts()`, implemented the same way as the existing `list_question_files()` — a `sandbox.list_files()` glob call, safe by construction because the sandbox already rejects `..` and absolute paths. No new abstractions, no new files outside `routes/` plus two small additions to `workspace.py`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest, FastAPI `TestClient`. Same stack as Phase 1 — no new dependencies.

## Global Constraints

- Python version floor: 3.11; use `str | None` union syntax (matches Phase 1's `pathfinder/models.py` and `workspace.py`).
- The backend contains **no methodology logic** — these are pure listing endpoints; they enumerate files and return paths/names, nothing more.
- Client-supplied paths/globs never escape the workspace root. The sandbox guard (`LocalSandbox._resolve` / `LocalSandbox.list_files`, which reject leading `/` and any `..` path segment) already enforces this; new `Workspace` methods call `sandbox.list_files()` and must not bypass or duplicate that guard.
- Reuse `get_workspace(pid)` from `backend/pathfinder/routes/deps.py` for 404 handling — do not re-define it in the new route module.
- New routers are registered in `backend/pathfinder/app.py` via `app.include_router(...)`, following the exact pattern already used for `projects`, `artifacts`, `answers`, and `turns`.
- Tests use FastAPI `TestClient`, `asyncio.run(...)` for async seed helpers (not `asyncio.get_event_loop().run_until_complete`, which is deprecated on 3.11 and fights pytest-asyncio's managed loop — see `backend/tests/conftest.py`), and a distinct `project_id` per test to avoid cross-test interference in the shared module-level `registry`.
- Project metadata (`name`) is intentionally minimal and in-memory in this plan. Durable project metadata storage (DynamoDB, created-at timestamps, ownership, etc.) is explicitly **out of scope** — it belongs to the later MicroVM/prod plan. This plan only adds an optional in-memory `name` string alongside the existing in-memory `Workspace`.

---

## File Structure

```
backend/
  pathfinder/
    workspace.py                # MODIFY: ProjectRegistry gains list_ids() + optional name;
                                 #         Workspace gains list_artifacts()
    routes/
      projects.py                # MODIFY: POST /projects accepts optional `name`;
                                  #         add GET /projects (listing)
      discovery.py                # CREATE: GET /projects/{pid}/questions,
                                  #         GET /projects/{pid}/artifacts
    app.py                       # MODIFY: register discovery.router
  tests/
    test_routes_projects_list.py # CREATE: tests for GET/POST /projects listing + name
    test_workspace_artifacts.py  # CREATE: tests for Workspace.list_artifacts()
    test_routes_discovery.py     # CREATE: tests for GET /projects/{pid}/questions and /artifacts
```

Rationale: this plan touches exactly two existing files (`workspace.py`, `projects.py`, `app.py` — the third is a one-line router registration) and adds one new route module. `list_question_files` and the new `list_artifacts` are both thin listing helpers on `Workspace`, so they belong together in `workspace.py` rather than a new file. The two *new* listing routes (`/questions`, `/artifacts`) are grouped in a new `routes/discovery.py` rather than added to `routes/artifacts.py`, because `artifacts.py` currently holds single-resource GETs (`state`, `audit`, `document`, one question file) while these two are collection listings for a different UI surface (dashboard project switcher + 산출물 panel); keeping them separate avoids conflating "read one artifact's content" with "list what exists." `POST /projects` and the new `GET /projects` naturally stay together in `projects.py` since both operate on the registry itself, not a single project's workspace.

---

### Task 1: ProjectRegistry — list_ids() and optional project name

**Files:**
- Modify: `backend/pathfinder/workspace.py`
- Test: `backend/tests/test_workspace.py` (append)

**Interfaces:**
- Consumes: nothing new (uses the existing `Workspace` class from Phase 1).
- Produces:
  - `ProjectRegistry.create(project_id: str, sandbox: Sandbox, name: str | None = None) -> Workspace` — `name` is optional and defaults to `None`, so every existing Phase 1 call site (`create(pid, sandbox)`, no `name` arg) keeps working unmodified.
  - `ProjectRegistry.list_ids() -> list[str]` — returns project ids in insertion order.
  - `ProjectRegistry.get_name(project_id: str) -> str | None` — returns the stored name (or `None`), raises `KeyError` if the project id is unknown (consistent with the existing `get()` behavior).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_workspace.py

def test_registry_list_ids_preserves_insertion_order(tmp_path):
    reg = ProjectRegistry()
    for pid in ("p1", "p2", "p3"):
        sb = LocalSandbox(root=tmp_path / pid)
        import asyncio
        asyncio.run(sb.start())
        reg.create(pid, sb)
    assert reg.list_ids() == ["p1", "p2", "p3"]

def test_registry_create_without_name_defaults_to_none(tmp_path):
    # Backward-compat: existing Phase 1 call sites pass no `name` at all.
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path)
    import asyncio
    asyncio.run(sb.start())
    reg.create("p-noname", sb)
    assert reg.get_name("p-noname") is None

def test_registry_create_with_name_stores_it(tmp_path):
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path)
    import asyncio
    asyncio.run(sb.start())
    reg.create("p-named", sb, name="기획전 AI 어시스턴트")
    assert reg.get_name("p-named") == "기획전 AI 어시스턴트"

def test_registry_get_name_unknown_project_raises_keyerror():
    reg = ProjectRegistry()
    try:
        reg.get_name("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workspace.py -v -k "list_ids or create_without_name or create_with_name or get_name_unknown"`
Expected: FAIL with `AttributeError: 'ProjectRegistry' object has no attribute 'list_ids'` (and similar for `get_name`, and a `TypeError` on the `name=` kwarg).

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/workspace.py
# Replace the existing ProjectRegistry class with this version.
class ProjectRegistry:
    def __init__(self):
        self._projects: dict[str, Workspace] = {}
        self._names: dict[str, str | None] = {}

    def create(self, project_id: str, sandbox: Sandbox, name: str | None = None) -> Workspace:
        ws = Workspace(sandbox)
        self._projects[project_id] = ws
        self._names[project_id] = name
        return ws

    def get(self, project_id: str) -> Workspace:
        return self._projects[project_id]

    def list_ids(self) -> list[str]:
        # dict preserves insertion order in Python 3.7+; this mirrors that
        # order rather than sorting, so newest-created projects are easy to
        # find at the tail — no requirement in the spec calls for sorting.
        return list(self._projects.keys())

    def get_name(self, project_id: str) -> str | None:
        if project_id not in self._projects:
            raise KeyError(project_id)
        return self._names[project_id]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workspace.py -v`
Expected: PASS (9 tests — 5 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/workspace.py backend/tests/test_workspace.py
git commit -m "feat: ProjectRegistry gains list_ids() and optional project name"
```

---

### Task 2: Workspace.list_artifacts()

**Files:**
- Modify: `backend/pathfinder/workspace.py`
- Test: `backend/tests/test_workspace_artifacts.py`

**Interfaces:**
- Consumes: `Sandbox.list_files(glob: str) -> list[str]` (Phase 1, `pathfinder/sandbox/base.py`); the existing `list_question_files()` pattern in the same class.
- Produces: `Workspace.list_artifacts() -> list[str]` — returns every file path under `aiplc-docs/` (recursively), regardless of extension, relative to the workspace root, e.g. `"aiplc-docs/audit.md"`, `"aiplc-docs/discovery/discovery-document.md"`, `"aiplc-docs/discovery/prototype/prototype-spec.md"`.

Design decision — **"artifact" = every file under `aiplc-docs/`, not just `*.md`.** The dashboard's 산출물 (artifacts) panel (`files/ui/01-dashboard.html`) lists a "프로토타입 프리뷰" (prototype preview) tile alongside `.md` files, and Phase 1's own file-as-contract model treats everything the methodology writes under `aiplc-docs/` as project output — restricting to `*.md` would silently drop non-markdown artifacts (e.g. exported JSON, HTML preview snapshots) a later stage might write. Using the whole subtree keeps this endpoint a dumb lister with no methodology-specific filtering, matching the "no methodology logic" global constraint. Question files (`*-questions.md`) are already listed separately by `GET /projects/{pid}/questions` (Task 3) but are not excluded here — a question file *is* a file under `aiplc-docs/`, and de-duplicating "is this an artifact or a question" would require the kind of content-aware judgment this backend deliberately avoids.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_workspace_artifacts.py
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.workspace import Workspace

async def test_list_artifacts_finds_nested_and_top_level_files(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "a")
    await sb.write_file("aiplc-docs/aiplc-state.md", "b")
    await sb.write_file("aiplc-docs/discovery/discovery-document.md", "c")
    await sb.write_file("aiplc-docs/discovery/prototype/prototype-spec.md", "d")
    ws = Workspace(sb)
    found = sorted(await ws.list_artifacts())
    assert found == [
        "aiplc-docs/aiplc-state.md",
        "aiplc-docs/audit.md",
        "aiplc-docs/discovery/discovery-document.md",
        "aiplc-docs/discovery/prototype/prototype-spec.md",
    ]

async def test_list_artifacts_includes_non_markdown_files(tmp_path: Path):
    # "artifact" = every file under aiplc-docs/, not just *.md — a later stage
    # may write non-markdown output (e.g. exported JSON); the lister must not
    # silently drop it.
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/discovery/prototype/preview-snapshot.json", "{}")
    ws = Workspace(sb)
    found = await ws.list_artifacts()
    assert "aiplc-docs/discovery/prototype/preview-snapshot.json" in found

async def test_list_artifacts_empty_workspace_returns_empty_list(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    ws = Workspace(sb)
    assert await ws.list_artifacts() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workspace_artifacts.py -v`
Expected: FAIL with `AttributeError: 'Workspace' object has no attribute 'list_artifacts'`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/workspace.py
# Add this method to the existing Workspace class, alongside list_question_files.
    async def list_artifacts(self) -> list[str]:
        # "Artifact" = every file under aiplc-docs/ (see Task 2 design note in
        # the plan): the dashboard's 산출물 panel and Phase 1's file-as-contract
        # model both treat the whole aiplc-docs/ subtree as project output, not
        # just *.md. Glob mirrors list_question_files's use of sandbox.list_files
        # (same traversal guard, no new IO path).
        return await self.sandbox.list_files("aiplc-docs/**/*")
```

Note: `sandbox.list_files` (via `LocalSandbox.list_files` → `Path.glob`) already filters to `p.is_file()`, so directories matched by `**` are excluded without any extra code here — this mirrors exactly how `list_question_files`'s `"aiplc-docs/**/*-questions.md"` glob works today.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workspace_artifacts.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/workspace.py backend/tests/test_workspace_artifacts.py
git commit -m "feat: Workspace.list_artifacts() lists files under aiplc-docs/"
```

---

### Task 3: GET /projects/{pid}/questions and GET /projects/{pid}/artifacts

**Files:**
- Create: `backend/pathfinder/routes/discovery.py`
- Modify: `backend/pathfinder/app.py` (register router)
- Test: `backend/tests/test_routes_discovery.py`

**Interfaces:**
- Consumes: `get_workspace(pid)` from `backend/pathfinder/routes/deps.py` (Phase 1); `Workspace.list_question_files()` (Phase 1); `Workspace.list_artifacts()` (Task 2).
- Produces:
  - `GET /projects/{pid}/questions` → `{"questions": list[str]}` — question-file paths in the workspace. 404 (via `get_workspace`) if `pid` is unknown.
  - `GET /projects/{pid}/artifacts` → `{"artifacts": list[str]}` — artifact-file paths under `aiplc-docs/`. 404 if `pid` is unknown.

These are new path segments (`/questions` with no trailing name, `/artifacts`) that do not collide with the existing `GET /projects/{pid}/questions/{name:path}` route in `routes/artifacts.py` — FastAPI/Starlette route matching is exact-segment-count first, so `/projects/p1/questions` matches only the no-argument route and `/projects/p1/questions/aiplc-docs/x.md` matches only the `{name:path}` route, regardless of which router is included first.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_routes_discovery.py
import asyncio
from fastapi.testclient import TestClient
from pathfinder.app import app, registry

client = TestClient(app)

def _seed(pid):
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    # asyncio.run (not get_event_loop().run_until_complete) — matches the
    # style already used in test_routes_answers.py / test_routes_turns.py.
    async def seed():
        await ws.sandbox.write_file("aiplc-docs/discovery-mode-selection-questions.md", "x")
        await ws.sandbox.write_file("aiplc-docs/discovery/product-strategy/strategy-questions.md", "y")
        await ws.sandbox.write_file("aiplc-docs/audit.md", "z")
        await ws.sandbox.write_file("aiplc-docs/discovery/discovery-document.md", "w")
    asyncio.run(seed())

def test_list_questions_route():
    _seed("disc-q1")
    r = client.get("/projects/disc-q1/questions")
    assert r.status_code == 200
    assert sorted(r.json()["questions"]) == [
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]

def test_list_artifacts_route():
    _seed("disc-a1")
    r = client.get("/projects/disc-a1/artifacts")
    assert r.status_code == 200
    assert sorted(r.json()["artifacts"]) == [
        "aiplc-docs/audit.md",
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/discovery-document.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]

def test_list_questions_unknown_project_404():
    r = client.get("/projects/nope-disc/questions")
    assert r.status_code == 404

def test_list_artifacts_unknown_project_404():
    r = client.get("/projects/nope-disc2/artifacts")
    assert r.status_code == 404

def test_list_questions_route_does_not_collide_with_single_question_route():
    # /projects/{pid}/questions/{name:path} (routes/artifacts.py, Phase 1) must
    # keep working once the no-argument /projects/{pid}/questions route exists.
    _seed("disc-collide")
    r = client.get(
        "/projects/disc-collide/questions/aiplc-docs/discovery-mode-selection-questions.md"
    )
    assert r.status_code == 200
    assert r.json()["parse_ok"] is False  # seeded content "x" is not valid question markdown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.routes.discovery'` (import error surfaces as a collection error for this test file).

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/routes/discovery.py
from fastapi import APIRouter
from pathfinder.routes.deps import get_workspace

router = APIRouter()

@router.get("/projects/{pid}/questions")
async def list_questions(pid: str):
    paths = await get_workspace(pid).list_question_files()
    return {"questions": paths}

@router.get("/projects/{pid}/artifacts")
async def list_artifacts(pid: str):
    paths = await get_workspace(pid).list_artifacts()
    return {"artifacts": paths}
```

```python
# add to backend/pathfinder/app.py, after the existing turns include
from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_discovery.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/routes/discovery.py backend/pathfinder/app.py backend/tests/test_routes_discovery.py
git commit -m "feat: GET /projects/{pid}/questions and /artifacts listing routes"
```

---

### Task 4: GET /projects and POST /projects optional name

**Files:**
- Modify: `backend/pathfinder/routes/projects.py`
- Test: `backend/tests/test_routes_projects_list.py`

**Interfaces:**
- Consumes: `ProjectRegistry.list_ids()`, `ProjectRegistry.get_name(pid)` (Task 1); `ProjectRegistry.create(project_id, sandbox, name=None)` (Task 1, backward-compatible signature).
- Produces:
  - `POST /projects` body `{"project_id": str, "name": str | None}` (`name` optional, defaults to `None` if omitted) → `{"project_id": str, "name": str | None}`. 409 if `project_id` already exists (unchanged from Phase 1).
  - `GET /projects` → `{"projects": [{"project_id": str, "name": str | None}, ...]}`, in the order `ProjectRegistry.list_ids()` returns them.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_routes_projects_list.py
from fastapi.testclient import TestClient
from pathfinder.app import app

client = TestClient(app)

def test_create_project_without_name_returns_null_name():
    r = client.post("/projects", json={"project_id": "plist-noname"})
    assert r.status_code == 200
    assert r.json() == {"project_id": "plist-noname", "name": None}

def test_create_project_with_name_returns_it():
    r = client.post("/projects", json={"project_id": "plist-named", "name": "기획전 AI 어시스턴트"})
    assert r.status_code == 200
    assert r.json() == {"project_id": "plist-named", "name": "기획전 AI 어시스턴트"}

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_projects_list.py -v`
Expected: FAIL — `test_create_project_with_name_returns_it` fails because `POST /projects` doesn't accept/return `name` yet (`assert r.json() == {"project_id": ..., "name": ...}` fails since the current response body is `{"project_id": ...}` only); `test_list_projects_*` fail with 404/405 since `GET /projects` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/routes/projects.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module

router = APIRouter()

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None

@router.post("/projects")
async def create_project(body: CreateProject):
    try:
        app_module.registry.get(body.project_id)
        raise HTTPException(status_code=409, detail="project exists")
    except KeyError:
        pass
    sandbox = await app_module.make_sandbox(body.project_id)
    app_module.registry.create(body.project_id, sandbox, name=body.name)
    return {"project_id": body.project_id, "name": body.name}

@router.get("/projects")
async def list_projects():
    # Minimal, in-memory listing only — no created-at/ownership/rich metadata.
    # Durable project metadata (DynamoDB) is a later MicroVM/prod concern, not
    # this backend-completion plan.
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid)}
            for pid in app_module.registry.list_ids()
        ]
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_projects_list.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all tests — 54 from Phase 1 + 4 (Task 1) + 3 (Task 2) + 5 (Task 3) + 4 (Task 4) = 70 total)

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/routes/projects.py backend/tests/test_routes_projects_list.py
git commit -m "feat: GET /projects listing and optional project name on create"
```

---

## Self-Review

**Spec coverage (this plan's three endpoints):**
- `GET /projects` (list of existing projects) → Task 4 (`list_projects`), backed by `ProjectRegistry.list_ids()` (Task 1).
- `POST /projects` optional `name`, stored + returned in listing → Task 1 (`ProjectRegistry.create(..., name=None)`, `get_name`) + Task 4 (`CreateProject.name`, response body, `list_projects` output).
- `GET /projects/{pid}/questions` (question-file paths via existing `Workspace.list_question_files()`) → Task 3, 404 via `get_workspace`.
- `GET /projects/{pid}/artifacts` (artifact-file paths under `aiplc-docs/`, via new `Workspace.list_artifacts()`) → Task 2 (method) + Task 3 (route), 404 via `get_workspace`.
- "Decide and document what counts as an artifact" → done in Task 2's design-decision note (all files under `aiplc-docs/`, not just `*.md`; justified against the dashboard's 산출물 panel and the "no methodology logic" constraint).
- "ProjectRegistry gaining a listing method + optional name storage is backward-compatible" → Task 1's `create(project_id, sandbox, name=None)` keeps the two-positional-arg call shape working; explicitly tested (`test_registry_create_without_name_defaults_to_none`).
- "Reuse get_workspace from routes/deps.py; register new routers in app.py" → Task 3 imports `get_workspace` rather than redefining it; both new routers (`discovery.router` in Task 3, no new router needed in Task 4 since it modifies the existing `projects.router`) are wired into `app.py`/the existing router respectively.
- "Rich metadata + DynamoDB is a LATER MicroVM/prod plan" → stated in Global Constraints and repeated inline in Task 4's `list_projects` docstring-comment.
- Route-collision risk between `GET /projects/{pid}/questions` (new) and `GET /projects/{pid}/questions/{name:path}` (Phase 1, `routes/artifacts.py`) → checked empirically (both routes coexist correctly regardless of path-segment count) and pinned by `test_list_questions_route_does_not_collide_with_single_question_route` in Task 3.

**Placeholder scan:** No TBD/TODO/"handle appropriately" strings; every step shows complete code; no "similar to Task N" references — Task 2 and 3 explicitly restate the `list_files`/glob mechanics rather than pointing back at Phase 1's `list_question_files`.

**Type consistency check across tasks:**
- `ProjectRegistry.create(project_id: str, sandbox: Sandbox, name: str | None = None) -> Workspace` (Task 1) is called with the 2-arg form in all pre-existing Phase 1 tests/routes (unaffected) and with `name=body.name` in Task 4's `create_project` — consistent.
- `ProjectRegistry.list_ids() -> list[str]` (Task 1) is consumed by `list_projects()` in Task 4 — consistent.
- `ProjectRegistry.get_name(project_id: str) -> str | None` (Task 1, raises `KeyError` if unknown) is consumed by `list_projects()` in Task 4, called only with ids already returned by `list_ids()` so the `KeyError` path is never hit there — consistent, no unhandled-exception risk introduced.
- `Workspace.list_artifacts() -> list[str]` (Task 2) is consumed by `list_artifacts()` route in Task 3 — consistent with the sibling `Workspace.list_question_files() -> list[str]` (Phase 1) it's modeled on.
- `get_workspace(pid: str) -> Workspace` (Phase 1, `routes/deps.py`, raises `HTTPException(404)`) is imported unmodified into `routes/discovery.py` in Task 3 — no redefinition, matching the constraint.
- Response shapes `{"questions": [...]}` and `{"artifacts": [...]}` (Task 3) and `{"projects": [{"project_id", "name"}, ...]}` (Task 4) are each defined once and consumed only by their own tests — no cross-task shape drift.

**Scope check:** Exactly three endpoints across four tasks (one endpoint — `GET /projects` — is split from the `POST /projects` name change only because they land in the same task; that's a single task, not scope creep). No MicroVM, no DynamoDB, no frontend code, no new sandbox methods beyond the one glob call `list_artifacts()` needed. Single-plan-sized: 4 tasks, ~16 net new tests, two modified files + one new route file.
