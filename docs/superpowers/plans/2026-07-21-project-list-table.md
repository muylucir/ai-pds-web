# 프로젝트 목록 테이블 + 페이지네이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로젝트 목록을 테이블(ID·이름·진행상황)로 바꾸고 백엔드 페이지네이션을 적용한다.

**Architecture:** `GET /projects`가 `page`/`size` 쿼리를 받아 `{projects, total, page, size}`를 반환하며, 페이지 내 프로젝트만 S3의 `aiplc-state.md`를 직접 읽어(`ensure_workspace` 우회) `progress`를 계산한다(fail-soft). 프론트는 `ProjectList`를 카드 그리드에서 테이블+페이지네이션 컨트롤로 재작성하고, `page.tsx`가 페이지 상태를 소유한다.

**Tech Stack:** FastAPI Query 검증, asyncio.gather, React 19 + Next.js 15 (next/link), Vitest + Testing Library + MSW.

## Global Constraints

- 응답 계약: `GET /projects?page=1&size=10` → `{"projects": [{"project_id", "name", "progress": {"current_stage": str|null, "completed": int, "total": int} | null}], "total": int, "page": int, "size": int}`. `page` ge=1, `size` ge=1 le=50, 범위 밖은 FastAPI 검증 422.
- 진행상황은 S3 `aiplc-docs/aiplc-state.md` **직접** 읽기 — `ensure_workspace` 호출 금지(워크스페이스 lazy 초기화 부작용). 실패(파일 없음/파싱 실패/S3 예외/버킷 미설정)는 해당 프로젝트 `progress: null`로 강등, 목록 응답은 절대 실패하지 않는다.
- 진행 셀 표기: `progress` 있으면 `{current_stage} ({completed}/{total})`, `current_stage`가 null이면 `({completed}/{total})`, `progress`가 null이면 `—`.
- e2e 호환: 행의 이름 링크 텍스트는 `name ?? project_id` — `frontend/e2e/workspace.spec.ts`가 `getByRole("link", { name: new RegExp(pid) })`로 찾는다. 삭제 확인 다이얼로그의 문구/흐름(경고 문구, 취소/삭제, Escape 닫기)은 기존 그대로 유지 — 기존 5개 삭제 테스트가 계약이다.
- 커밋 메시지 말미: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## File Structure

- Modify: `backend/pathfinder/routes/projects.py` — `list_projects` 페이지네이션 + `_progress` 헬퍼
- Modify: `backend/tests/test_routes_projects_list.py` — 페이지·progress 테스트 추가
- Modify: `frontend/lib/api/types.ts` — `ProjectProgress`/`ProjectPage` 추가, `ProjectSummary.progress`
- Modify: `frontend/lib/api/client.ts` — `listProjects(page, size) → ProjectPage`
- Modify: `frontend/test/msw/handlers.ts` — 기본 핸들러를 새 응답 형태로
- Rewrite: `frontend/components/ProjectList.tsx` — 테이블 + 페이지네이션 컨트롤
- Modify: `frontend/components/ProjectList.test.tsx` — 새 props/테이블 테스트
- Modify: `frontend/app/page.tsx` — 페이지 상태 소유 + 새 props 배선

---

### Task 1: 백엔드 — GET /projects 페이지네이션 + progress

**Files:**
- Modify: `backend/pathfinder/routes/projects.py`
- Test: `backend/tests/test_routes_projects_list.py`

**Interfaces:**
- Consumes: `app_module.registry.list_ids()/get_name()`, `app_module.s3_store_factory(pid)`, `app_module.durable_projects_enabled()`, `pathfinder.parsers.state.parse_state_file`.
- Produces: `GET /projects?page=&size=` → Global Constraints의 응답 계약. (프론트 Task 2가 이 계약을 소비.)

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_routes_projects_list.py` 맨 아래에 추가:

```python
# ---- 페이지네이션 + progress (2026-07-21-project-list-table spec) ----
import pytest
from pathfinder import app as app_module
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
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
    fake = FakeS3Store()
    fake.blobs["aiplc-docs/aiplc-state.md"] = _STATE_MD
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: fake)
    _register("pg-state")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-state")
    assert row["progress"] == {
        "current_stage": "DISCOVERY - Envision", "completed": 2, "total": 4}


def test_progress_null_when_state_missing(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    _register("pg-nostate")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-nostate")
    assert row["progress"] is None


def test_progress_null_when_bucket_unset(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    _register("pg-nobucket")
    r = client.get("/projects", params={"size": 50})
    row = next(p for p in r.json()["projects"] if p["project_id"] == "pg-nobucket")
    assert row["progress"] is None


def test_progress_null_when_s3_raises(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
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
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    _register("pg-lazy")
    client.get("/projects", params={"size": 50})
    assert not app_module.registry.has_workspace("pg-lazy")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_list.py -q`
Expected: 신규 테스트 FAIL — 현재 응답에 `total`/`page`/`size`/`progress` 없음, 422 검증 없음. (기존 4개는 통과 유지 — 응답의 `projects` 키는 계속 존재.)

- [ ] **Step 3: 라우트 구현**

`backend/pathfinder/routes/projects.py` — import에 `asyncio`, `Query`, `parse_state_file` 추가하고 `list_projects`를 교체:

```python
import asyncio
from fastapi import APIRouter, HTTPException, Query
from pathfinder.parsers.state import parse_state_file

_STATE_PATH = "aiplc-docs/aiplc-state.md"


async def _progress(pid: str) -> dict | None:
    """페이지 내 프로젝트의 진행상황. S3 직접 읽기 — ensure_workspace를 타지
    않는다(목록 조회가 N개 워크스페이스 lazy 초기화를 유발하면 안 됨).
    fail-soft: 어떤 실패도 None으로 강등, 목록 응답은 막지 않는다."""
    if not app_module.durable_projects_enabled():
        return None
    try:
        md = await app_module.s3_store_factory(pid).get(_STATE_PATH)
        state = parse_state_file(md)
    except Exception:
        return None
    if not state.stages:          # 파일은 있지만 스테이지 파싱 결과가 비면 표시할 게 없다
        return None
    return {
        "current_stage": state.current_stage,
        "completed": sum(1 for s in state.stages if s.status == "completed"),
        "total": len(state.stages),
    }


@router.get("/projects")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50)):
    ids = app_module.registry.list_ids()
    total = len(ids)
    page_ids = ids[(page - 1) * size : page * size]
    progresses = await asyncio.gather(*(_progress(pid) for pid in page_ids))
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid), "progress": prog}
            for pid, prog in zip(page_ids, progresses)
        ],
        "total": total,
        "page": page,
        "size": size,
    }
```

(기존 상단 주석 "Minimal, in-memory listing only…"는 "페이지 단위 목록 + S3 진행상황(fail-soft)"로 갱신. `HTTPException` import는 기존 것 유지.)

- [ ] **Step 4: 통과 확인 + 전체 스위트**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_projects_list.py -q && .venv/bin/python -m pytest -q`
Expected: 파일 내 전부 PASS(기존 4 + 신규 8 = 12), 전체 스위트 그린(180 + 8 = 188).

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/routes/projects.py backend/tests/test_routes_projects_list.py
git commit -m "feat(backend): paginate GET /projects and attach fail-soft S3 progress

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 프론트엔드 — 테이블 + 페이지네이션

**Files:**
- Modify: `frontend/lib/api/types.ts`, `frontend/lib/api/client.ts`, `frontend/test/msw/handlers.ts`
- Rewrite: `frontend/components/ProjectList.tsx`
- Modify: `frontend/components/ProjectList.test.tsx`, `frontend/app/page.tsx`

**Interfaces:**
- Consumes: Task 1의 응답 계약.
- Produces:
  - `listProjects(page = 1, size = 10): Promise<ProjectPage>`
  - `ProjectList({ data: ProjectPage, onDeleted: () => void, onPageChange: (page: number) => void })`
  - `ProjectProgress { current_stage: string | null; completed: number; total: number }`
  - `ProjectPage { projects: ProjectSummary[]; total: number; page: number; size: number }`
  - `ProjectSummary`에 `progress?: ProjectProgress | null` 추가.

- [ ] **Step 1: 타입·클라이언트·MSW 갱신**

`frontend/lib/api/types.ts` — `ProjectSummary`를 다음으로 교체(주석 갱신 포함):

```ts
// GET /projects?page&size → ProjectPage; POST /projects → ProjectSummary.
export interface ProjectProgress {
  current_stage: string | null;
  completed: number;
  total: number;
}

export interface ProjectSummary {
  project_id: string;
  name: string | null;
  // 목록 응답에만 실림(fail-soft: 상태 파일이 없거나 읽기 실패면 null).
  progress?: ProjectProgress | null;
}

export interface ProjectPage {
  projects: ProjectSummary[];
  total: number;
  page: number;
  size: number;
}
```

`frontend/lib/api/client.ts` — import에 `ProjectPage` 추가, `listProjects` 교체:

```ts
export async function listProjects(page = 1, size = 10): Promise<ProjectPage> {
  return request<ProjectPage>(`/projects?page=${page}&size=${size}`);
}
```

`frontend/test/msw/handlers.ts` — 기본 목록 핸들러 교체:

```ts
  http.get(`${API_BASE_URL}/projects`, () =>
    HttpResponse.json({ projects: [], total: 0, page: 1, size: 10 })),
```

- [ ] **Step 2: 실패 테스트 작성 — 테이블·진행 셀·페이지네이션**

`frontend/components/ProjectList.test.tsx`에서 상단 `PROJECTS` 상수를 `ProjectPage` 형태로 바꾸고 기존 5개 삭제 테스트의 `<ProjectList projects={PROJECTS} onDeleted={...} />`를 `<ProjectList data={PAGE} onDeleted={...} onPageChange={vi.fn()} />`로 갱신:

```tsx
const PAGE = {
  projects: [
    { project_id: "p1", name: "워크숍 A", progress: { current_stage: "Envision", completed: 2, total: 8 } },
    { project_id: "p2", name: null, progress: null },
  ],
  total: 2, page: 1, size: 10,
};
```

그리고 새 describe 추가:

```tsx
describe("ProjectList table + pagination", () => {
  it("테이블에 ID·이름·진행상황 컬럼과 행 데이터를 렌더한다", () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "프로젝트 ID" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "프로젝트명" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "진행상황" })).toBeInTheDocument();
    expect(screen.getByText("p1")).toBeInTheDocument();
    expect(screen.getByText("워크숍 A")).toBeInTheDocument();
    expect(screen.getByText("Envision (2/8)")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();          // progress null
  });

  it("이름 링크는 대시보드로 가고, 이름 없으면 ID를 링크 텍스트로 쓴다", () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("link", { name: "워크숍 A" })).toHaveAttribute(
      "href", "/projects/p1/dashboard");
    expect(screen.getByRole("link", { name: "p2" })).toHaveAttribute(
      "href", "/projects/p2/dashboard");
  });

  it("current_stage가 null이면 카운트만 보여준다", () => {
    const page = { ...PAGE, projects: [
      { project_id: "p3", name: null, progress: { current_stage: null, completed: 1, total: 4 } }] };
    render(<ProjectList data={page} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText("(1/4)")).toBeInTheDocument();
  });

  it("페이지네이션: 총 건수·현재/전체 페이지·이전/다음 버튼", async () => {
    const onPageChange = vi.fn();
    const page = { ...PAGE, total: 23, page: 2, size: 10 };
    render(<ProjectList data={page} onDeleted={vi.fn()} onPageChange={onPageChange} />);
    expect(screen.getByText("총 23건")).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "다음 페이지" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
    await userEvent.setup().click(screen.getByRole("button", { name: "이전 페이지" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("첫/마지막 페이지에서 이전/다음이 비활성화된다", () => {
    const first = { ...PAGE, total: 11, page: 1, size: 10 };
    const { rerender } = render(
      <ProjectList data={first} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "이전 페이지" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 페이지" })).toBeEnabled();
    rerender(<ProjectList data={{ ...first, page: 2 }} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "다음 페이지" })).toBeDisabled();
  });

  it("total이 0이면 빈 목록 문구를 보여준다", () => {
    render(<ProjectList data={{ projects: [], total: 0, page: 1, size: 10 }}
                        onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText(/아직 생성된 프로젝트가 없습니다/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 실패 확인**

Run: `cd frontend && npx vitest run components/ProjectList.test.tsx`
Expected: FAIL — props 형태 변경으로 컴파일/렌더 실패.

- [ ] **Step 4: ProjectList 재작성**

`frontend/components/ProjectList.tsx` 전체 교체. 삭제 다이얼로그 로직(state/Escape/confirmDelete)은 기존 코드를 그대로 옮긴다:

```tsx
"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ProjectPage, ProjectProgress, ProjectSummary } from "@/lib/api/types";
import { deleteProject } from "@/lib/api/client";

function progressLabel(p: ProjectProgress | null | undefined): string {
  if (!p) return "—";
  const count = `(${p.completed}/${p.total})`;
  return p.current_stage ? `${p.current_stage} ${count}` : count;
}

export function ProjectList({
  data,
  onDeleted,
  onPageChange,
}: {
  data: ProjectPage;
  onDeleted: () => void;
  onPageChange: (page: number) => void;
}) {
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  if (data.total === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.size));

  return (
    <>
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
              <th scope="col" className="px-4 py-3 font-medium">프로젝트 ID</th>
              <th scope="col" className="px-4 py-3 font-medium">프로젝트명</th>
              <th scope="col" className="px-4 py-3 font-medium">진행상황</th>
              <th scope="col" className="px-4 py-3 w-12">
                <span className="sr-only">삭제</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.projects.map((p) => (
              // relative + 이름 링크의 after:inset-0 스트레치드 링크 — 행 어디를
              // 클릭해도 대시보드로 이동하되, 삭제 버튼은 z-10으로 위에 띄운다.
              <tr key={p.project_id} className="relative border-b border-slate-100 last:border-0 hover:bg-violet-50/40">
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{p.project_id}</td>
                <td className="px-4 py-3 font-medium">
                  <Link
                    href={`/projects/${p.project_id}/dashboard`}
                    className="text-slate-900 hover:text-violet-700 after:absolute after:inset-0 after:content-['']"
                  >
                    {p.name ?? p.project_id}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-600">{progressLabel(p.progress)}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    type="button"
                    aria-label={`${p.name ?? p.project_id} 프로젝트 삭제`}
                    onClick={() => {
                      setError(null);
                      setTarget(p);
                    }}
                    className="relative z-10 w-8 h-8 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 inline-flex items-center justify-center"
                  >
                    🗑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-4 text-sm text-slate-500">
        <span>총 {data.total}건</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label="이전 페이지"
            disabled={data.page <= 1}
            onClick={() => onPageChange(data.page - 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            ‹ 이전
          </button>
          <span>{data.page} / {totalPages}</span>
          <button
            type="button"
            aria-label="다음 페이지"
            disabled={data.page >= totalPages}
            onClick={() => onPageChange(data.page + 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            다음 ›
          </button>
        </div>
      </div>

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

- [ ] **Step 5: page.tsx 배선**

`frontend/app/page.tsx`의 본문 교체:

```tsx
"use client";
import { useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CreateProjectForm } from "@/components/CreateProjectForm";
import { ProjectList } from "@/components/ProjectList";
import { listProjects } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function Home() {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useAsync(() => listProjects(page), [page]);
  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">프로젝트</h1>
          <p className="text-sm text-slate-500 mt-1">
            워크숍 세션을 개설하고 Discovery를 시작하세요.
          </p>
        </div>
        <CreateProjectForm onCreated={reload} />
        {loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {error && (
          <p className="text-sm text-rose-600">
            프로젝트 목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요.
          </p>
        )}
        {data && (
          <ProjectList
            data={data}
            onDeleted={() => {
              // 삭제로 현재 페이지가 비면 이전 페이지로 (page 상태 변경이 곧 리로드)
              if (data.projects.length === 1 && page > 1) setPage(page - 1);
              else reload();
            }}
            onPageChange={setPage}
          />
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 6: 통과 확인 + 전체 스위트 + 타입체크**

Run: `cd frontend && npx vitest run components/ProjectList.test.tsx && npm test && npx tsc --noEmit`
Expected: ProjectList 테스트 전부 PASS(기존 5 갱신 + 신규 6 = 11), 전체 스위트 그린(기존 218 + 신규 6 = 224), tsc 클린. 다른 테스트가 구 `listProjects` 배열 반환을 참조하면 함께 갱신(검색: `grep -rn "listProjects" frontend --include="*.test.*" --include="*.tsx" | grep -v node_modules`).

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/api/types.ts frontend/lib/api/client.ts frontend/test/msw/handlers.ts \
        frontend/components/ProjectList.tsx frontend/components/ProjectList.test.tsx frontend/app/page.tsx
git commit -m "feat(frontend): project list as table with pagination and progress column

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** §1 페이지네이션/progress/fail-soft/ensure_workspace 우회 → Task 1 (테스트 8건). §2 타입·클라이언트·테이블·진행 셀 3형태·페이지네이션 컨트롤·삭제 흐름 유지·빈 목록·삭제 시 이전 페이지 이동 → Task 2. §3 테스트 항목 전부 매핑. e2e 호환(링크 텍스트 name??pid) → Task 2 Step 4의 Link + Global Constraints. ✓

**Placeholder scan:** 없음.

**Type consistency:** `ProjectPage`/`ProjectProgress`/`listProjects(page,size)`/`ProjectList({data,onDeleted,onPageChange})` — Task 2 내 정의·사용 일치. 백엔드 응답 키(`current_stage/completed/total`)와 프론트 인터페이스 일치. ✓
