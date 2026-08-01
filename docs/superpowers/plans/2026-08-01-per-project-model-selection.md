# 프로젝트별 AI 모델 선택 + 관리자 모델 카탈로그 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로젝트 생성 시 AI 모델을 고르고 그 값이 그 프로젝트의 Discovery·프로토타입 빌드·설문 생성 전부에 주입되게 하며, 보여줄 모델 목록을 관리자 화면에서 추가·삭제할 수 있게 한다.

**Architecture:** 모델 카탈로그를 S3 버킷 루트의 `models/catalog.json`에 두고(파일 없으면 코드의 시드 4개로 폴백), 프로젝트는 선택값을 `project.json`에 **복사**한다(카탈로그 삭제가 진행 중 프로젝트의 모델을 앗아가지 않게). 백엔드는 `app.project_model(pid)` 하나로 프로젝트 → env → None 순서로 해석하고, 모델 env를 읽던 세 지점이 전부 그것을 쓴다. IAM은 명시 목록 대신 `claude-*` 와일드카드로 넓혀, 관리자가 새 모델을 등록하면 CDK 배포 없이 바로 호출된다.

**Tech Stack:** FastAPI + Pydantic (backend), Next.js 15 App Router + Vitest/MSW (frontend), AWS CDK (TypeScript, infra), pytest-asyncio.

## Global Constraints

- **스펙:** `docs/superpowers/specs/2026-08-01-per-project-model-selection-design.md` — 이 계획의 모든 결정의 출처.
- **비ASCII 문자열은 리터럴 UTF-8로 작성한다.** `\uXXXX` 유니코드 이스케이프 금지(전역 CLAUDE.md).
- **시드 모델 4개** (이름 - 모델 ID, 이 순서 그대로):
  - `Opus 5` - `global.anthropic.claude-opus-5`
  - `Opus 4.6` - `global.anthropic.claude-opus-4-6-v1`
  - `Sonnet 5` - `global.anthropic.claude-sonnet-5`
  - `Sonnet 4.6` - `global.anthropic.claude-sonnet-4-6`
- **표시 상한은 5개.** 등록은 무제한. 상한은 `display: true`의 개수에만 적용된다.
- **`MODEL = 'global.anthropic.claude-opus-4-8'`은 바꾸지 않는다.** 시드 목록에 없지만 구 프로젝트와 미지정 폴백으로 남는다.
- **카탈로그 시드는 읽기 폴백일 뿐 파일로 쓰지 않는다.** 관리자가 처음 수정할 때 비로소 파일이 생긴다.
- **테스트 명령:** 백엔드 `cd backend && .venv/bin/python -m pytest -q`, 프론트 `cd frontend && npm test`, 인프라 `cd infra && npm test`.
- **커밋 메시지는 한국어**로 쓰고 마지막 줄에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`를 넣는다.
- **`StrandsDriver`는 범위 밖** — `PATHFINDER_DISCOVERY_DRIVER=strands` 폴백은 프로젝트별 모델을 무시하고 env 기본값을 쓴다. Task 3이 그 제약을 주석으로 기록한다.

---

## File Structure

**Backend (신규)**
- `backend/pathfinder/model_catalog.py` — 카탈로그 도메인: 시드 목록, `ModelEntry`, 로드/저장, 표시 상한 검증. S3 접근은 주입받은 `S3StoreLike`로만.
- `backend/pathfinder/routes/models.py` — `GET /models`(일반) + `/admin/models*`(관리자) 두 라우터.
- `backend/tests/test_model_catalog.py`, `backend/tests/test_routes_models.py`

**Backend (수정)**
- `backend/pathfinder/app.py` — `models_root_s3_factory()`, `model_catalog_store()`, `project_model(pid)` 추가; 모델 주입 3지점 교체; `questionnaire_agent_factory(project_id)` 시그니처 변경; 새 라우터 등록.
- `backend/pathfinder/project_store.py` — `write_manifest(..., model_id=)`, `restore_projects` → 4-tuple.
- `backend/pathfinder/workspace.py` — `ProjectRegistry._model_id`, `register(..., model_id=)`, `get_model_id(pid)`.
- `backend/pathfinder/routes/projects.py` — `CreateProject.model_id`, 검증, `GET /projects/{pid}` 신설, 목록 응답에 `model_id`.
- `backend/pathfinder/routes/surveys.py` — `questionnaire_agent_factory(pid)` 호출.
- `backend/pathfinder/agent/driver.py` — StrandsDriver 범위 제외 주석.

**Infra (수정)**
- `infra/lib/backend-permissions.ts` — `INVOKABLE_MODELS` → 와일드카드.
- `infra/test/hosting-stack.assert.ts` — 5개 단정 → 와일드카드 + `MODEL` 포함 단정.

**Frontend (신규)**
- `frontend/lib/api/models.ts` — `/models` + `/admin/models*` 클라이언트.
- `frontend/components/admin/ModelTable.tsx`, `frontend/components/admin/AddModelModal.tsx` (+ 각 `.test.tsx`)
- `frontend/app/admin/models/page.tsx`

**Frontend (수정)**
- `frontend/lib/api/types.ts` — `ProjectSummary.model_id`, `ProjectDetail`.
- `frontend/lib/api/client.ts` — `createProject(...modelId)`, `getProject(pid)`.
- `frontend/components/CreateProjectForm.tsx` — 모델 셀렉트.
- `frontend/components/AppHeader.tsx` — 모델 배지.
- `frontend/components/UserMenu.tsx` — 관리자 메뉴에 "모델 관리".
- `frontend/test/msw/handlers.ts` — `/models` 기본 핸들러.
- 4개 화면(`workspace`/`dashboard`/`review`/`prototypes`)에 배지 배선.

---

### Task 1: 모델 카탈로그 도메인

**Files:**
- Create: `backend/pathfinder/model_catalog.py`
- Test: `backend/tests/test_model_catalog.py`

**Interfaces:**
- Consumes: `pathfinder.s3store.S3StoreLike` (기존 Protocol), `tests.fakes.in_memory_s3.FakeS3Store` (기존 fake).
- Produces:
  - `SEED_MODELS: tuple[ModelEntry, ...]` — 시드 4개
  - `MAX_DISPLAYED: int = 5`
  - `CATALOG_KEY: str = "models/catalog.json"`
  - `class ModelEntry(BaseModel)`: `name: str`, `model_id: str`, `display: bool = True`
  - `class CatalogError(Exception)`: `.code: str` — `"duplicate"` | `"too_many_displayed"` | `"not_found"` | `"readonly"`
  - `class ModelCatalog`: `__init__(self, s3: S3StoreLike | None)`; `async load() -> list[ModelEntry]`; `async displayed() -> list[ModelEntry]`; `async add(name, model_id, display) -> ModelEntry`; `async update(model_id, *, name=None, display=None) -> ModelEntry`; `async remove(model_id) -> None`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_model_catalog.py`:

```python
# backend/tests/test_model_catalog.py
#
# 카탈로그 도메인만 시험한다 — S3는 FakeS3Store, 라우트는 test_routes_models.py.
from __future__ import annotations

import json

import pytest

from pathfinder.model_catalog import (
    CATALOG_KEY, MAX_DISPLAYED, SEED_MODELS, CatalogError, ModelCatalog,
)
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_missing_file_falls_back_to_seed_without_writing():
    s3 = FakeS3Store()
    entries = await ModelCatalog(s3).load()
    assert [e.model_id for e in entries] == [e.model_id for e in SEED_MODELS]
    # 시드는 읽기 폴백일 뿐이다 — 관리자가 손대기 전까지 파일은 없다.
    assert CATALOG_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_seed_is_the_four_requested_models_in_order():
    assert [(e.name, e.model_id, e.display) for e in SEED_MODELS] == [
        ("Opus 5", "global.anthropic.claude-opus-5", True),
        ("Opus 4.6", "global.anthropic.claude-opus-4-6-v1", True),
        ("Sonnet 5", "global.anthropic.claude-sonnet-5", True),
        ("Sonnet 4.6", "global.anthropic.claude-sonnet-4-6", True),
    ]


@pytest.mark.asyncio
async def test_corrupt_file_falls_back_to_seed():
    s3 = FakeS3Store()
    s3.blobs[CATALOG_KEY] = "{{{ not json"
    entries = await ModelCatalog(s3).load()
    assert [e.model_id for e in entries] == [e.model_id for e in SEED_MODELS]


@pytest.mark.asyncio
async def test_add_writes_seed_plus_new_entry():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    added = await cat.add("Opus 4.8", "global.anthropic.claude-opus-4-8", display=False)
    assert added.model_id == "global.anthropic.claude-opus-4-8"
    stored = json.loads(s3.blobs[CATALOG_KEY])["models"]
    assert len(stored) == len(SEED_MODELS) + 1
    assert stored[-1] == {"name": "Opus 4.8",
                          "model_id": "global.anthropic.claude-opus-4-8",
                          "display": False}


@pytest.mark.asyncio
async def test_add_rejects_a_duplicate_model_id():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.add("다른 이름", SEED_MODELS[0].model_id, display=False)
    assert exc.value.code == "duplicate"


@pytest.mark.asyncio
async def test_add_rejects_the_sixth_displayed_model():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    # 시드 4개가 이미 표시 상태다 — 하나 더는 5개로 허용, 그 다음이 거부된다.
    await cat.add("다섯", "global.anthropic.claude-opus-4-8", display=True)
    with pytest.raises(CatalogError) as exc:
        await cat.add("여섯", "global.anthropic.claude-opus-4-7", display=True)
    assert exc.value.code == "too_many_displayed"
    # 거부는 아무것도 바꾸지 않는다.
    assert len(json.loads(s3.blobs[CATALOG_KEY])["models"]) == 5


@pytest.mark.asyncio
async def test_add_allows_unlimited_hidden_models():
    cat = ModelCatalog(FakeS3Store())
    for i, mid in enumerate(["a", "b", "c", "d", "e", "f"]):
        await cat.add(f"m{i}", f"global.anthropic.claude-{mid}", display=False)
    entries = await cat.load()
    assert len(entries) == len(SEED_MODELS) + 6


@pytest.mark.asyncio
async def test_displayed_returns_only_display_true_capped_at_max():
    s3 = FakeS3Store()
    s3.blobs[CATALOG_KEY] = json.dumps({"models": [
        {"name": f"m{i}", "model_id": f"global.anthropic.claude-x{i}", "display": True}
        for i in range(7)
    ]}, ensure_ascii=False)
    displayed = await ModelCatalog(s3).displayed()
    # 파일이 손으로 편집돼 6개 이상이 켜져 있어도 화면에는 5개만 간다.
    assert len(displayed) == MAX_DISPLAYED
    assert [e.model_id for e in displayed] == [
        f"global.anthropic.claude-x{i}" for i in range(MAX_DISPLAYED)]


@pytest.mark.asyncio
async def test_update_changes_name_and_display():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    target = SEED_MODELS[1].model_id
    updated = await cat.update(target, name="오퍼스 4.6", display=False)
    assert updated.name == "오퍼스 4.6" and updated.display is False
    entries = {e.model_id: e for e in await cat.load()}
    assert entries[target].name == "오퍼스 4.6"
    # 나머지는 그대로다.
    assert entries[SEED_MODELS[0].model_id].name == "Opus 5"


@pytest.mark.asyncio
async def test_update_turning_on_a_sixth_display_is_rejected():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    await cat.add("다섯", "global.anthropic.claude-opus-4-8", display=True)
    hidden = await cat.add("여섯", "global.anthropic.claude-opus-4-7", display=False)
    with pytest.raises(CatalogError) as exc:
        await cat.update(hidden.model_id, display=True)
    assert exc.value.code == "too_many_displayed"


@pytest.mark.asyncio
async def test_update_of_an_unknown_model_id_is_not_found():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.update("global.anthropic.claude-nope", display=False)
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_remove_deletes_the_entry():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    await cat.remove(SEED_MODELS[0].model_id)
    assert SEED_MODELS[0].model_id not in {e.model_id for e in await cat.load()}


@pytest.mark.asyncio
async def test_remove_of_an_unknown_model_id_is_not_found():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.remove("global.anthropic.claude-nope")
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_without_a_store_reads_seed_and_refuses_writes():
    # 버킷 미설정(로컬/테스트): 읽기는 되고 쓰기는 거부된다 —
    # durable_projects_enabled()와 같은 규율.
    cat = ModelCatalog(None)
    assert [e.model_id for e in await cat.load()] == [e.model_id for e in SEED_MODELS]
    with pytest.raises(CatalogError) as exc:
        await cat.add("x", "global.anthropic.claude-opus-4-8", display=False)
    assert exc.value.code == "readonly"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_model_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.model_catalog'`

- [ ] **Step 3: Write the implementation**

`backend/pathfinder/model_catalog.py`:

```python
"""모델 카탈로그 — 프로젝트 생성 화면이 고를 수 있는 모델 목록.

버킷 루트의 `models/catalog.json`에 산다. projects/ 밖에 두는 이유는
카탈로그가 프로젝트보다 먼저 존재해야 하기 때문이다(프로젝트 생성 화면이
프로젝트가 없는 상태에서 이것을 읽는다) — surveys/by-token/이 프로젝트
프리픽스 밖에 있는 것과 같은 이유다.

시드 목록은 **읽기 시점의 폴백일 뿐 파일로 쓰지 않는다.** 배포 직후 관리자가
아무것도 하지 않아도 콤보박스가 채워져야 하고, 반대로 '빈 카탈로그'를 유효
상태로 두면 첫 프로젝트 생성이 막힌다 — 시드는 편의가 아니라 부트스트랩
경로다. 관리자가 처음 수정할 때 비로소 파일이 생긴다.

표시 상한(5)은 등록이 아니라 `display`에만 적용된다: 관리자는 여러 모델을
등록해 두고 그중 5개만 화면에 노출한다. 상한을 등록에 두면 요구사항이
성립하지 않는다.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: 버킷 루트 기준 키. projects/·sessions/·surveys/ 옆의 네 번째 프리픽스다.
CATALOG_KEY = "models/catalog.json"

#: 콤보박스에 동시에 띄울 수 있는 모델 수. 등록 수와는 무관하다.
MAX_DISPLAYED = 5


class ModelEntry(BaseModel):
    name: str
    model_id: str
    display: bool = True


class CatalogError(Exception):
    """카탈로그 정책 위반. `code`가 라우트의 HTTP 상태로 번역된다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: ap-northeast-2에서 네 개 모두 ACTIVE인 것을 list-inference-profiles로 실측
#: 확인했다. 배포 기본값(backend-permissions.ts의 MODEL = opus-4-8)은 여기
#: 없다 — 의도된 것이다: 이 기능 이전에 만든 프로젝트와 모델 미지정 시의
#: 폴백으로만 쓰이고 콤보박스에는 뜨지 않는다.
SEED_MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(name="Opus 5", model_id="global.anthropic.claude-opus-5"),
    ModelEntry(name="Opus 4.6", model_id="global.anthropic.claude-opus-4-6-v1"),
    ModelEntry(name="Sonnet 5", model_id="global.anthropic.claude-sonnet-5"),
    ModelEntry(name="Sonnet 4.6", model_id="global.anthropic.claude-sonnet-4-6"),
)


class ModelCatalog:
    """카탈로그의 읽기/쓰기. `s3`가 None이면 읽기 전용(버킷 미설정)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> list[ModelEntry]:
        """등록된 전체 목록. 파일이 없거나 손상됐으면 시드로 떨어진다.

        손상을 예외로 올리지 않는 이유: 카탈로그를 읽지 못하면 프로젝트 생성이
        전부 막힌다. 시드로 떨어지면 워크숍은 계속 돌고, 원인은 로그에 남는다.
        """
        if self._s3 is None:
            return list(SEED_MODELS)
        try:
            body = await self._s3.get(CATALOG_KEY)
        except FileNotFoundError:
            return list(SEED_MODELS)
        try:
            d = json.loads(body)
            raw = d["models"] if isinstance(d, dict) else None
            if not isinstance(raw, list):
                raise ValueError("models is not a list")
            return [ModelEntry(**e) for e in raw]
        except Exception:
            _log.exception("corrupt model catalog at %s; falling back to seed",
                           CATALOG_KEY)
            return list(SEED_MODELS)

    async def displayed(self) -> list[ModelEntry]:
        """콤보박스에 띄울 목록. 상한을 여기서도 자른다 — 파일이 손으로
        편집되어 6개가 켜져 있어도 화면 계약(최대 5개)은 지켜져야 한다."""
        return [e for e in await self.load() if e.display][:MAX_DISPLAYED]

    async def add(self, name: str, model_id: str, display: bool) -> ModelEntry:
        entries = await self._writable()
        if any(e.model_id == model_id for e in entries):
            raise CatalogError("duplicate", f"{model_id} is already registered")
        entry = ModelEntry(name=name, model_id=model_id, display=display)
        entries.append(entry)
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def update(self, model_id: str, *, name: str | None = None,
                     display: bool | None = None) -> ModelEntry:
        entries = await self._writable()
        entry = next((e for e in entries if e.model_id == model_id), None)
        if entry is None:
            raise CatalogError("not_found", f"{model_id} is not registered")
        if name is not None:
            entry.name = name
        if display is not None:
            entry.display = display
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def remove(self, model_id: str) -> None:
        entries = await self._writable()
        kept = [e for e in entries if e.model_id != model_id]
        if len(kept) == len(entries):
            raise CatalogError("not_found", f"{model_id} is not registered")
        await self._save(kept)

    async def _writable(self) -> list[ModelEntry]:
        if self._s3 is None:
            raise CatalogError(
                "readonly",
                "model catalog is read-only without PATHFINDER_S3_BUCKET")
        return await self.load()

    @staticmethod
    def _check_display_cap(entries: list[ModelEntry]) -> None:
        shown = sum(1 for e in entries if e.display)
        if shown > MAX_DISPLAYED:
            raise CatalogError(
                "too_many_displayed",
                f"at most {MAX_DISPLAYED} models can be displayed "
                f"(now {shown}) — hide one first")

    async def _save(self, entries: list[ModelEntry]) -> None:
        assert self._s3 is not None  # _writable()이 이미 확인했다
        body = json.dumps({"models": [e.model_dump() for e in entries]},
                          ensure_ascii=False)
        await self._s3.put(CATALOG_KEY, body)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_model_catalog.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/model_catalog.py backend/tests/test_model_catalog.py
git commit -m "$(cat <<'EOF'
feat(models): 모델 카탈로그 도메인

버킷 루트 models/catalog.json. 파일이 없거나 손상되면 시드 4개로 떨어지고
그 시드를 파일로 쓰지는 않는다 — 배포 직후 아무 설정 없이 콤보박스가
채워지되, 관리자가 손대기 전까지 파일은 생기지 않는다.

표시 상한 5는 display에만 적용된다. 등록은 무제한 — 요구사항이 "여러 모델을
등록하고 디스플레이는 5개"이므로 상한을 등록에 두면 성립하지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 레지스트리·매니페스트에 model_id 싣기

**Files:**
- Modify: `backend/pathfinder/workspace.py:64-110` (`ProjectRegistry`)
- Modify: `backend/pathfinder/project_store.py:18-50` (`write_manifest`, `restore_projects`)
- Modify: `backend/tests/test_project_store.py:5-28` (기존 3-tuple 단정)
- Test: `backend/tests/test_registry.py` (추가), `backend/tests/test_project_store.py` (추가)

**Interfaces:**
- Consumes: 없음 (Task 1과 독립).
- Produces:
  - `ProjectRegistry.register(project_id, name=None, created_at=None, model_id=None)`
  - `ProjectRegistry.get_model_id(project_id) -> str | None`
  - `write_manifest(root, project_id, name, created_at=None, model_id=None) -> str`
  - `restore_projects(root) -> list[tuple[str, str | None, str | None, str | None]]` — **4-tuple** `(pid, name, created_at, model_id)`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_registry.py` 끝에 추가:

```python
def test_register_stores_and_returns_model_id():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", created_at="2026-08-01T00:00:00+00:00",
               model_id="global.anthropic.claude-opus-5")
    assert r.get_model_id("p1") == "global.anthropic.claude-opus-5"


def test_get_model_id_is_none_for_a_project_registered_without_one():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_model_id("p1") is None


def test_get_model_id_is_none_for_an_unknown_project():
    # get_name은 KeyError를 내지만 이쪽은 None이다 — 호출부(project_model)가
    # 폴백 체인의 첫 칸으로 쓰므로 미등록도 "모델 없음"이면 충분하다.
    from pathfinder.workspace import ProjectRegistry
    assert ProjectRegistry().get_model_id("nope") is None


def test_remove_drops_the_model_id():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, model_id="global.anthropic.claude-opus-5")
    r.remove("p1")
    assert r.get_model_id("p1") is None
```

`backend/tests/test_project_store.py`의 기존 두 테스트를 4-tuple로 고치고 두 개를 추가한다.

`test_write_manifest_puts_expected_key_and_shape`를 다음으로 교체:

```python
@pytest.mark.asyncio
async def test_write_manifest_puts_expected_key_and_shape():
    root = FakeS3Store()
    await write_manifest(root, "p1", "이름")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["project_id"] == "p1" and d["name"] == "이름"
    assert d["created_at"].endswith("+00:00") or d["created_at"].endswith("Z")  # UTC ISO8601
    # 모델 미지정은 명시적 null로 기록한다 — 키 자체를 빼면 "구 매니페스트"와
    # "모델을 고르지 않은 새 프로젝트"를 구별할 수 없다.
    assert d["model_id"] is None


@pytest.mark.asyncio
async def test_write_manifest_records_the_model_id():
    root = FakeS3Store()
    await write_manifest(root, "p1", None,
                         model_id="global.anthropic.claude-opus-5")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["model_id"] == "global.anthropic.claude-opus-5"
```

`test_restore_reads_manifests_and_skips_garbage`를 다음으로 교체:

```python
@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps(
        {"project_id": "pa", "name": "A", "created_at": "2026-07-22T01:00:00+00:00",
         "model_id": "global.anthropic.claude-opus-5"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # 손상 → 건너뜀
    root.blobs["pd/project.json"] = "[1,2,3]"                # JSON but not dict → 건너뜀
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # 매니페스트 아님 → 무시
    restored = {pid: (name, created_at, model_id)
                for pid, name, created_at, model_id in await restore_projects(root)}
    # created_at·model_id는 매니페스트에서 승계, 없으면(구 매니페스트) None.
    assert restored == {
        "pa": ("A", "2026-07-22T01:00:00+00:00", "global.anthropic.claude-opus-5"),
        "pb": (None, None, None),
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py tests/test_project_store.py -q`
Expected: FAIL — `AttributeError: 'ProjectRegistry' object has no attribute 'get_model_id'` 및 `restore_projects`의 3-tuple 언패킹 `ValueError: not enough values to unpack`

- [ ] **Step 3: Write the implementation**

`backend/pathfinder/workspace.py` — `ProjectRegistry.__init__`에 맵 추가:

```python
    def __init__(self):
        self._names: dict[str, str | None] = {}
        self._workspaces: dict[str, Workspace] = {}
        # 생성 시각(ISO 문자열) — 목록 정렬 기준. 매니페스트에서 복원되거나
        # 생성 라우트가 전달한다. 구 매니페스트에는 없을 수 있어 None 허용.
        self._created_at: dict[str, str | None] = {}
        # 이 프로젝트가 도는 Bedrock 모델 id. 카탈로그를 참조(FK)하지 않고
        # 값을 복사해 둔 것이다 — 관리자가 모델을 카탈로그에서 지워도 진행
        # 중인 프로젝트가 모델을 잃으면 안 된다. None = 미지정(env 폴백).
        self._model_id: dict[str, str | None] = {}
```

`register`를 교체:

```python
    def register(self, project_id: str, name: str | None = None,
                 created_at: str | None = None,
                 model_id: str | None = None) -> None:
        self._names[project_id] = name
        self._created_at[project_id] = created_at
        self._model_id[project_id] = model_id
```

`remove`에 한 줄 추가:

```python
    def remove(self, project_id: str) -> Workspace | None:
        """등록·워크스페이스 모두 제거. 있던 Workspace를 반환(없으면 None). 멱등."""
        self._names.pop(project_id, None)
        self._created_at.pop(project_id, None)
        self._model_id.pop(project_id, None)
        return self._workspaces.pop(project_id, None)
```

파일 끝에 게터 추가:

```python
    def get_model_id(self, project_id: str) -> str | None:
        """이 프로젝트의 모델 id, 없으면 None.

        get_name과 달리 미등록에 KeyError를 내지 않는다 —
        app.project_model()이 폴백 체인의 첫 칸으로 쓰므로, 미등록도
        '모델 없음'으로 다루는 것이 호출부를 단순하게 만든다.
        """
        return self._model_id.get(project_id)
```

`backend/pathfinder/project_store.py` — 두 함수 교체:

```python
async def write_manifest(root: S3StoreLike, project_id: str, name: str | None,
                         created_at: str | None = None,
                         model_id: str | None = None) -> str:
    """매니페스트를 쓰고 기록된 created_at을 반환한다 — 호출부(생성 라우트)가
    같은 시각을 레지스트리에도 등록해 목록 정렬 기준을 일치시킨다.

    model_id는 카탈로그를 참조하지 않고 **복사**한다: 관리자가 그 모델을
    카탈로그에서 지워도 이 프로젝트는 계속 같은 모델로 돌아야 한다. 미지정은
    명시적 null로 기록한다 — 키를 빼면 '구 매니페스트'와 '모델을 고르지 않은
    새 프로젝트'를 구별할 수 없다.
    """
    ts = created_at or datetime.now(timezone.utc).isoformat()
    body = json.dumps(
        {"project_id": project_id, "name": name, "created_at": ts,
         "model_id": model_id},
        ensure_ascii=False)
    await root.put(f"{project_id}/project.json", body)
    return ts


async def restore_projects(
    root: S3StoreLike,
) -> list[tuple[str, str | None, str | None, str | None]]:
    """projects/ 스캔 → 매니페스트 병렬 GET → [(pid, name, created_at, model_id)].
    손상 항목은 로그 후 건너뜀 — 하나가 썩어도 나머지 복원을 막지 않는다.
    created_at·model_id는 구 매니페스트에 없을 수 있어 None 허용(정렬 시 맨 앞,
    모델은 env 폴백)."""
    keys = [k for k in await root.list("") if _MANIFEST.match(k)]
    bodies = await asyncio.gather(*(root.get(k) for k in keys), return_exceptions=True)
    out: list[tuple[str, str | None, str | None, str | None]] = []
    for key, body in zip(keys, bodies):
        if isinstance(body, BaseException):
            _log.warning("manifest read failed for %s: %r", key, body)
            continue
        try:
            d = json.loads(body)
            if not isinstance(d, dict):
                _log.warning("corrupt manifest skipped: %s", key)
                continue
            pid = d.get("project_id") or _MANIFEST.match(key).group(1)  # type: ignore[union-attr]
            out.append((pid, d.get("name"), d.get("created_at"), d.get("model_id")))
        except (json.JSONDecodeError, TypeError):
            _log.warning("corrupt manifest skipped: %s", key)
    return out
```

- [ ] **Step 4: Fix the one existing 3-tuple consumer**

`backend/pathfinder/app.py:372` (`_lifespan`)의 루프를 4-tuple로 바꾼다:

```python
            for pid, name, created_at, model_id in await restore_projects(projects_root_s3_factory()):
                registry.register(pid, name, created_at=created_at, model_id=model_id)
```

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS — 전체 통과. `test_app_lifespan_restore.py`가 `restore_projects`를 가짜로 갈아끼우고 있으면 그 가짜의 반환도 4-tuple로 고친다.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/workspace.py backend/pathfinder/project_store.py \
        backend/pathfinder/app.py backend/tests/test_registry.py \
        backend/tests/test_project_store.py backend/tests/test_app_lifespan_restore.py
git commit -m "$(cat <<'EOF'
feat(models): 매니페스트·레지스트리에 model_id를 싣는다

카탈로그를 FK로 참조하지 않고 값을 복사한다 — 관리자가 모델을 카탈로그에서
지워도 진행 중인 프로젝트가 모델을 잃으면 안 된다.

미지정은 명시적 null로 기록한다. 키를 빼면 구 매니페스트와 "모델을 고르지
않은 새 프로젝트"를 구별할 수 없다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `project_model()` + 모델 주입 3지점

**Files:**
- Modify: `backend/pathfinder/app.py` (팩토리 추가, 주입 3지점, `questionnaire_agent_factory` 시그니처)
- Modify: `backend/pathfinder/routes/surveys.py:66`
- Modify: `backend/pathfinder/agent/driver.py:95` (범위 제외 주석)
- Modify: `backend/tests/test_routes_surveys.py:47,84`
- Test: `backend/tests/test_project_model.py` (신규)

**Interfaces:**
- Consumes: `ProjectRegistry.get_model_id` (Task 2), `ModelCatalog` (Task 1).
- Produces:
  - `app.models_root_s3_factory() -> S3StoreLike` — 버킷 루트 스토어 (`surveys_root_s3_factory`와 같은 모양)
  - `app.model_catalog() -> ModelCatalog` — 버킷 미설정이면 `ModelCatalog(None)`
  - `app.project_model(project_id: str) -> str | None`
  - `app.questionnaire_agent_factory(project_id: str)` — **인자 1개 추가**

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_project_model.py`:

```python
# backend/tests/test_project_model.py
#
# 모델 해석의 폴백 순서와, 그것이 실제로 세 주입 지점에 닿는지.
from __future__ import annotations

import pytest

import pathfinder.app as app_module


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    app_module.registry.remove("pm-test")


def test_project_model_prefers_the_projects_own_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-opus-5")
    assert app_module.project_model("pm-test") == "global.anthropic.claude-opus-5"


def test_project_model_falls_back_to_env_when_project_has_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    app_module.registry.register("pm-test", None)
    assert app_module.project_model("pm-test") == "global.anthropic.claude-opus-4-8"


def test_project_model_is_none_without_project_or_env(monkeypatch):
    # 로컬 개발: 둘 다 없으면 None — 드라이버가 env를 넣지 않아 SDK 기본값으로
    # 간다(종전 동작).
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    assert app_module.project_model("pm-test") is None


def test_project_model_for_an_unregistered_project_uses_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    assert app_module.project_model("never-registered") == "global.anthropic.claude-opus-4-8"


def test_driver_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claude")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    driver = app_module.driver_factory("pm-test", tmp_path)
    assert driver._anthropic_model == "global.anthropic.claude-sonnet-5"


def test_builder_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    monkeypatch.setenv("PATHFINDER_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("PATHFINDER_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    session = app_module.proto_session_factory("pm-test", "slug")
    builder = session._builder_factory("sid", False)
    assert builder._anthropic_model == "global.anthropic.claude-sonnet-5"


@pytest.mark.asyncio
async def test_questionnaire_agent_factory_raises_when_no_model(monkeypatch):
    # 설문 생성은 여기가 유일하게 모델을 필수로 요구하는 지점이다. 없으면
    # 502로 번역될 RuntimeError를 내고 이유를 남긴다 — KeyError로 터지면
    # 로그에 'ANTHROPIC_MODEL'만 남고 프로젝트 설정 문제인지 알 수 없다.
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    call = app_module.questionnaire_agent_factory("pm-test")
    with pytest.raises(RuntimeError, match="no model"):
        await call("프롬프트")
```

`backend/tests/test_routes_surveys.py`의 가짜 팩토리 두 개가 인자를 받게 고친다.
`:42`의 `def agent_factory():`와 `:80`의 같은 줄을 각각 다음으로 바꾼다
(`_pid`로 받아 무시한다 — 이 테스트들은 모델이 아니라 문항 생성 경로를 본다):

```python
    def agent_factory(_pid):
```

`monkeypatch.setattr(...)` 라인은 그대로 둔다 — 팩토리 자체를 갈아끼우는
방식이므로 시그니처만 맞으면 된다.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_model.py -q`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute 'project_model'`

- [ ] **Step 3: Write the implementation**

`backend/pathfinder/app.py` — `projects_root_s3_factory` 아래에 추가:

```python
# 모델 카탈로그용 — 버킷 루트 스토어. 카탈로그는 프로젝트보다 먼저 존재해야
# 하므로(프로젝트 생성 화면이 프로젝트 없이 읽는다) projects/ 밖에 있다.
# 테스트에서 monkeypatch.
def models_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def model_catalog():
    """ModelCatalog 팩토리 (monkeypatchable in tests).

    버킷이 없으면 읽기 전용 카탈로그(시드만)를 준다 — 로컬 개발이 아무 설정
    없이 프로젝트를 만들 수 있어야 하고, 그 화면의 콤보박스도 채워져야 한다.
    """
    from pathfinder.model_catalog import ModelCatalog
    if not durable_projects_enabled():
        return ModelCatalog(None)
    return ModelCatalog(models_root_s3_factory())


def project_model(project_id: str) -> str | None:
    """이 프로젝트가 도는 Bedrock 모델 id.

    폴백 순서는 프로젝트 → env → None이고 각 칸에 이유가 있다:
      - 프로젝트: 생성 시 고른 값(매니페스트에 복사돼 있다).
      - env(ANTHROPIC_MODEL): 이 기능 이전에 만든 프로젝트가 계속 도는 길.
        배포에서는 backend-permissions.ts의 MODEL이 이 값을 넣는다.
      - None: 로컬 개발에서 env도 없는 경우. 드라이버는 None을 받으면
        ANTHROPIC_MODEL을 넣지 않아 SDK 기본값으로 간다(종전 동작).
    """
    return registry.get_model_id(project_id) or os.environ.get("ANTHROPIC_MODEL")
```

`driver_factory`의 `anthropic_model` 인자를 교체:

```python
        anthropic_model=project_model(project_id),
```

`builder_factory`(`proto_session_factory` 안)의 같은 인자를 교체:

```python
            anthropic_model=project_model(project_id),
```

`questionnaire_agent_factory`를 교체:

```python
def questionnaire_agent_factory(project_id: str):
    """A one-shot `async (prompt) -> str` callable. Deliberately NOT
    StrandsDriver: that bakes in the AIPLC rules prompt, workspace tools and a
    session manager, none of which belong in a stateless generation call.

    project_id를 받는 이유: 문항 생성도 그 프로젝트의 모델로 돌아야 한다.
    종전에는 os.environ["ANTHROPIC_MODEL"]을 직접 읽어, 프로젝트별 모델을
    골라도 이 경로만 전역 env를 썼다.
    """
    model_id = project_model(project_id)

    async def call(prompt: str) -> str:
        if not model_id:
            # 여기가 유일하게 모델을 필수로 요구하는 지점이다(다른 둘은 None을
            # SDK 기본값으로 넘긴다). 라우트가 502로 감싸고 이 문장이 로그에
            # 남아 원인이 프로젝트 설정임을 말해 준다.
            raise RuntimeError(
                f"no model for project {project_id!r}: neither the project's "
                "model_id nor ANTHROPIC_MODEL is set")
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=model_id, max_tokens=8000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call
```

`backend/pathfinder/routes/surveys.py:66`의 호출을 교체:

```python
            prototype_md, app_module.questionnaire_agent_factory(pid),
```

`backend/pathfinder/agent/driver.py`의 `model_id=os.environ["ANTHROPIC_MODEL"],` 위에 주석 추가:

```python
        # ⚠️ 프로젝트별 모델 선택(app.project_model)은 이 폴백 드라이버에
        # 적용되지 않는다 — PATHFINDER_DISCOVERY_DRIVER=strands로 돌리면
        # 전역 ANTHROPIC_MODEL을 쓴다. 의도된 범위 제외다: 이 드라이버는
        # 워크숍 중 탈출로이고 워크숍 후 삭제 예정이므로, 두 경로를 다
        # 유지하는 대신 하나(ClaudeDriver)만 정확하게 둔다.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_project_model.py tests/test_routes_surveys.py -q`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/app.py backend/pathfinder/routes/surveys.py \
        backend/pathfinder/agent/driver.py backend/tests/test_project_model.py \
        backend/tests/test_routes_surveys.py
git commit -m "$(cat <<'EOF'
feat(models): project_model()로 모델 주입 3지점을 프로젝트별로

Discovery 드라이버·프로토타입 빌더·설문 문항 생성이 전역 env 대신
project_model(pid)를 쓴다. questionnaire_agent_factory는 여기가 유일하게
모델을 필수로 요구하는 지점이라 인자를 받게 바꿨다 — 그렇지 않으면
프로젝트별 모델을 골라도 설문 생성만 전역 env를 쓴다.

StrandsDriver는 범위 밖(폴백 경로, 워크숍 후 삭제 예정) — 주석으로 기록.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `/models` + `/admin/models*` 라우터

**Files:**
- Create: `backend/pathfinder/routes/models.py`
- Modify: `backend/pathfinder/app.py` (라우터 2개 등록)
- Test: `backend/tests/test_routes_models.py`

**Interfaces:**
- Consumes: `app.model_catalog()` (Task 3), `ModelCatalog`/`CatalogError` (Task 1), `require_admin` (기존 `pathfinder.auth.deps`).
- Produces:
  - `models.router` — `GET /models` (일반)
  - `models.admin_router` — `GET|POST /admin/models`, `PATCH|DELETE /admin/models/{model_id}`
  - 응답 모양: `GET /models` → `{"models": [{"name": ..., "model_id": ...}]}`; `GET /admin/models` → `{"models": [{"name": ..., "model_id": ..., "display": bool}]}`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_routes_models.py`:

```python
# backend/tests/test_routes_models.py
#
# 라우트 계층의 책임만: 응답 축약(/models는 display만·이름과 id만), 정책 위반의
# HTTP 번역, 관리자 게이트. 카탈로그 자체는 test_model_catalog.py가 본다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal
from pathfinder.model_catalog import SEED_MODELS, ModelCatalog
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture()
def catalog(monkeypatch):
    """쓰기 가능한 카탈로그 + 'admin으로 로그인한' 요청자."""
    cat = ModelCatalog(FakeS3Store())
    monkeypatch.setattr(app_module, "model_catalog", lambda: cat)
    me = Principal(username="admin@pathfinder.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield cat
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# ---- GET /models (일반) ----

def test_models_returns_name_and_id_only(catalog, client):
    body = client.get("/models").json()
    assert body["models"] == [
        {"name": e.name, "model_id": e.model_id} for e in SEED_MODELS]
    # display는 화면에 보내지 않는다 — 일반 사용자에게 의미가 없고, 프론트가
    # 필터링을 잊는 경로를 없앤다.
    assert all("display" not in m for m in body["models"])


@pytest.mark.asyncio
async def test_models_omits_hidden_entries(catalog, client):
    await catalog.update(SEED_MODELS[0].model_id, display=False)
    ids = {m["model_id"] for m in client.get("/models").json()["models"]}
    assert SEED_MODELS[0].model_id not in ids
    assert SEED_MODELS[1].model_id in ids


# ---- GET /admin/models ----

def test_admin_list_includes_display_flag(catalog, client):
    body = client.get("/admin/models").json()
    assert body["models"][0] == {"name": SEED_MODELS[0].name,
                                 "model_id": SEED_MODELS[0].model_id,
                                 "display": True}


# ---- POST /admin/models ----

def test_admin_add_returns_201_and_the_entry(catalog, client):
    r = client.post("/admin/models",
                    json={"name": "Opus 4.8",
                          "model_id": "global.anthropic.claude-opus-4-8",
                          "display": False})
    assert r.status_code == 201
    assert r.json() == {"name": "Opus 4.8",
                        "model_id": "global.anthropic.claude-opus-4-8",
                        "display": False}


def test_admin_add_duplicate_is_409(catalog, client):
    r = client.post("/admin/models",
                    json={"name": "중복", "model_id": SEED_MODELS[0].model_id,
                          "display": False})
    assert r.status_code == 409


def test_admin_add_sixth_displayed_is_400(catalog, client):
    assert client.post("/admin/models", json={
        "name": "다섯", "model_id": "global.anthropic.claude-opus-4-8",
        "display": True}).status_code == 201
    r = client.post("/admin/models", json={
        "name": "여섯", "model_id": "global.anthropic.claude-opus-4-7",
        "display": True})
    assert r.status_code == 400
    # 관리자가 무엇을 해야 하는지 문장에 있어야 한다.
    assert "5" in r.json()["detail"]


def test_admin_add_rejects_a_blank_name(catalog, client):
    r = client.post("/admin/models", json={
        "name": "  ", "model_id": "global.anthropic.claude-opus-4-8",
        "display": True})
    assert r.status_code == 422


def test_admin_add_rejects_a_blank_model_id(catalog, client):
    r = client.post("/admin/models", json={"name": "x", "model_id": "",
                                           "display": True})
    assert r.status_code == 422


# ---- PATCH /admin/models/{model_id} ----

def test_admin_patch_changes_display(catalog, client):
    r = client.patch(f"/admin/models/{SEED_MODELS[0].model_id}",
                     json={"display": False})
    assert r.status_code == 200 and r.json()["display"] is False


def test_admin_patch_changes_name(catalog, client):
    r = client.patch(f"/admin/models/{SEED_MODELS[0].model_id}",
                     json={"name": "오퍼스 5"})
    assert r.status_code == 200 and r.json()["name"] == "오퍼스 5"


def test_admin_patch_unknown_model_is_404(catalog, client):
    r = client.patch("/admin/models/global.anthropic.claude-nope",
                     json={"display": False})
    assert r.status_code == 404


# ---- DELETE /admin/models/{model_id} ----

def test_admin_delete_removes_the_entry(catalog, client):
    r = client.delete(f"/admin/models/{SEED_MODELS[0].model_id}")
    assert r.status_code == 204
    ids = {m["model_id"] for m in client.get("/admin/models").json()["models"]}
    assert SEED_MODELS[0].model_id not in ids


def test_admin_delete_unknown_model_is_404(catalog, client):
    assert client.delete("/admin/models/global.anthropic.claude-nope").status_code == 404


# ---- 버킷 미설정 ----

def test_admin_write_without_a_bucket_is_503(monkeypatch, client):
    monkeypatch.setattr(app_module, "model_catalog", lambda: ModelCatalog(None))
    me = Principal(username="admin@pathfinder.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    try:
        r = client.post("/admin/models", json={
            "name": "x", "model_id": "global.anthropic.claude-opus-4-8",
            "display": True})
        assert r.status_code == 503
        # 읽기는 여전히 된다 — 로컬 개발이 시드로 돈다.
        assert len(client.get("/models").json()["models"]) == len(SEED_MODELS)
    finally:
        app_module.app.dependency_overrides.clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_models.py -q`
Expected: FAIL — 모든 요청이 404 (라우터 미등록)

- [ ] **Step 3: Write the implementation**

`backend/pathfinder/routes/models.py`:

```python
# backend/pathfinder/routes/models.py — 모델 카탈로그.
#
# 두 라우터로 나뉘는 이유는 권한이 다르기 때문이다:
#   router       GET /models        — 프로젝트 생성 화면의 콤보박스(일반 사용자)
#   admin_router /admin/models*     — 등록·수정·삭제(관리자)
#
# admin_router는 라우터 전체에 require_admin을 붙인다(admin_users.py와 같은
# 규율) — 라우트마다 붙이는 것을 잊을 여지를 없앤다.
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from pathfinder.auth.deps import require_admin
from pathfinder.model_catalog import CatalogError

_log = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# 카탈로그 정책 위반 → HTTP. readonly가 503인 이유: 클라이언트 잘못이 아니라
# 서버가 버킷 없이 떠 있다는 뜻이다(로컬 개발에서 관리자 화면을 연 경우).
_ERROR_STATUS = {
    "duplicate": 409,
    "too_many_displayed": 400,
    "not_found": 404,
    "readonly": 503,
}


def _http_error(exc: CatalogError) -> HTTPException:
    status = _ERROR_STATUS.get(exc.code, 500)
    if status >= 500:
        _log.warning("model catalog error (%s) -> %d", exc.code, status)
    # 이 메시지들은 전부 우리가 쓴 문장이고 자격증명이나 내부 경로를 담지
    # 않는다 — 관리자가 무엇을 해야 하는지 알아야 하므로 그대로 보여준다.
    return HTTPException(status_code=status, detail=str(exc))


class AddModel(BaseModel):
    name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    display: bool = True


class PatchModel(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    display: bool | None = None


def _catalog():
    import pathfinder.app as app_module
    return app_module.model_catalog()


@router.get("/models")
async def list_displayed_models():
    """콤보박스가 부르는 곳. display가 켜진 것만, 최대 5개, 이름과 id만.

    display 플래그 자체는 보내지 않는다 — 일반 사용자에게 의미가 없고,
    프론트가 필터링을 잊는 경로를 없앤다.
    """
    entries = await _catalog().displayed()
    return {"models": [{"name": e.name, "model_id": e.model_id} for e in entries]}


@admin_router.get("/models")
async def admin_list_models():
    entries = await _catalog().load()
    return {"models": [e.model_dump() for e in entries]}


@admin_router.post("/models", status_code=201)
async def admin_add_model(body: AddModel):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="이름을 입력하세요.")
    try:
        entry = await _catalog().add(body.name.strip(), body.model_id.strip(),
                                     display=body.display)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return entry.model_dump()


@admin_router.patch("/models/{model_id}")
async def admin_patch_model(model_id: str, body: PatchModel):
    name = body.name.strip() if body.name is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=422, detail="이름을 입력하세요.")
    try:
        entry = await _catalog().update(model_id, name=name, display=body.display)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return entry.model_dump()


@admin_router.delete("/models/{model_id}", status_code=204)
async def admin_delete_model(model_id: str):
    try:
        await _catalog().remove(model_id)
    except CatalogError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=204)
```

`backend/pathfinder/app.py` — `admin_users` 등록 아래에 추가:

```python
from pathfinder.routes import models as models_routes  # noqa: E402
app.include_router(models_routes.router, dependencies=_AUTH)
app.include_router(models_routes.admin_router, dependencies=_AUTH)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_models.py tests/test_auth_route_coverage.py -q`
Expected: PASS — `test_auth_route_coverage`가 새 `/admin/models*` 라우트를 admin으로 인식해야 한다. 실패하면 그 테스트가 admin 라우트를 어떻게 열거하는지 읽고(경로 프리픽스 목록이 있으면) `/admin/models`를 넣는다.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/routes/models.py backend/pathfinder/app.py \
        backend/tests/test_routes_models.py
git commit -m "$(cat <<'EOF'
feat(models): /models + /admin/models* 라우터

/models는 display가 켜진 것만 이름·id로 축약해 보낸다 — 일반 사용자에게
display 플래그는 의미가 없고, 프론트가 필터링을 잊는 경로를 없앤다.

admin_router는 라우터 전체에 require_admin을 붙인다(admin_users.py와 같은
규율). 카탈로그 정책 위반 문장은 우리가 쓴 것이고 내부 정보를 담지 않으므로
그대로 보여준다 — 관리자가 무엇을 해야 하는지 알아야 한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `POST /projects`의 model_id 검증 + `GET /projects/{pid}`

**Files:**
- Modify: `backend/pathfinder/routes/projects.py`
- Test: `backend/tests/test_routes_projects_model.py` (신규)

**Interfaces:**
- Consumes: `app.model_catalog()` (Task 3), `write_manifest(..., model_id=)` (Task 2), `registry.get_model_id` (Task 2).
- Produces:
  - `POST /projects` 본문에 `model_id: str | None`; 응답에 `model_id`
  - `GET /projects` 각 항목에 `model_id`
  - `GET /projects/{pid}` → `{"project_id", "name", "created_at", "model_id"}`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_routes_projects_model.py`:

```python
# backend/tests/test_routes_projects_model.py
#
# 생성 시점의 model_id 검증과 조회. 검증 기준이 '표시 목록'인 것이 핵심이다 —
# 표시가 꺼진 모델은 관리자가 의도적으로 내린 것이므로 새 프로젝트가 그것을
# 고르면 안 된다.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.model_catalog import SEED_MODELS, ModelCatalog
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)
CHOSEN = SEED_MODELS[0].model_id


@pytest.fixture()
def catalog(monkeypatch):
    cat = ModelCatalog(FakeS3Store())
    monkeypatch.setattr(app_module, "model_catalog", lambda: cat)
    return cat


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pm-1", "pm-2", "pm-3", "pm-4", "pm-5"):
        app_module.registry.remove(pid)


def test_create_accepts_a_displayed_model_and_records_it(catalog, monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pm-1", "model_id": CHOSEN})
    assert r.status_code == 200
    assert r.json()["model_id"] == CHOSEN
    assert json.loads(fake.blobs["pm-1/project.json"])["model_id"] == CHOSEN
    assert app_module.registry.get_model_id("pm-1") == CHOSEN


def test_create_without_a_model_id_still_works(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pm-2"})
    assert r.status_code == 200
    assert r.json()["model_id"] is None
    assert app_module.registry.get_model_id("pm-2") is None


def test_create_rejects_an_unregistered_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pm-3",
                                       "model_id": "global.anthropic.claude-nope"})
    assert r.status_code == 400
    # 첫 대화 턴의 AccessDenied가 아니라 생성 시점에 막혀야 한다.
    assert not app_module.registry.is_registered("pm-3")


@pytest.mark.asyncio
async def test_create_rejects_a_hidden_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    await catalog.update(CHOSEN, display=False)
    r = client.post("/projects", json={"project_id": "pm-4", "model_id": CHOSEN})
    assert r.status_code == 400


def test_get_project_returns_metadata_without_booting_a_workspace(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    booted = {"n": 0}

    async def _boom(pid):
        booted["n"] += 1
        raise AssertionError("배지 하나가 워크스페이스 lazy 부팅을 유발해서는 안 된다")

    app_module.registry.register("pm-5", "이름",
                                 created_at="2026-08-01T00:00:00+00:00",
                                 model_id=CHOSEN)
    monkeypatch.setattr(app_module, "make_workspace", _boom)
    body = client.get("/projects/pm-5").json()
    assert body == {"project_id": "pm-5", "name": "이름",
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "model_id": CHOSEN}
    assert booted["n"] == 0


def test_get_project_is_404_for_an_unknown_project(catalog):
    assert client.get("/projects/never-existed").status_code == 404


def test_list_includes_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pm-1", "model_id": CHOSEN})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pm-1")
    assert row["model_id"] == CHOSEN
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_model.py -q`
Expected: FAIL — `KeyError: 'model_id'` (응답에 필드 없음) 및 `GET /projects/pm-5`가 404

- [ ] **Step 3: Write the implementation**

`backend/pathfinder/routes/projects.py` — `CreateProject`와 `create_project`를 교체:

```python
class CreateProject(BaseModel):
    project_id: str
    name: str | None = None
    # 이 프로젝트가 쓸 Bedrock 모델 id. 미지정이면 env 기본값으로 돈다
    # (app.project_model의 폴백 체인).
    model_id: str | None = None


async def _validate_model_id(model_id: str | None) -> None:
    """카탈로그의 **표시 목록**에 있는지 확인한다.

    등록 목록이 아니라 표시 목록인 이유: display가 꺼진 모델은 관리자가
    의도적으로 내린 것이므로 새 프로젝트가 그것을 고르면 안 된다.

    이 검증이 없으면 임의 문자열이 매니페스트에 들어가고, 실패는 첫 대화
    턴의 AccessDenied(IAM 와일드카드 밖) 또는 ValidationException(존재하지
    않는 프로파일)으로 나타난다 — 둘 다 백엔드 로그에만 남는다.
    """
    if model_id is None:
        return
    allowed = {e.model_id for e in await app_module.model_catalog().displayed()}
    if model_id not in allowed:
        raise HTTPException(status_code=400,
                            detail="선택할 수 없는 모델입니다.")


@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    # 워크스페이스를 만들기 전에 검증한다 — 거절할 요청 때문에 로컬 디렉토리와
    # 러너를 만들고 되돌리는 것은 낭비다.
    await _validate_model_id(body.model_id)
    workspace = await app_module.make_workspace(body.project_id)
    # 매니페스트와 레지스트리가 같은 created_at을 갖도록 여기서 확정 —
    # 목록 정렬(생성일 오름차순) 기준이 재시작 전후로 달라지지 않는다.
    created_at = datetime.now(timezone.utc).isoformat()
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id)
        except Exception:
            # 스펙 결정: 재시작하면 사라질 프로젝트를 조용히 만들지 않는다.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("workspace cleanup after manifest failure failed")
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.register(body.project_id, body.name,
                                 created_at=created_at, model_id=body.model_id)
    app_module.registry.attach(body.project_id, workspace)
    return {"project_id": body.project_id, "name": body.name,
            "model_id": body.model_id}
```

`list_projects`의 응답 항목에 `model_id`를 추가:

```python
            {"project_id": pid, "name": app_module.registry.get_name(pid),
             "created_at": app_module.registry.get_created_at(pid),
             "model_id": app_module.registry.get_model_id(pid),
             "progress": prog}
```

`list_projects` 아래에 상세 라우트 추가:

```python
@router.get("/projects/{pid}")
async def get_project(pid: str):
    """프로젝트 하나의 메타데이터. 헤더의 모델 배지가 부르는 곳이다.

    ensure_workspace를 타지 않고 레지스트리만 읽는다 — 배지 하나가 워크스페이스
    lazy 초기화(러너 부팅)를 유발하면 안 된다. list_projects의 _progress가
    같은 이유로 S3를 직접 읽는다.
    """
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    return {"project_id": pid,
            "name": app_module.registry.get_name(pid),
            "created_at": app_module.registry.get_created_at(pid),
            "model_id": app_module.registry.get_model_id(pid)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_model.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS. `GET /projects/{pid}`가 `/projects/{pid}/...` 라우트들보다 뒤에 등록되어 경로 충돌이 없는지 확인 — 충돌 증상은 기존 `test_routes_artifacts.py`/`test_routes_history.py`의 404다.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/routes/projects.py backend/tests/test_routes_projects_model.py
git commit -m "$(cat <<'EOF'
feat(models): 프로젝트 생성 시 model_id 검증 + GET /projects/{pid}

검증 기준은 등록 목록이 아니라 표시 목록이다 — display가 꺼진 모델은
관리자가 의도적으로 내린 것이므로 새 프로젝트가 고르면 안 된다. 검증이
없으면 임의 문자열이 매니페스트에 들어가고 실패는 첫 대화 턴의
AccessDenied로만 드러난다.

상세 라우트는 레지스트리만 읽는다 — 헤더 배지 하나가 워크스페이스 lazy
부팅을 유발하면 안 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: IAM 와일드카드

**Files:**
- Modify: `infra/lib/backend-permissions.ts:9-26,52-58`
- Modify: `infra/test/hosting-stack.assert.ts:179-195`

**Interfaces:**
- Consumes: 없음 (백엔드와 독립).
- Produces: `backendPolicyStatements`가 `claude-*` 와일드카드 리소스를 내는 것. `MODEL` 상수는 불변.

- [ ] **Step 1: Write the failing assertion**

`infra/test/hosting-stack.assert.ts`의 `for (const m of [...])` 블록 전체(주석 포함, 179~195행)를 다음으로 교체:

```typescript
  // 모델 허용은 명시 목록이 아니라 와일드카드다. 명시 목록이면 관리자가
  // /admin/models에서 새 모델을 등록해도 IAM이 막아 첫 대화 턴에
  // AccessDenied가 나고, 그 실패는 백엔드 로그에만 남는다 — "화면에서 모델을
  // 추가할 수 있다"고 보여주면서 실제로는 cdk deploy가 필요한 상태가 최악이다.
  // (spec 2026-08-01-per-project-model-selection §4)
  assert.match(allActions, /inference-profile\/global\.anthropic\.claude-\*/,
    'instance role can invoke any global Anthropic Claude inference profile');
  assert.match(allActions, /foundation-model\/anthropic\.claude-\*/,
    'instance role can invoke any Anthropic Claude foundation model');
  // 폴백 기본값이 그 와일드카드에 실제로 포함되는지. MODEL은 카탈로그의 시드
  // 목록에 없지만(콤보박스에 뜨지 않는다) 구 프로젝트와 미지정 폴백으로
  // 남으므로 invoke 가능해야 한다.
  assert.ok(MODEL.startsWith('global.anthropic.claude-'),
    `MODEL ${MODEL} must fall under the global.anthropic.claude-* wildcard`);
```

같은 파일 상단의 import에 `MODEL`을 추가한다(`backend-permissions`에서 이미 export되어 있다). 예: `import { MODEL } from '../lib/backend-permissions';` — 기존 import 구문이 있으면 거기에 합친다.

- [ ] **Step 2: Run the assertion to verify it fails**

Run: `cd infra && npm test`
Expected: FAIL — `instance role can invoke any global Anthropic Claude inference profile` (현재 정책에는 5개 리터럴만 있고 `claude-*`가 없다)

- [ ] **Step 3: Write the implementation**

`infra/lib/backend-permissions.ts` — `INVOKABLE_MODELS` 상수 블록(주석 9~26행 포함)을 다음으로 교체:

```typescript
// invoke를 허용하는 모델 — 명시 목록이 아니라 와일드카드다.
//
// 명시 목록이었을 때의 문제: 모델 카탈로그가 S3로 옮겨가면서
// (/admin/models, spec 2026-08-01) 관리자가 새 Claude 모델을 화면에서 등록할
// 수 있게 됐는데, IAM이 5개만 허용하면 등록해도 첫 대화 턴에 AccessDenied가
// 나고 그 실패는 백엔드 로그에만 남는다. "화면에서 모델을 추가할 수 있다"고
// 보여주면서 실제로는 cdk deploy가 필요한 상태가 최악이므로 와일드카드로
// 넓힌다 — 이것이 그 기능이 성립하는 유일한 조건이다.
//
// 허용 범위가 "모든 global Anthropic Claude 추론 프로파일"로 넓어지는 것은
// 의도된 교환이다. 이 롤은 Bedrock invoke 외에 하는 일이 없고(S3는 별도
// statement), 어떤 Claude 모델을 부르든 데이터 경계는 같다.
//
// inference-profile은 global.* 프리픽스, foundation-model은 없는 형태 —
// 둘 다 필요하다(프로파일 경유 호출이 내부적으로 후자를 참조한다).
// test/hosting-stack.assert.ts가 이 두 패턴과 MODEL의 포함 여부를 단정한다.
const INVOKABLE_MODEL_ARNS = (account: string) => [
  `arn:aws:bedrock:*:${account}:inference-profile/global.anthropic.claude-*`,
  `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`,
];
```

`backendPolicyStatements`의 첫 statement를 교체:

```typescript
    new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: INVOKABLE_MODEL_ARNS(account),
    }),
```

- [ ] **Step 4: Run the infra suite to verify it passes**

Run: `cd infra && npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/lib/backend-permissions.ts infra/test/hosting-stack.assert.ts
git commit -m "$(cat <<'EOF'
feat(infra): Bedrock invoke를 claude-* 와일드카드로

관리자 화면에서 모델을 등록할 수 있게 되면서(spec 2026-08-01) 명시 목록이
기능을 막는 쪽이 됐다 — 등록해도 IAM이 거부해 첫 대화 턴에 AccessDenied가
나고 그 실패는 백엔드 로그에만 남는다. "화면에서 추가할 수 있다"고 보여주면서
실제로는 cdk deploy가 필요한 상태가 최악이다.

MODEL(=opus-4-8) 기본값은 그대로 둔다. 카탈로그 시드에는 없지만 구 프로젝트와
미지정 폴백으로 남으므로, 와일드카드에 포함되는지를 테스트가 단정한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 프론트 API 클라이언트 + 타입

**Files:**
- Create: `frontend/lib/api/models.ts`
- Modify: `frontend/lib/api/types.ts` (`ProjectSummary`, `ProjectDetail` 추가)
- Modify: `frontend/lib/api/client.ts` (`createProject`, `getProject`)
- Modify: `frontend/test/msw/handlers.ts` (`/models` 기본 핸들러)
- Test: `frontend/lib/api/models.test.ts`

**Interfaces:**
- Consumes: 백엔드 Task 4·5의 응답 모양; 기존 `apiFetch`(`lib/api/http.ts`), `API_BASE_URL`/`ApiError`(`lib/api/client.ts`).
- Produces:
  - `types.ts`: `ProjectSummary.model_id?: string | null`; `interface ProjectDetail { project_id: string; name: string | null; created_at: string | null; model_id: string | null }`
  - `models.ts`: `interface ModelOption { name: string; model_id: string }`; `interface AdminModel extends ModelOption { display: boolean }`; `listModels(): Promise<ModelOption[]>`; `listAdminModels(): Promise<AdminModel[]>`; `addModel(name, modelId, display): Promise<AdminModel>`; `patchModel(modelId, patch: {name?: string; display?: boolean}): Promise<AdminModel>`; `deleteModel(modelId): Promise<void>`
  - `client.ts`: `createProject(projectId: string, name?: string, modelId?: string): Promise<ProjectSummary>`; `getProject(pid: string): Promise<ProjectDetail>`

- [ ] **Step 1: Write the failing tests**

`frontend/lib/api/models.test.ts`:

```tsx
// frontend/lib/api/models.test.ts
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import {
  addModel, deleteModel, listAdminModels, listModels, patchModel,
} from "./models";

describe("models API", () => {
  it("listModels unwraps the models array", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () =>
      HttpResponse.json({ models: [{ name: "Opus 5", model_id: "global.anthropic.claude-opus-5" }] })));
    expect(await listModels()).toEqual([
      { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
    ]);
  });

  it("listModels returns [] when the body is empty", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({})));
    expect(await listModels()).toEqual([]);
  });

  it("listAdminModels keeps the display flag", async () => {
    server.use(http.get(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ models: [{ name: "Opus 5", model_id: "m1", display: false }] })));
    expect(await listAdminModels()).toEqual([
      { name: "Opus 5", model_id: "m1", display: false },
    ]);
  });

  it("addModel posts name, model_id and display", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    const created = await addModel("Opus 4.8", "global.anthropic.claude-opus-4-8", true);
    expect(body).toEqual({ name: "Opus 4.8",
                           model_id: "global.anthropic.claude-opus-4-8",
                           display: true });
    expect(created.display).toBe(true);
  });

  it("addModel surfaces the server's 400 detail", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ detail: "at most 5 models can be displayed" }, { status: 400 })));
    await expect(addModel("여섯", "m6", true)).rejects.toMatchObject({
      status: 400, detail: "at most 5 models can be displayed",
    });
  });

  it("patchModel sends only the given fields", async () => {
    let body: any;
    server.use(http.patch(`${API_BASE_URL}/admin/models/m1`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ name: "n", model_id: "m1", display: false });
    }));
    await patchModel("m1", { display: false });
    expect(body).toEqual({ display: false });
  });

  it("deleteModel tolerates a 204 with no body", async () => {
    server.use(http.delete(`${API_BASE_URL}/admin/models/m1`, () =>
      new HttpResponse(null, { status: 204 })));
    await expect(deleteModel("m1")).resolves.toBeUndefined();
  });
});
```

`frontend/lib/api/client.test.ts`에 추가:

```tsx
  it("createProject sends model_id when given", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ project_id: "p1", name: null, model_id: body.model_id });
    }));
    await createProject("p1", undefined, "global.anthropic.claude-opus-5");
    expect(body).toEqual({ project_id: "p1",
                           model_id: "global.anthropic.claude-opus-5" });
  });

  it("createProject omits model_id when not given", async () => {
    let body: any;
    server.use(http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ project_id: "p1", name: null, model_id: null });
    }));
    await createProject("p1");
    expect(body).toEqual({ project_id: "p1" });
  });

  it("getProject returns the project's metadata", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1`, () =>
      HttpResponse.json({ project_id: "p1", name: "이름", created_at: null,
                          model_id: "global.anthropic.claude-opus-5" })));
    expect(await getProject("p1")).toEqual({
      project_id: "p1", name: "이름", created_at: null,
      model_id: "global.anthropic.claude-opus-5",
    });
  });
```

`client.test.ts` 상단의 import에 `getProject`를 추가한다(기존 import 목록에 합친다).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- lib/api/models.test.ts lib/api/client.test.ts`
Expected: FAIL — `Cannot find module './models'` 및 `getProject is not exported`

- [ ] **Step 3: Write the implementation**

`frontend/lib/api/models.ts`:

```typescript
// frontend/lib/api/models.ts — 모델 카탈로그 클라이언트.
//
// 두 계층이 다른 모양을 받는다: 프로젝트 생성 화면(`listModels`)은 이름과 id만,
// 관리자 화면(`listAdminModels`)은 display 플래그까지. 백엔드가 그렇게 나눠서
// 보내는 이유는 일반 사용자에게 display가 의미가 없기 때문이다 — 여기서
// 필터링하지 않는다.
import { apiFetch } from "./http";

export interface ModelOption {
  name: string;
  model_id: string;
}

export interface AdminModel extends ModelOption {
  display: boolean;
}

// model_id는 영숫자와 `.`·`-`·`:`만 포함하므로 경로 세그먼트에서 이스케이프가
// 필요 없다(adminUsers.ts의 "@" 처리 같은 것이 없는 이유).
export async function listModels(): Promise<ModelOption[]> {
  const body = await apiFetch<{ models: ModelOption[] }>("/models");
  return body?.models ?? [];
}

export async function listAdminModels(): Promise<AdminModel[]> {
  const body = await apiFetch<{ models: AdminModel[] }>("/admin/models");
  return body?.models ?? [];
}

export async function addModel(name: string, modelId: string,
                               display: boolean): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>("/admin/models", {
    method: "POST",
    body: JSON.stringify({ name, model_id: modelId, display }),
  });
  return body as AdminModel;
}

export async function patchModel(
  modelId: string, patch: { name?: string; display?: boolean },
): Promise<AdminModel> {
  const body = await apiFetch<AdminModel>(`/admin/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return body as AdminModel;
}

export async function deleteModel(modelId: string): Promise<void> {
  await apiFetch<null>(`/admin/models/${modelId}`, { method: "DELETE" });
}
```

`frontend/lib/api/types.ts` — `ProjectSummary`에 필드 추가하고 그 아래에 `ProjectDetail`을 넣는다:

```typescript
export interface ProjectSummary {
  project_id: string;
  name: string | null;
  // 목록 응답에만 실림(fail-soft: 상태 파일이 없거나 읽기 실패면 null).
  progress?: ProjectProgress | null;
  // 이 프로젝트가 도는 Bedrock 모델 id. null = 미지정(서버의 env 기본값으로
  // 돈다 — 프론트는 그 값을 알 수 없다).
  model_id?: string | null;
}

// GET /projects/{pid} → ProjectDetail. 헤더의 모델 배지가 부르는 곳이다.
export interface ProjectDetail {
  project_id: string;
  name: string | null;
  created_at: string | null;
  model_id: string | null;
}
```

`frontend/lib/api/client.ts` — `createProject`를 교체하고 `getProject`를 추가한다. `ProjectDetail`을 import 목록에 넣는다:

```typescript
export async function createProject(projectId: string, name?: string,
                                    modelId?: string): Promise<ProjectSummary> {
  const body: { project_id: string; name?: string; model_id?: string } = {
    project_id: projectId,
  };
  if (name !== undefined) body.name = name;
  // 미지정은 키를 아예 빼서 보낸다 — 서버의 optional 필드와 맞고, null을 보내는
  // 것과 결과가 같으므로 더 적게 보내는 쪽을 고른다.
  if (modelId !== undefined) body.model_id = modelId;
  return request<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function getProject(pid: string): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/projects/${encodeURIComponent(pid)}`);
}
```

`frontend/test/msw/handlers.ts` — 기본 핸들러 2개 추가(배열 안, `/projects` 핸들러 아래):

```typescript
  // 프로젝트 생성 화면과 헤더 배지가 모든 화면에서 부른다 — 기본을 두어
  // 화면 테스트가 모델 목록을 신경쓰지 않게 한다.
  http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
  http.get(`${API_BASE_URL}/projects/:pid`, ({ params }) =>
    HttpResponse.json({ project_id: params.pid, name: null, created_at: null,
                        model_id: null })),
```

⚠️ MSW는 먼저 등록된 핸들러가 이긴다. `/projects/:pid`는 `/projects` 뒤에 두어야 목록 요청을 가로채지 않는다.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- lib/api/models.test.ts lib/api/client.test.ts`
Expected: PASS

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api/models.ts frontend/lib/api/models.test.ts \
        frontend/lib/api/types.ts frontend/lib/api/client.ts \
        frontend/lib/api/client.test.ts frontend/test/msw/handlers.ts
git commit -m "$(cat <<'EOF'
feat(models): 프론트 모델 API 클라이언트

listModels(일반)와 listAdminModels(관리자)를 나눠 둔다 — 백엔드가 두 모양을
따로 보내고, 일반 사용자에게 display 플래그는 의미가 없으므로 프론트에서
필터링하지 않는다.

MSW 기본 핸들러의 /projects/:pid는 /projects 뒤에 둔다(먼저 등록된 핸들러가
이기므로 목록 요청을 가로챈다).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 프로젝트 생성 폼의 모델 콤보박스

**Files:**
- Modify: `frontend/components/CreateProjectForm.tsx`
- Modify: `frontend/components/CreateProjectForm.test.tsx`

**Interfaces:**
- Consumes: `listModels`/`ModelOption` (Task 7), `createProject(projectId, name?, modelId?)` (Task 7).
- Produces: 없음 (화면 말단).

- [ ] **Step 1: Write the failing tests**

`frontend/components/CreateProjectForm.test.tsx`의 기존 첫 테스트를 다음으로 교체하고(모델 필드가 생겨 본문이 달라진다) 나머지를 추가한다:

```tsx
  it("submits project_id + name + the selected model", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
        { name: "Sonnet 5", model_id: "global.anthropic.claude-sonnet-5" },
      ] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null,
                                   model_id: body.model_id ?? null });
      }),
    );
    render(<CreateProjectForm onCreated={onCreated} />);
    // 목록이 도착할 때까지 기다린다 — 첫 항목이 기본 선택된다.
    await screen.findByRole("option", { name: "Opus 5" });
    await user.type(screen.getByLabelText("프로젝트 ID"), "pilot2");
    await user.type(screen.getByLabelText("프로젝트 이름 (선택)"), "신규 세션");
    await user.selectOptions(screen.getByLabelText("AI 모델"), "global.anthropic.claude-sonnet-5");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "pilot2", name: "신규 세션",
                           model_id: "global.anthropic.claude-sonnet-5" });
  });

  it("shows model names only — never the raw model id", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
      { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" },
    ] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    const option = await screen.findByRole("option", { name: "Opus 5" });
    // 요구사항: "콤보박스에는 모델 이름만 표시". id는 value에만 있다.
    expect(option.textContent).toBe("Opus 5");
    expect(screen.queryByText(/global\.anthropic/)).toBeNull();
  });

  it("defaults to the first model in the list", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "m-first" },
        { name: "Sonnet 5", model_id: "m-second" },
      ] })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p", name: null, model_id: body.model_id });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await screen.findByRole("option", { name: "Opus 5" });
    await user.type(screen.getByLabelText("프로젝트 ID"), "p");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body.model_id).toBe("m-first");
  });

  it("still creates a project when the model list fails to load", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ detail: "boom" },
                                                                 { status: 500 })),
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: "p", name: null, model_id: null });
      }),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "p");
    // 셀렉트는 비활성이지만 생성은 막지 않는다 — 카탈로그 조회 실패가
    // 프로젝트 생성 전체를 막는 것은 과하다(서버가 env 기본값으로 떨어진다).
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "p" });
  });

  it("disables the select while the list is empty", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })));
    render(<CreateProjectForm onCreated={vi.fn()} />);
    expect(await screen.findByLabelText("AI 모델")).toBeDisabled();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- components/CreateProjectForm.test.tsx`
Expected: FAIL — `Unable to find a label with the text of: AI 모델`

- [ ] **Step 3: Write the implementation**

`frontend/components/CreateProjectForm.tsx` 전체를 교체:

```tsx
"use client";
import { useEffect, useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import { listModels, type ModelOption } from "@/lib/api/models";
import type { ProjectSummary } from "@/lib/api/types";

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 모델 목록은 최대 5개짜리 짧은 목록이라 마운트 시 한 번 받아 온다.
  // 실패는 무해하게 흘린다: 셀렉트가 비활성이 되고 서버가 env 기본값으로
  // 떨어진다 — 카탈로그 조회 실패가 프로젝트 생성 전체를 막는 것은 과하다.
  useEffect(() => {
    let alive = true;
    void listModels()
      .then((list) => {
        if (!alive) return;
        setModels(list);
        setModelId(list[0]?.model_id ?? "");
      })
      .catch(() => {
        if (alive) setModels([]);
      });
    return () => { alive = false; };
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject(projectId.trim(), name.trim() || undefined,
                                          modelId || undefined);
      onCreated(created);
      setProjectId("");
      setName("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("이미 존재하는 프로젝트 ID입니다.");
      } else if (err instanceof ApiError) {
        setError(`프로젝트 생성에 실패했습니다. (${err.status})`);
      } else {
        setError("네트워크 오류로 프로젝트를 생성하지 못했습니다.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-xl border border-slate-200 p-5 mb-8 flex flex-col sm:flex-row sm:items-end gap-3"
    >
      <div className="flex-1">
        <label htmlFor="pid" className="block text-xs text-slate-500 mb-1">
          프로젝트 ID
        </label>
        <input
          id="pid"
          required
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          placeholder="예: pilot2"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="flex-1">
        <label htmlFor="pname" className="block text-xs text-slate-500 mb-1">
          프로젝트 이름 (선택)
        </label>
        <input
          id="pname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="예: 기획전 AI 어시스턴트"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="sm:w-44">
        <label htmlFor="pmodel" className="block text-xs text-slate-500 mb-1">
          AI 모델
        </label>
        {/* 이름만 보여준다 — 모델 id는 value로만 간다. */}
        <select
          id="pmodel"
          value={modelId}
          disabled={models.length === 0}
          onChange={(e) => setModelId(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 bg-white disabled:bg-slate-50 disabled:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          {models.length === 0 && <option value="">기본 모델</option>}
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>{m.name}</option>
          ))}
        </select>
      </div>
      <button
        type="submit"
        disabled={submitting || projectId.trim() === ""}
        className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold"
      >
        프로젝트 생성
      </button>
      {error && <p className="text-sm text-rose-600 w-full sm:w-auto">{error}</p>}
    </form>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- components/CreateProjectForm.test.tsx`
Expected: PASS

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/CreateProjectForm.tsx frontend/components/CreateProjectForm.test.tsx
git commit -m "$(cat <<'EOF'
feat(models): 프로젝트 생성 폼에 모델 콤보박스

이름만 표시하고 model_id는 value로만 보낸다(요구사항). 목록 조회 실패 시엔
셀렉트를 비활성화하되 생성은 막지 않는다 — 서버가 env 기본값으로 떨어지므로
카탈로그 조회 실패가 프로젝트 생성 전체를 막는 것은 과하다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 관리자 모델 관리 화면

**Files:**
- Create: `frontend/components/admin/ModelTable.tsx`, `frontend/components/admin/ModelTable.test.tsx`
- Create: `frontend/components/admin/AddModelModal.tsx`, `frontend/components/admin/AddModelModal.test.tsx`
- Create: `frontend/app/admin/models/page.tsx`
- Modify: `frontend/components/UserMenu.tsx` (메뉴 항목 추가)

**Interfaces:**
- Consumes: `listAdminModels`/`addModel`/`patchModel`/`deleteModel`/`AdminModel` (Task 7), `ApiError` (기존).
- Produces:
  - `ModelTable({ models, onChanged }: { models: AdminModel[]; onChanged: () => void })`
  - `AddModelModal({ onAdded, onClose }: { onAdded: () => void; onClose: () => void })`

- [ ] **Step 1: Write the failing tests**

`frontend/components/admin/ModelTable.test.tsx`:

```tsx
// frontend/components/admin/ModelTable.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ModelTable } from "./ModelTable";
import type { AdminModel } from "@/lib/api/models";

const MODELS: AdminModel[] = [
  { name: "Opus 5", model_id: "global.anthropic.claude-opus-5", display: true },
  { name: "Opus 4.8", model_id: "global.anthropic.claude-opus-4-8", display: false },
];

describe("ModelTable", () => {
  it("shows the name, the model id and the display state", () => {
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    expect(screen.getByText("Opus 5")).toBeInTheDocument();
    // 관리자 화면은 id를 보여준다 — 무엇을 등록했는지 확인해야 한다
    // (콤보박스와 다른 점이다).
    expect(screen.getByText("global.anthropic.claude-opus-5")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Opus 5 표시" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Opus 4.8 표시" })).not.toBeChecked();
  });

  it("toggling display patches the model and reloads", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    let body: any;
    server.use(http.patch(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...MODELS[0], display: false });
      }));
    render(<ModelTable models={MODELS} onChanged={onChanged} />);
    await user.click(screen.getByRole("switch", { name: "Opus 5 표시" }));
    expect(body).toEqual({ display: false });
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows the server's message when a sixth display is rejected", async () => {
    const user = userEvent.setup();
    server.use(http.patch(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-4-8`,
      () => HttpResponse.json({ detail: "at most 5 models can be displayed" },
                              { status: 400 })));
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("switch", { name: "Opus 4.8 표시" }));
    // 프론트가 규칙을 복제하지 않는다 — 서버 문장을 그대로 보여준다.
    expect(await screen.findByRole("alert"))
      .toHaveTextContent("at most 5 models can be displayed");
  });

  it("deletes after a confirmation", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    let deleted = false;
    server.use(http.delete(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      () => { deleted = true; return new HttpResponse(null, { status: 204 }); }));
    render(<ModelTable models={MODELS} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: "Opus 5 삭제" }));
    await user.click(await screen.findByRole("button", { name: "삭제 확인" }));
    expect(deleted).toBe(true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("does not delete when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    let deleted = false;
    server.use(http.delete(
      `${API_BASE_URL}/admin/models/global.anthropic.claude-opus-5`,
      () => { deleted = true; return new HttpResponse(null, { status: 204 }); }));
    render(<ModelTable models={MODELS} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Opus 5 삭제" }));
    await user.click(await screen.findByRole("button", { name: "취소" }));
    expect(deleted).toBe(false);
  });
});
```

`frontend/components/admin/AddModelModal.test.tsx`:

```tsx
// frontend/components/admin/AddModelModal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { AddModelModal } from "./AddModelModal";

describe("AddModelModal", () => {
  it("posts the name, id and display flag then closes", async () => {
    const user = userEvent.setup();
    const onAdded = vi.fn();
    const onClose = vi.fn();
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    render(<AddModelModal onAdded={onAdded} onClose={onClose} />);
    await user.type(screen.getByLabelText("표시 이름"), "Opus 4.8");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-4-8");
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(body).toEqual({ name: "Opus 4.8",
                           model_id: "global.anthropic.claude-opus-4-8",
                           display: true });
    expect(onAdded).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("can add a hidden model", async () => {
    const user = userEvent.setup();
    let body: any;
    server.use(http.post(`${API_BASE_URL}/admin/models`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...body }, { status: 201 });
    }));
    render(<AddModelModal onAdded={vi.fn()} onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("표시 이름"), "숨김");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-4-7");
    await user.click(screen.getByLabelText("콤보박스에 표시"));
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(body.display).toBe(false);
  });

  it("keeps the modal open and shows the detail on 409", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    server.use(http.post(`${API_BASE_URL}/admin/models`, () =>
      HttpResponse.json({ detail: "이미 등록된 모델입니다." }, { status: 409 })));
    render(<AddModelModal onAdded={vi.fn()} onClose={onClose} />);
    await user.type(screen.getByLabelText("표시 이름"), "중복");
    await user.type(screen.getByLabelText("모델 ID"), "global.anthropic.claude-opus-5");
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("이미 등록된 모델입니다.");
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- components/admin/ModelTable.test.tsx components/admin/AddModelModal.test.tsx`
Expected: FAIL — `Cannot find module './ModelTable'`

- [ ] **Step 3: Write the implementation**

`frontend/components/admin/ModelTable.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { deleteModel, patchModel, type AdminModel } from "@/lib/api/models";

export function ModelTable({
  models, onChanged,
}: {
  models: AdminModel[];
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AdminModel | null>(null);

  // 서버가 정책 위반(표시 5개 상한 등)을 알려주면 그 문장을 그대로 보여준다 —
  // 프론트가 규칙을 복제하면 두 곳이 어긋난다(UserTable과 같은 규율).
  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "요청이 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      {error && (
        <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-500">
          <tr className="border-b border-slate-200">
            <th className="py-2 font-medium">이름</th>
            <th className="py-2 font-medium">모델 ID</th>
            <th className="py-2 font-medium">표시</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model_id} className="border-b border-slate-100">
              <td className="py-3">{m.name}</td>
              {/* 관리자는 무엇을 등록했는지 확인해야 하므로 id를 보여준다 —
                  콤보박스가 이름만 보여주는 것과 다른 이유다. */}
              <td className="py-3 font-mono text-xs text-slate-500">{m.model_id}</td>
              <td className="py-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={m.display}
                  aria-label={`${m.name} 표시`}
                  disabled={busy === m.model_id}
                  onClick={() => run(m.model_id,
                    () => patchModel(m.model_id, { display: !m.display }).then(() => undefined))}
                  className={`h-6 w-11 rounded-full transition-colors disabled:opacity-50 ${
                    m.display ? "bg-violet-600" : "bg-slate-300"}`}
                >
                  <span className={`block h-5 w-5 rounded-full bg-white transition-transform ${
                    m.display ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </td>
              <td className="py-3 text-right">
                <button
                  type="button"
                  aria-label={`${m.name} 삭제`}
                  onClick={() => setConfirmDelete(m)}
                  className="text-xs text-rose-600 hover:underline"
                >
                  삭제
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {confirmDelete && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2 className="text-lg font-bold">모델 삭제</h2>
            <p className="mt-2 text-sm text-slate-600">
              {confirmDelete.name}을(를) 목록에서 제거합니다. 이미 이 모델로 만든
              프로젝트는 계속 같은 모델로 돕니다.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = confirmDelete;
                  setConfirmDelete(null);
                  void run(target.model_id, () => deleteModel(target.model_id));
                }}
                className="rounded-lg bg-rose-600 px-4 py-2 text-sm text-white hover:bg-rose-700"
              >
                삭제 확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

`frontend/components/admin/AddModelModal.tsx`:

```tsx
"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { addModel } from "@/lib/api/models";

export function AddModelModal({
  onAdded, onClose,
}: {
  onAdded: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [display, setDisplay] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !modelId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addModel(name.trim(), modelId.trim(), display);
      onAdded();
      onClose();
    } catch (err) {
      // 실패하면 모달을 닫지 않는다 — 입력을 다시 치게 만들지 않기 위해서다.
      setError(err instanceof ApiError ? err.detail : "모델 추가에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
        <h2 className="text-lg font-bold">모델 추가</h2>
        <form onSubmit={submit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="model-name" className="block text-sm font-medium">
              표시 이름
            </label>
            <input
              id="model-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: Opus 4.8"
              className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 text-sm"
            />
          </div>
          <div>
            <label htmlFor="model-id" className="block text-sm font-medium">
              모델 ID
            </label>
            <input
              id="model-id"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="예: global.anthropic.claude-opus-4-8"
              className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 font-mono text-xs"
            />
            <p className="mt-1 text-xs text-slate-500">
              Bedrock 추론 프로파일 id입니다. 배포 리전에서 모델 액세스가 켜져
              있어야 실제로 호출됩니다.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={display}
              onChange={(e) => setDisplay(e.target.checked)}
              aria-label="콤보박스에 표시"
            />
            콤보박스에 표시 (최대 5개)
          </label>
          {error && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={busy || !name.trim() || !modelId.trim()}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700 disabled:opacity-50"
            >
              추가
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

`frontend/app/admin/models/page.tsx`:

```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ModelTable } from "@/components/admin/ModelTable";
import { AddModelModal } from "@/components/admin/AddModelModal";
import { ApiError } from "@/lib/api/client";
import { listAdminModels, type AdminModel } from "@/lib/api/models";

export default function AdminModelsPage() {
  const [models, setModels] = useState<AdminModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setModels(await listAdminModels());
    } catch (err) {
      // 403은 pm이 URL을 직접 친 경우다 — 미들웨어는 UX 게이트일 뿐이고
      // 실제 차단은 여기(백엔드 응답)에서 드러난다.
      setError(err instanceof ApiError && err.status === 403
        ? "관리자 권한이 필요합니다."
        : "모델 목록을 불러오지 못했습니다.");
      setModels([]);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold">모델 관리</h1>
            <p className="mt-1 text-sm text-slate-500">
              프로젝트 생성 화면의 모델 목록입니다. 여러 모델을 등록해 두고 그중
              최대 5개만 표시할 수 있습니다. 이미 만든 프로젝트는 여기서 모델을
              지워도 계속 같은 모델로 돕니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700"
          >
            모델 추가
          </button>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </p>
        )}
        {models === null && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {models !== null && models.length > 0 && (
          <ModelTable models={models} onChanged={reload} />
        )}
        {models !== null && models.length === 0 && !error && (
          <p className="text-sm text-slate-500">등록된 모델이 없습니다.</p>
        )}

        {adding && (
          <AddModelModal onAdded={reload} onClose={() => setAdding(false)} />
        )}
      </main>
    </>
  );
}
```

`frontend/components/UserMenu.tsx:76-83` — 관리자 블록이 지금은 `<Link>` 하나만
감싸므로 프래그먼트로 묶어야 한다. 그 블록 전체를 다음으로 교체:

```tsx
          {me.role === "admin" && (
            <>
              <Link
                href="/admin/users"
                className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              >
                사용자 관리
              </Link>
              <Link
                href="/admin/models"
                className="block rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              >
                모델 관리
              </Link>
            </>
          )}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- components/admin/`
Expected: PASS

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/admin/ModelTable.tsx \
        frontend/components/admin/ModelTable.test.tsx \
        frontend/components/admin/AddModelModal.tsx \
        frontend/components/admin/AddModelModal.test.tsx \
        frontend/app/admin/models/page.tsx frontend/components/UserMenu.tsx
git commit -m "$(cat <<'EOF'
feat(models): 관리자 모델 관리 화면

표시 토글·추가·삭제. 정책 위반(표시 5개 상한, 중복)은 서버 문장을 그대로
보여준다 — 프론트가 규칙을 복제하면 두 곳이 어긋난다(UserTable과 같은 규율).

콤보박스와 달리 여기서는 모델 ID를 보여준다: 관리자는 무엇을 등록했는지
확인해야 한다. 삭제 확인문은 기존 프로젝트가 영향받지 않는다고 말한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: 헤더의 현재 모델 배지

**Files:**
- Modify: `frontend/components/AppHeader.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx:142`
- Modify: `frontend/app/projects/[projectId]/dashboard/page.tsx:22`
- Modify: `frontend/app/projects/[projectId]/review/page.tsx:121`
- Modify: `frontend/app/projects/[projectId]/prototypes/page.tsx:171`
- Create: `frontend/components/AppHeader.test.tsx`
- Create: `frontend/lib/useProjectModel.ts`, `frontend/lib/useProjectModel.test.tsx`

**Interfaces:**
- Consumes: `getProject` (Task 7), `listModels`/`ModelOption` (Task 7).
- Produces:
  - `useProjectModel(projectId: string | undefined) -> string | null` — 표시할 라벨(이름이 있으면 이름, 없으면 model_id 원문, 미지정이면 null)
  - `AppHeader({ activeTab, projectId, modelLabel }: { activeTab: HeaderTab; projectId?: string; modelLabel?: string | null })`

- [ ] **Step 1: Write the failing tests**

`frontend/lib/useProjectModel.test.tsx`:

```tsx
// frontend/lib/useProjectModel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { useProjectModel } from "./useProjectModel";

function Probe({ pid }: { pid?: string }) {
  const label = useProjectModel(pid);
  return <span data-testid="label">{label ?? "(없음)"}</span>;
}

describe("useProjectModel", () => {
  it("resolves the catalog name for the project's model", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
        project_id: "p1", name: null, created_at: null,
        model_id: "global.anthropic.claude-opus-5" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [
        { name: "Opus 5", model_id: "global.anthropic.claude-opus-5" }] })),
    );
    render(<Probe pid="p1" />);
    expect(await screen.findByText("Opus 5")).toBeInTheDocument();
  });

  it("falls back to the raw model id when the catalog no longer has it", async () => {
    // 값을 복사해 두는 설계의 결과가 화면에서도 정직하게 드러나야 한다:
    // 관리자가 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있다.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
        project_id: "p1", name: null, created_at: null,
        model_id: "global.anthropic.claude-gone" })),
      http.get(`${API_BASE_URL}/models`, () => HttpResponse.json({ models: [] })),
    );
    render(<Probe pid="p1" />);
    expect(await screen.findByText("global.anthropic.claude-gone")).toBeInTheDocument();
  });

  it("is null when the project has no model", async () => {
    // 서버의 env 기본값이 무엇인지 프론트는 알 수 없다 — 추측한 이름을
    // 보여주는 것보다 아무것도 안 보여주는 게 낫다.
    server.use(http.get(`${API_BASE_URL}/projects/p1`, () => HttpResponse.json({
      project_id: "p1", name: null, created_at: null, model_id: null })));
    render(<Probe pid="p1" />);
    expect(await screen.findByText("(없음)")).toBeInTheDocument();
  });

  it("is null when the project fetch fails", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1`, () =>
      HttpResponse.json({ detail: "boom" }, { status: 500 })));
    render(<Probe pid="p1" />);
    expect(await screen.findByText("(없음)")).toBeInTheDocument();
  });

  it("fetches nothing without a project id", () => {
    // onUnhandledRequest: "error"이므로, 요청을 보내면 이 테스트가 실패한다.
    render(<Probe />);
    expect(screen.getByTestId("label")).toHaveTextContent("(없음)");
  });
});
```

`frontend/components/AppHeader.test.tsx`:

```tsx
// frontend/components/AppHeader.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  it("shows the model badge when a label is given", () => {
    render(<AppHeader activeTab="workspace" projectId="p1" modelLabel="Opus 5" />);
    expect(screen.getByText("Opus 5")).toBeInTheDocument();
  });

  it("shows no model badge without a label", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.queryByTestId("model-badge")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- lib/useProjectModel.test.tsx components/AppHeader.test.tsx`
Expected: FAIL — `Cannot find module './useProjectModel'`

- [ ] **Step 3: Write the implementation**

`frontend/lib/useProjectModel.ts`:

```typescript
// frontend/lib/useProjectModel.ts
//
// 헤더 배지가 보여줄 모델 라벨. 프로젝트마다 모델이 다르면 지금 무엇으로
// 도는지 화면에 없으면 알 수 없다.
//
// 두 번 부르는 이유: 프로젝트는 model_id만 알고(매니페스트에 복사된 값),
// 사람이 읽는 이름은 카탈로그에만 있다. 대조 실패는 정상 경로다 — 관리자가
// 카탈로그에서 지운 모델로 도는 프로젝트가 있을 수 있고, 그때는 id 원문을
// 보여준다(값을 복사해 두는 설계의 결과가 화면에서도 정직해야 한다).
"use client";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/api/client";
import { listModels } from "@/lib/api/models";

export function useProjectModel(projectId: string | undefined): string | null {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setLabel(null);
      return;
    }
    let alive = true;
    // 실패는 배지가 빠지는 것으로 끝난다 — 화면의 다른 것을 막지 않는다.
    void Promise.all([
      getProject(projectId),
      listModels().catch(() => []),
    ])
      .then(([project, models]) => {
        if (!alive) return;
        const id = project.model_id;
        if (!id) {
          // 미지정: 서버가 env 기본값으로 도는데 그 값을 프론트는 알 수 없다.
          setLabel(null);
          return;
        }
        setLabel(models.find((m) => m.model_id === id)?.name ?? id);
      })
      .catch(() => {
        if (alive) setLabel(null);
      });
    return () => { alive = false; };
  }, [projectId]);

  return label;
}
```

`frontend/components/AppHeader.tsx` — 시그니처와 배지 영역을 교체:

```tsx
export function AppHeader({
  activeTab,
  projectId,
  modelLabel,
}: {
  activeTab: HeaderTab;
  projectId?: string;
  // 이 프로젝트가 도는 모델의 표시 이름. null/undefined면 배지를 그리지
  // 않는다 — 프로젝트가 없는 화면이거나, 모델 미지정(서버 env 기본값)이다.
  modelLabel?: string | null;
}) {
```

`<div className="flex items-center gap-3">` 안, "Bedrock 연결됨" 배지 **앞**에 추가:

```tsx
          {modelLabel && (
            <span
              data-testid="model-badge"
              title="이 프로젝트가 사용하는 AI 모델"
              className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-violet-50 text-violet-700 border border-violet-200"
            >
              {modelLabel}
            </span>
          )}
```

네 화면에 배선한다. 각 파일에서:

1. import 추가: `import { useProjectModel } from "@/lib/useProjectModel";`
2. 컴포넌트 본문 상단(다른 훅들과 같은 자리)에 `const modelLabel = useProjectModel(projectId);`
3. `<AppHeader ... />`에 `modelLabel={modelLabel}` 추가

예: `frontend/app/projects/[projectId]/workspace/page.tsx:142`

```tsx
      <AppHeader activeTab="workspace" projectId={projectId} modelLabel={modelLabel} />
```

같은 패턴을 `dashboard/page.tsx:22`(`activeTab="dashboard"`), `review/page.tsx:121`(`activeTab="review"`), `prototypes/page.tsx:171`(`activeTab="prototypes"`)에 적용한다.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- lib/useProjectModel.test.tsx components/AppHeader.test.tsx`
Expected: PASS

- [ ] **Step 5: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS. 네 화면의 기존 테스트가 이제 `GET /projects/:pid`를 부르는데, Task 7이 MSW 기본 핸들러를 넣었으므로 통과해야 한다. `onUnhandledRequest: "error"`로 실패하는 화면이 있으면 그 테스트에 핸들러가 아니라 **기본 핸들러 순서**를 확인한다(`/projects/:pid`가 `/projects` 뒤에 있어야 한다).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/AppHeader.tsx frontend/components/AppHeader.test.tsx \
        frontend/lib/useProjectModel.ts frontend/lib/useProjectModel.test.tsx \
        frontend/app/projects/\[projectId\]/workspace/page.tsx \
        frontend/app/projects/\[projectId\]/dashboard/page.tsx \
        frontend/app/projects/\[projectId\]/review/page.tsx \
        frontend/app/projects/\[projectId\]/prototypes/page.tsx
git commit -m "$(cat <<'EOF'
feat(models): 헤더에 현재 모델 배지

프로젝트마다 모델이 다르면 지금 무엇으로 도는지 화면에 없으면 알 수 없다.

카탈로그에서 지워진 모델은 id 원문을 그대로 보여준다 — 값을 복사해 두는
설계의 결과가 화면에서도 정직해야 한다. 모델 미지정(null)이면 배지를 그리지
않는다: 서버의 env 기본값을 프론트가 알 방법이 없으니 추측한 이름보다 공백이
낫다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: 문서 현행화

**Files:**
- Modify: `README.md`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: Task 1~10의 최종 동작.
- Produces: 없음.

- [ ] **Step 1: README의 `ANTHROPIC_MODEL` 행을 교체**

`README.md`의 백엔드 환경 변수 표에서 `ANTHROPIC_MODEL` 행을 다음으로 바꾼다:

```
| `ANTHROPIC_MODEL` | — (EC2 배포는 `global.anthropic.claude-opus-4-8`) | **폴백** Bedrock 추론 프로파일 id. 프로젝트가 자기 모델을 가지면 그것이 이긴다(`app.project_model`) — 이 값은 이 기능 이전에 만든 프로젝트와 모델 미지정 시에만 쓰인다. IAM은 `global.anthropic.claude-*`를 전부 허용하므로 관리자 화면에서 등록한 모델은 배포 없이 바로 돈다 |
```

- [ ] **Step 2: README에 모델 선택 절을 추가**

"참고" 절의 **Bedrock 모델과 샘플링 파라미터** 항목을 다음으로 교체한다:

```markdown
- **모델 선택은 프로젝트 단위다.** 프로젝트 생성 화면의 콤보박스에서 고른
  모델이 그 프로젝트의 Discovery 에이전트·프로토타입 빌드 에이전트·설문 문항
  생성에 전부 주입된다(`app.project_model`). 고를 수 있는 목록은 관리자
  화면(`/admin/models`)에서 편집하고 S3의 `models/catalog.json`에 저장된다 —
  파일이 없으면 코드의 시드 4개(Opus 5 / Opus 4.6 / Sonnet 5 / Sonnet 4.6)로
  떨어지고, 그 시드는 관리자가 처음 수정할 때 비로소 파일이 된다.

  **콤보박스에 뜨는 것은 최대 5개**이고, 그 5개를 고르는 것은 관리자가 켜고
  끄는 표시 플래그다(등록 수 자체는 무제한). 여섯 번째를 켜려 하면 400과 함께
  "무엇을 먼저 내리라"는 안내가 온다 — 정렬 상위 5개로 자르면 밀려난 모델이
  화면에서 조용히 사라진다.

  프로젝트가 고른 값은 매니페스트에 **복사**된다. 관리자가 카탈로그에서 그
  모델을 지워도 진행 중인 프로젝트는 계속 같은 모델로 돌고, 헤더 배지에는
  이름 대신 모델 id 원문이 뜬다.

  **IAM은 `global.anthropic.claude-*`를 전부 허용한다**
  (`infra/lib/backend-permissions.ts`). 명시 목록이던 시절에는 관리자가 새
  모델을 등록해도 첫 대화 턴에 `AccessDenied`가 났다 — 화면에서 추가할 수
  있다고 보여주면서 실제로는 `cdk deploy`가 필요한 상태가 최악이라 넓혔다.
  단 **배포 리전에서 그 모델의 Bedrock 액세스가 켜져 있어야** 실제로 돈다
  (IAM과 별개다).

  `PATHFINDER_DISCOVERY_DRIVER=strands` 폴백 드라이버는 프로젝트별 모델을
  **무시하고** 전역 `ANTHROPIC_MODEL`을 쓴다(의도된 범위 제외 —
  `agent/driver.py` 주석).

  **Claude Opus 4.7 이후 모델(Opus 4.7·4.8·5, Sonnet 5)은 `temperature`/`top_p`/`top_k`와
  `budget_tokens`를 제거했다** — 보내면 요청 전체가 `ValidationException`으로 실패한다
  (`` `temperature` is deprecated for this model ``). 백엔드 드라이버는 원래 보내지 않지만,
  **빌드 에이전트가 생성하는 프로토타입 코드가 이걸 넣으면 런타임에 깨진다.** 그래서
  `proto-config/CLAUDE.md`에 금지 지침을 두어 에이전트가 처음부터 넣지 않게 한다. 모델 ID를
  정규식으로 검사해 특정 모델만 제외하는 우회는 만들지 않는다 — 기본 모델이 env로 바뀌면
  패턴이 새 모델을 놓쳐 같은 에러가 재발한다(실제로 `opus-(4-8|5)` 패턴이 `sonnet-5`를
  놓쳤다). 추론 깊이가 필요하면 `thinking: {type: "adaptive"}`를 쓴다.
```

- [ ] **Step 3: S3 프리픽스 목록을 갱신**

README에서 S3 프리픽스를 열거하는 두 곳을 고친다:

1. 스택 구성 표의 `PathfinderDrillStack` 행: `S3 아티팩트 버킷(`projects/*` + `sessions/*` + `surveys/*`)` → `(`projects/*` + `sessions/*` + `surveys/*` + `models/*`)`
2. 설문 데이터 단락 뒤에 한 문장 추가:

```markdown
같은 이유로 **모델 카탈로그도 버킷 루트(`models/catalog.json`)에 있다** — 프로젝트
생성 화면이 프로젝트가 하나도 없는 상태에서 이것을 읽어야 하므로 프로젝트
프리픽스 안에 둘 수 없다.
```

⚠️ **`BACKEND_BUCKET_PREFIXES`에 `models/*`를 추가해야 한다.** 이것은 코드
변경이므로 Step 4에서 처리한다.

- [ ] **Step 4: IAM 프리픽스에 `models/*` 추가**

`infra/lib/backend-permissions.ts`의 `BACKEND_BUCKET_PREFIXES`를 교체:

```typescript
// 백엔드가 쓰는 아티팩트 버킷 프리픽스. 프로젝트 데이터는 projects/,
// strands 세션은 sessions/ 아래에 있고 — surveys/와 models/는 프로젝트
// 프리픽스 밖에 있어야 한다.
//
// surveys/by-token/{token}.json은 토큰 -> 프로토타입 단방향 인덱스다. 공개
// 설문 링크(/survey/{token})는 토큰이 어느 프로젝트 것인지 알기 전에 이걸
// 읽어야 하므로 projects/{pid}/ 안에 둘 수 없다
// (backend/pathfinder/app.py의 surveys_root_s3_factory).
//
// models/catalog.json은 프로젝트 생성 화면의 모델 목록이다. 프로젝트가 하나도
// 없는 상태에서 읽히므로 같은 이유로 프로젝트 프리픽스 밖에 있다
// (backend/pathfinder/model_catalog.py의 CATALOG_KEY).
//
// 실측 배포 버그: 이 목록에 surveys/*가 없어서 설문 생성이 전부 500이었고,
// 백엔드 로그에만 AccessDenied(PutObject on surveys/by-token/...)가 남았다.
// 설문 기능이 들어온 뒤 이 헬퍼가 함께 갱신되지 않은 것이 원인 —
// backend/pathfinder/survey/store.py의 TOKEN_INDEX_PREFIX와 짝이다.
// ListBucket에도 필요하다: purge()의 토큰 회수는 delete_prefix(=list 후
// delete_objects)를 타므로 목록 권한이 없으면 조용히 0건을 지운다.
const BACKEND_BUCKET_PREFIXES = [
  'projects/*', 'sessions/*', 'surveys/*', 'models/*',
] as const;
```

`infra/test/hosting-stack.assert.ts:14`의 리터럴 목록에도 넣는다(이 파일은
`BACKEND_BUCKET_PREFIXES`를 import하지 않고 같은 목록을 따로 적어 둔다 — 두 곳이
어긋나면 단정이 무의미해지므로 함께 고쳐야 한다):

```typescript
const BUCKET_PREFIXES = ['projects/*', 'sessions/*', 'surveys/*', 'models/*'];
```

- [ ] **Step 5: `backend/.env.example`에 주석 추가**

`ANTHROPIC_MODEL` 항목 위에 한 줄:

```
# 폴백 모델. 프로젝트가 생성 시 고른 모델이 이것보다 우선한다(app.project_model).
```

- [ ] **Step 6: 인프라 테스트 실행**

Run: `cd infra && npm test`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add README.md backend/.env.example infra/lib/backend-permissions.ts \
        infra/test/hosting-stack.assert.ts
git commit -m "$(cat <<'EOF'
docs(models): 프로젝트별 모델 선택 현행화 + IAM에 models/* 추가

ANTHROPIC_MODEL이 이제 "기본"이 아니라 "폴백"이다 — 표와 참고 절이 그렇게
말해야 한다. 표시 5개의 기준(관리자 플래그, 정렬 아님)과 카탈로그 삭제가
진행 중 프로젝트에 영향이 없다는 것도 기록한다.

models/*는 네 번째 버킷 프리픽스다. surveys/*가 빠져서 설문 생성이 전부
500이었던 것과 같은 실수를 반복하지 않게 IAM 목록에 함께 넣는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: 전체 검증

**Files:** 없음 (검증만).

**Interfaces:**
- Consumes: Task 1~11 전부.

- [ ] **Step 1: 백엔드 전체**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS. 시작 전 819개였으므로 대략 850+ 통과.

- [ ] **Step 2: 프론트 유닛 전체**

Run: `cd frontend && npm test`
Expected: PASS. 시작 전 635개였으므로 대략 670+ 통과.

- [ ] **Step 3: 인프라 전체**

Run: `cd infra && npm test`
Expected: PASS

- [ ] **Step 4: 프론트 타입 검사**

Run: `cd frontend && npx tsc --noEmit`
Expected: 오류 없음. `AppHeader`의 새 prop, `ProjectDetail`, `ModelOption`이 전부 맞물려야 한다.

- [ ] **Step 5: CDK 합성**

Run: `cd infra && npx cdk synth PathfinderDrillStack > /dev/null`
Expected: 오류 없음(크리덴셜 불필요 — 드릴 스택은 lookup을 쓰지 않는다).

- [ ] **Step 6: 와일드카드 정책을 눈으로 확인**

Run: `cd infra && npx cdk synth PathfinderDrillStack 2>/dev/null | grep -A 3 'InvokeModel'`
Expected: 출력에 `inference-profile/global.anthropic.claude-*`와 `foundation-model/anthropic.claude-*`가 보이고, 개별 모델 이름(`claude-opus-4-7` 등)은 없다.

- [ ] **Step 7: 미배포 사실을 기록**

이 계획의 변경은 **배포되지 않았다.** IAM 와일드카드는 `cdk deploy`가 필요하고, 그 전까지는 카탈로그에 `claude-opus-4-6-v1`을 등록해도 첫 대화 턴에 `AccessDenied`가 난다(시드에 들어 있으므로 배포 전에는 그 모델을 고르지 말 것). 이 사실을 최종 보고에 명시한다 — 배포는 사용자의 판단이다.

- [ ] **Step 8: Commit (변경이 있으면)**

검증에서 고친 것이 있으면 커밋한다. 없으면 이 단계를 건너뛴다.

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 구현 |
|---|---|
| §1 카탈로그 S3 + 시드 폴백 + 표시 상한 | Task 1 |
| §1 버킷 미설정 시 읽기 전용 | Task 1 (`ModelCatalog(None)`), Task 3 (`model_catalog()`) |
| §2 매니페스트에 model_id 복사 + 4-tuple | Task 2 |
| §3 `project_model()` 폴백 3단계 | Task 3 |
| §3 주입 3지점 | Task 3 |
| §3 `questionnaire_agent_factory(pid)` | Task 3 |
| §3 StrandsDriver 범위 제외 주석 | Task 3 |
| §4 IAM 와일드카드 + `MODEL` 유지 | Task 6 |
| §5 `/admin/models*` CRUD | Task 4 |
| §5 `GET /models` 축약 | Task 4 |
| §5 `POST /projects` model_id 검증(표시 목록 기준) | Task 5 |
| §5 `GET /projects/{pid}` 신설 | Task 5 |
| §6 콤보박스(이름만, 실패 시 생성 가능) | Task 8 |
| §6 관리자 페이지 | Task 9 |
| §6 배지(원문 폴백, null이면 미표시) | Task 10 |
| §7 테스트 전체 | 각 Task의 Step 1 |

스펙에 없었으나 계획에서 추가한 것: **`models/*` IAM 프리픽스**(Task 11 Step 4). 스펙이 카탈로그를 S3에 두기로 하면서 `BACKEND_BUCKET_PREFIXES`를 언급하지 않았는데, 넣지 않으면 관리자 화면의 모든 쓰기가 `AccessDenied`로 실패한다 — `surveys/*`가 빠져 설문 생성이 전부 500이었던 것과 같은 구멍이다. Task 11에 넣었다.

**2. 플레이스홀더 스캔**

`TBD`/`TODO`/"적절히 처리" 없음. 모든 코드 단계가 실제 코드를 담고 있다.
자기 검토에서 "파일을 읽고 맞춘다"로 남아 있던 세 곳을 실제 코드를 확인해
정확한 편집으로 바꿨다:
- Task 3 Step 1: `test_routes_surveys.py:42,80`의 `def agent_factory():` →
  `def agent_factory(_pid):`
- Task 9 Step 3: `UserMenu.tsx:76-83` 블록 전문(프래그먼트로 묶어야 한다 —
  지금은 `<Link>` 하나만 감싸고 있다)
- Task 11 Step 4: `hosting-stack.assert.ts:14`의 리터럴 `BUCKET_PREFIXES`
  (이 파일은 `BACKEND_BUCKET_PREFIXES`를 import하지 않고 목록을 따로 적어 둔다)

**3. 타입 일관성**

- `restore_projects` 4-tuple: Task 2가 정의하고 유일한 소비자(`app._lifespan`)를 같은 Task에서 고친다.
- `ModelEntry.model_dump()` → 라우트 응답 `{name, model_id, display}` ↔ 프론트 `AdminModel` 일치.
- `GET /models` → `{name, model_id}` ↔ `ModelOption` 일치.
- `GET /projects/{pid}` → `{project_id, name, created_at, model_id}` ↔ `ProjectDetail` 일치.
- `CatalogError.code` 값 4개(`duplicate`/`too_many_displayed`/`not_found`/`readonly`)가 Task 1의 정의와 Task 4의 `_ERROR_STATUS` 키에서 일치.
- `useProjectModel` 반환 `string | null` ↔ `AppHeader.modelLabel?: string | null` 일치.
- `patchModel(modelId, patch)` ↔ 백엔드 `PatchModel {name?, display?}` 일치.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-01-per-project-model-selection.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
