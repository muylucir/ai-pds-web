# 대시보드 상태 영속화 + 워크스페이스/리뷰 UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `report_stage`가 `aiplc-state.md`를 코드로 보장하고, 워크스페이스 문서 드롭다운·산출물 zip 다운로드·채팅 stick-to-bottom·수정요청→워크스페이스 이동 4개 UX를 넣는다.

**Architecture:** 백엔드: 신설 `agent/state_sync.py` 순수 함수를 `report_stage`가 fail-soft로 호출; `GET /projects/{pid}/artifacts/archive`가 S3 산출물을 stdlib zipfile로 스트림. 프론트: WorkspaceDocPanel 헤더가 `<select>`, ChatTimeline이 명시적 stick-to-bottom 상태, ApprovalGate 수정요청이 `/workspace?draft=` 라우팅 + ChatInput 프리필.

**Tech Stack:** Python stdlib(zipfile/io/re), FastAPI Response, React 19 + next/navigation, Vitest + Testing Library + MSW, pytest.

## Global Constraints

- 상태 upsert 결과물은 기존 `parse_state_file`(backend/pathfinder/parsers/state.py)이 정상 파싱해야 한다(왕복 테스트 필수). 실전 포맷: `- **Current Stage**: <이름>` 줄 + `## Stage Progress` 아래 `- [x]/- [ ] <이름>[ — 노트]` 줄.
- `report_stage`의 이벤트 emit·반환 문자열(`stage recorded: {stage} ({status})`)·invalid-status 문자열은 불변. upsert 실패는 로그만 남기고 반환은 성공(fail-soft).
- zip 엔트리 키 = 워크스페이스 상대 경로(`aiplc-docs/...`). 산출물 0건 → 404, 미등록 프로젝트 → 404. `Content-Disposition: attachment; filename="{pid}-artifacts.zip"`.
- 채팅 스크롤: `scrollIntoView` 금지(문서 스크롤 오염 — 기존 주석의 실측 버그), 컨테이너 `scrollTop`만 조작. 사용자가 위로 스크롤하면 자동 스크롤 중단, 메시지 전송 시 무조건 재개.
- draft 프리필은 전송하지 않는다(사용자가 이어 써서 보냄). 처리 후 URL에서 `draft` 제거(replaceState).
- 커밋 메시지 말미: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## File Structure

- Create: `backend/pathfinder/agent/state_sync.py` — `upsert_stage(markdown: str | None, stage: str, status: str) -> str` 순수 함수
- Modify: `backend/pathfinder/agent/tools.py` — report_stage가 upsert 호출, driver addendum 한 줄
- Modify: `backend/pathfinder/agent/driver.py` — `_CONTACT_ADDENDUM` 문구 보강
- Modify: `backend/pathfinder/routes/artifacts.py` — archive 엔드포인트
- Modify: `frontend/components/workspace/WorkspaceDocPanel.tsx` — 드롭다운
- Modify: `frontend/components/canvas/ChatTimeline.tsx` — stick-to-bottom
- Modify: `frontend/components/review/ApprovalGate.tsx` + `frontend/app/projects/[projectId]/review/page.tsx` — 수정요청 라우팅
- Modify: `frontend/components/canvas/ChatInput.tsx` + `frontend/app/projects/[projectId]/workspace/page.tsx` — draft 프리필
- Modify: `frontend/lib/api/client.ts` — `downloadArtifactsArchive`
- Modify: `frontend/app/projects/[projectId]/review/page.tsx` — zip 버튼

Task 분할: 백엔드 2개(1: state_sync, 2: archive) + 프론트 3개(3: 드롭다운, 4: 스크롤, 5: 수정요청+draft) + zip 버튼은 Task 2에 프론트까지 포함.

---

### Task 1: state_sync — report_stage가 aiplc-state.md를 보장

**Files:**
- Create: `backend/pathfinder/agent/state_sync.py`
- Modify: `backend/pathfinder/agent/tools.py` (report_stage), `backend/pathfinder/agent/driver.py` (addendum 한 줄)
- Test: `backend/tests/test_state_sync.py` (신설), `backend/tests/test_agent_tools.py` (통합 추가)

**Interfaces:**
- Produces: `upsert_stage(markdown: str | None, stage: str, status: str) -> str` — 갱신된 상태 파일 전문. `build_tools(workspace, rules_dir, emit)` 시그니처 불변(내부에서 워크스페이스 경로로 파일 IO).

- [ ] **Step 1: 실패 테스트 — state_sync 순수 함수**

`backend/tests/test_state_sync.py`:

```python
from pathfinder.agent.state_sync import upsert_stage
from pathfinder.parsers.state import parse_state_file


def test_creates_skeleton_when_no_file():
    md = upsert_stage(None, "Envision", "in_progress")
    state = parse_state_file(md)
    assert state.current_stage == "Envision"
    assert [s.name for s in state.stages] == ["Envision"]
    assert state.stages[0].status == "in_progress"


def test_marks_existing_stage_completed():
    md = upsert_stage(None, "Envision", "in_progress")
    md = upsert_stage(md, "Envision", "completed")
    state = parse_state_file(md)
    assert state.stages[0].status == "completed"


def test_appends_new_stage_to_progress_list():
    md = upsert_stage(None, "Workspace Detection", "completed")
    md = upsert_stage(md, "Envision", "in_progress")
    state = parse_state_file(md)
    assert [s.name for s in state.stages] == ["Workspace Detection", "Envision"]
    assert state.current_stage == "Envision"
    assert state.stages[0].status == "completed"
    assert state.stages[1].status == "in_progress"


def test_completed_does_not_move_current_stage():
    md = upsert_stage(None, "Envision", "in_progress")
    md = upsert_stage(md, "Envision", "completed")
    # Current Stage는 completed로는 안 바뀜 — 다음 in_progress가 갱신
    assert "**Current Stage**: Envision" in md
    md = upsert_stage(md, "Solution Analysis", "in_progress")
    state = parse_state_file(md)
    assert state.current_stage == "Solution Analysis"


def test_matches_stage_by_partial_name_like_parser():
    # 파서와 동일한 관용: 실전 파일은 "Envision (Path A)"처럼 노트가 붙는다.
    existing = """# AI-PLC State
- **Current Stage**: Envision (Path A - Step 1)

## Stage Progress
- [ ] Envision (Path A)
- [ ] Solution Analysis
"""
    md = upsert_stage(existing, "Envision", "completed")
    state = parse_state_file(md)
    envision = next(s for s in state.stages if "Envision" in s.name)
    assert envision.status == "completed"
    # 다른 스테이지는 불변
    assert next(s for s in state.stages if s.name == "Solution Analysis").status == "pending"


def test_preserves_unrelated_content():
    existing = """# AI-PLC State

## Project
- **Name**: TC Copilot

- **Current Stage**: Envision

## Stage Progress
- [ ] Envision

## Notes
- 사용자가 Path A를 선택함.
"""
    md = upsert_stage(existing, "Envision", "completed")
    assert "- **Name**: TC Copilot" in md
    assert "사용자가 Path A를 선택함." in md


def test_roundtrip_with_real_fixture():
    from pathlib import Path
    fixture = (Path(__file__).parent / "fixtures" / "aiplc-state.md").read_text(encoding="utf-8")
    md = upsert_stage(fixture, "Prototype & Validation", "in_progress")
    state = parse_state_file(md)
    target = next(s for s in state.stages if "Prototype" in s.name)
    assert target.status == "in_progress"
    # 나머지 완료 스테이지들은 그대로
    assert sum(1 for s in state.stages if s.status == "completed") >= 5
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_state_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: pathfinder.agent.state_sync`.

- [ ] **Step 3: state_sync.py 구현**

`backend/pathfinder/agent/state_sync.py`:

```python
# backend/pathfinder/agent/state_sync.py — report_stage의 aiplc-state.md 보장.
# 방법론 룰은 스테이지 전이마다 상태 파일 갱신을 명시하지만 이는 프롬프트 규약일
# 뿐이라, 에이전트가 건너뛰면 대시보드/목록/게이트 배지가 전부 빈다(실사고:
# qa-test 프로젝트). 이 모듈이 도구 호출 시점에 기계적으로 upsert한다.
# 출력은 반드시 parsers/state.py의 parse_state_file이 파싱 가능한 포맷.
from __future__ import annotations
import re

_CURRENT = re.compile(r"^(- \*\*Current Stage\*\*: ).*$", re.MULTILINE)
_PROGRESS_HEADER = re.compile(r"^## Stage Progress\s*$", re.MULTILINE)
_CHECK_LINE = re.compile(r"^- \[([ xX])\]\s*(.+)$")

_SKELETON = """# AI-PLC State

- **Current Stage**: {stage}

## Stage Progress
- [{mark}] {stage}
"""


def _mark(status: str) -> str:
    return "x" if status == "completed" else " "


def _names_match(line_name: str, stage: str) -> bool:
    """파서(parse_state_file)의 current-stage 매칭 관용과 동일: 정확 일치
    또는 부분 포함('Envision' ↔ 'Envision (Path A)')."""
    base = line_name.split(" — ")[0].split(" - ")[0].strip()
    return base == stage or stage in base or base in stage


def upsert_stage(markdown: str | None, stage: str, status: str) -> str:
    """상태 파일 전문에 스테이지 전이를 반영해 갱신본을 반환한다.

    - markdown=None(파일 없음): 최소 골격 생성.
    - 기존 체크리스트에서 이름이 맞는 줄의 체크박스를 갱신(노트는 보존).
    - 줄이 없으면 ## Stage Progress 블록 끝에 추가.
    - Current Stage는 in_progress/pending일 때만 stage로 갱신(completed는 유지).
    """
    if markdown is None or markdown.strip() == "":
        return _SKELETON.format(stage=stage, mark=_mark(status))

    lines = markdown.splitlines()
    out: list[str] = []
    in_progress_block = False
    block_end = -1          # ## Stage Progress 블록의 마지막 체크라인 인덱스(out 기준)
    matched = False
    for line in lines:
        if _PROGRESS_HEADER.match(line):
            in_progress_block = True
            out.append(line)
            block_end = len(out)
            continue
        if in_progress_block:
            m = _CHECK_LINE.match(line.strip())
            if m:
                block_end = len(out) + 1
                if not matched and _names_match(m.group(2), stage):
                    matched = True
                    body = m.group(2)
                    out.append(f"- [{_mark(status)}] {body}")
                    continue
            elif line.startswith("## "):
                in_progress_block = False
        out.append(line)

    if not matched:
        if block_end == -1:
            # ## Stage Progress 블록 자체가 없음 — 문서 끝에 블록째 추가
            if out and out[-1].strip() != "":
                out.append("")
            out.append("## Stage Progress")
            out.append(f"- [{_mark(status)}] {stage}")
        else:
            out.insert(block_end, f"- [{_mark(status)}] {stage}")

    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"

    if status != "completed":
        if _CURRENT.search(text):
            text = _CURRENT.sub(rf"\g<1>{stage}", text, count=1)
        else:
            # Current Stage 줄이 없으면 헤더 바로 아래 추가
            text = text.replace("\n", f"\n\n- **Current Stage**: {stage}\n", 1) \
                if text.startswith("#") else f"- **Current Stage**: {stage}\n{text}"
    return text
```

- [ ] **Step 4: state_sync 테스트 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_state_sync.py -q`
Expected: PASS (7 tests). 실패하면 로직 수정(특히 Current-Stage-없는 골격 삽입 경로) — 테스트가 계약이다.

- [ ] **Step 5: 실패 테스트 — report_stage 통합**

`backend/tests/test_agent_tools.py` 맨 아래 추가:

```python
def test_report_stage_writes_state_file(tmp_path):
    from pathfinder.parsers.state import parse_state_file
    ws = tmp_path / "ws"; ws.mkdir()
    tools, _ = _tools(ws, tmp_path / "rules")
    tools["report_stage"](stage="Envision", status="in_progress", summary="시작")
    state_file = ws / "aiplc-docs" / "aiplc-state.md"
    assert state_file.is_file()
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.current_stage == "Envision"
    tools["report_stage"](stage="Envision", status="completed", summary="끝")
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.stages[0].status == "completed"


def test_report_stage_survives_state_write_failure(tmp_path, monkeypatch):
    # fail-soft: 상태 파일 upsert가 터져도 이벤트/반환은 정상.
    ws = tmp_path / "ws"; ws.mkdir()
    emitted = []
    from pathfinder.agent import tools as tools_mod
    monkeypatch.setattr(tools_mod, "upsert_stage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tools = {t.tool_name: t for t in tools_mod.build_tools(str(ws), str(tmp_path / "rules"), emitted.append)}
    out = tools["report_stage"](stage="Envision", status="in_progress")
    assert "stage recorded" in out
    assert emitted and emitted[0].kind == "stage"
```

- [ ] **Step 6: report_stage 수정 + addendum**

`backend/pathfinder/agent/tools.py` — 상단 import에 `import logging` + `from pathfinder.agent.state_sync import upsert_stage`, `_log = logging.getLogger("pathfinder.agent")` 추가. `report_stage` 본문:

```python
    @tool
    def report_stage(stage: str, status: str, summary: str = "") -> str:
        """Discovery 스테이지 전이를 선언한다. aiplc-state.md도 자동 갱신된다.

        Args:
            stage: 스테이지 이름 (예: "Envision").
            status: "pending" | "in_progress" | "completed".
            summary: 한 줄 요약.
        """
        if status not in ("pending", "in_progress", "completed"):
            return f"invalid status '{status}' — use pending|in_progress|completed"
        emit(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage, "status": status, "summary": summary}, ensure_ascii=False)))
        # 상태 파일 보장(코드 강제): 대시보드/목록/게이트가 읽는
        # aiplc-docs/aiplc-state.md를 이 시점에 기계적으로 upsert한다.
        # 실패는 이벤트/반환을 막지 않는다(fail-soft) — 화면 이벤트가 우선.
        try:
            p = _confine(workspace, "aiplc-docs/aiplc-state.md")
            existing = p.read_text(encoding="utf-8") if p.is_file() else None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(upsert_stage(existing, stage, status), encoding="utf-8")
            emit(AgentEvent(kind="file_changed", path="aiplc-docs/aiplc-state.md"))
        except Exception:
            _log.exception("aiplc-state.md upsert failed (stage=%s)", stage)
        return f"stage recorded: {stage} ({status})"
```

`backend/pathfinder/agent/driver.py`의 `_CONTACT_ADDENDUM` 내 report_stage 항목을 다음으로 교체:

```
- 스테이지를 시작/완료할 때마다 report_stage 도구를 호출한다. 이 도구가
  aiplc-state.md를 자동 갱신하므로 상태 파일을 file_write로 직접 만들 필요 없다.
```

- [ ] **Step 7: 전체 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_state_sync.py tests/test_agent_tools.py -q && .venv/bin/python -m pytest -q`
Expected: 신규 9 통과, 전체 그린(188 + 9 = 197).

- [ ] **Step 8: Commit**

```bash
git add backend/pathfinder/agent/state_sync.py backend/pathfinder/agent/tools.py backend/pathfinder/agent/driver.py backend/tests/test_state_sync.py backend/tests/test_agent_tools.py
git commit -m "feat(backend): report_stage now guarantees aiplc-state.md via code (fail-soft upsert)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 산출물 zip — 백엔드 엔드포인트 + 리뷰 버튼

**Files:**
- Modify: `backend/pathfinder/routes/artifacts.py`
- Modify: `frontend/lib/api/client.ts`, `frontend/app/projects/[projectId]/review/page.tsx`
- Test: `backend/tests/test_routes_artifacts.py`, `frontend/app/projects/[projectId]/review/page.test.tsx` (있으면; 없으면 신설 최소 테스트)

**Interfaces:**
- Produces: `GET /projects/{pid}/artifacts/archive` → zip 바이트. `downloadArtifactsArchive(pid: string): Promise<Blob>`.

- [ ] **Step 1: 실패 테스트 — 백엔드**

`backend/tests/test_routes_artifacts.py` 맨 아래 추가 (파일 상단의 기존 헬퍼/클라이언트 재사용 — 파일을 먼저 읽고 기존 `_project`/시드 패턴에 맞춰 조정하되, 단언은 그대로):

```python
import io
import zipfile


def test_archive_returns_zip_of_artifacts(monkeypatch):
    pid = "zip1"
    _seeded_project(monkeypatch, pid, {
        "aiplc-docs/discovery/discovery-document.md": "# Doc",
        "aiplc-docs/audit.md": "# Audit",
        "uploads/raw.md": "NOT INCLUDED",          # 산출물 아님
    })
    r = client.get(f"/projects/{pid}/artifacts/archive")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{pid}-artifacts.zip"' in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["aiplc-docs/audit.md", "aiplc-docs/discovery/discovery-document.md"]
    assert zf.read("aiplc-docs/discovery/discovery-document.md").decode() == "# Doc"


def test_archive_404_when_no_artifacts(monkeypatch):
    pid = "zip-empty"
    _seeded_project(monkeypatch, pid, {})
    assert client.get(f"/projects/{pid}/artifacts/archive").status_code == 404


def test_archive_404_unknown_project():
    assert client.get("/projects/zip-ghost/artifacts/archive").status_code == 404
```

(`_seeded_project`는 이 테스트 파일의 기존 FakeRunner 주입 헬퍼 이름에 맞춘다 — 파일에 이미 make_workspace 몽키패치 + `ws.runner.write_file` 시드 패턴이 있으니 그걸 사용하고, 없으면 같은 형태로 로컬 헬퍼를 만든다.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_artifacts.py -q`
Expected: 신규 3건 404/405류로 FAIL (라우트 없음).

- [ ] **Step 3: 라우트 구현**

`backend/pathfinder/routes/artifacts.py` — import에 `import asyncio, io, zipfile` + `from fastapi import Response` 추가. **주의: FastAPI 라우트 순서 — `/artifacts/archive`가 기존 `/files/{path:path}`보다 위에 있을 필요는 없지만(경로가 다름), `read_artifact`와 충돌하지 않는 새 경로다.** 파일 끝에:

```python
@router.get("/projects/{pid}/artifacts/archive")
async def download_artifacts_archive(pid: str):
    """aiplc-docs/** 전체를 zip으로 — 문서 리뷰의 '전체 다운로드'. 산출물이
    없으면 404. 콘텐츠는 S3 원문(오디트는 이미 redacted-at-rest)."""
    ws = await ensure_workspace(pid)
    paths = await ws.runner.list_files("aiplc-docs/**/*")
    if not paths:
        raise HTTPException(status_code=404, detail="no artifacts")
    contents = await asyncio.gather(*(ws.runner.read_file(p) for p in paths))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in zip(paths, contents):
            zf.writestr(path, content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pid}-artifacts.zip"'},
    )
```

(미등록 프로젝트는 `ensure_workspace`가 404를 냄 — 스펙의 "registry 등록 여부만 확인"보다 단순하며, 목록 조회와 달리 단건 다운로드 액션이라 lazy 초기화 비용이 수용 가능. 이 선택을 코드 주석에 남기지 말고 그대로 두라 — ensure_workspace가 이 라우트 파일의 기존 관용이다.)

- [ ] **Step 4: 백엔드 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_artifacts.py -q && .venv/bin/python -m pytest -q`
Expected: 파일 그린, 전체 그린(197 + 3 = 200).

- [ ] **Step 5: 프론트 — 클라이언트 함수 + 리뷰 버튼 + 테스트**

`frontend/lib/api/client.ts`에 추가:

```ts
export async function downloadArtifactsArchive(pid: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(pid)}/artifacts/archive`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.blob();
}
```

`frontend/app/projects/[projectId]/review/page.tsx` — `downloadMarkdown` 아래에 헬퍼 추가, 그리고 `.md 다운로드` 버튼이 있는 `flex justify-end mb-3` div에 버튼 추가:

```tsx
async function downloadZip(projectId: string) {
  const blob = await downloadArtifactsArchive(projectId);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${projectId}-artifacts.zip`;
  a.click();
  URL.revokeObjectURL(url);
}
```

```tsx
<button
  type="button"
  onClick={() => downloadZip(projectId).catch(() => setActionError("압축 다운로드에 실패했습니다."))}
  className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 mr-2"
>
  ⬇ 전체 다운로드 (.zip)
</button>
```

(import에 `downloadArtifactsArchive` 추가.)

프론트 테스트 — review page 테스트 파일이 있으면 거기, 없으면 `frontend/app/projects/[projectId]/review/page.test.tsx` 확인 후 기존 패턴에 맞춰: MSW로 `GET */projects/:pid/artifacts/archive` → zip blob 응답 등록, 버튼 클릭 → `URL.createObjectURL` 스파이 호출 확인.

- [ ] **Step 6: 프론트 확인 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: 그린.

```bash
git add backend/pathfinder/routes/artifacts.py backend/tests/test_routes_artifacts.py frontend/lib/api/client.ts 'frontend/app/projects/[projectId]/review/page.tsx' 'frontend/app/projects/[projectId]/review/page.test.tsx'
git commit -m "feat: artifacts zip download — backend archive endpoint + review button

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: WorkspaceDocPanel 문서 드롭다운

**Files:**
- Modify: `frontend/components/workspace/WorkspaceDocPanel.tsx`
- Test: `frontend/components/workspace/WorkspaceDocPanel.test.tsx`

**Interfaces:**
- Consumes: 기존 props `{ projectId, activeDoc, turnSeq }` — 시그니처 불변. `listArtifacts(projectId)` (기존 client 함수).
- Produces: 헤더에 `<select aria-label="문서 선택">` — 옵션 value=전체 경로, 라벨=파일명.

- [ ] **Step 1: 실패 테스트**

`frontend/components/workspace/WorkspaceDocPanel.test.tsx`에 추가 (기존 테스트 파일의 MSW 패턴 재사용 — 파일을 먼저 읽고 기존 핸들러/렌더 헬퍼에 맞춰 조정):

```tsx
describe("WorkspaceDocPanel — 문서 드롭다운", () => {
  it("산출물 목록이 드롭다운 옵션으로 뜬다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/discovery/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: "v1" }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["a.md", "b.md"]);
    expect((select as HTMLSelectElement).value).toBe("aiplc-docs/a.md");
  });

  it("드롭다운으로 다른 문서를 고르면 그 문서를 로드한다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/b.md`, () =>
        HttpResponse.json({ content: "# B-내용" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    await userEvent.setup().selectOptions(select, "aiplc-docs/b.md");
    expect(await screen.findByText("B-내용")).toBeInTheDocument();
  });

  it("새 activeDoc 이벤트가 오면 자동으로 그 문서로 전환한다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/b.md`, () =>
        HttpResponse.json({ content: "# B-내용" })),
    );
    const { rerender } = render(
      <WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    // 사용자가 수동 선택해 두어도…
    await userEvent.setup().selectOptions(select, "aiplc-docs/a.md");
    // …새 문서 이벤트(activeDoc 변경)는 그 문서로 전환한다
    rerender(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/b.md", version: "v2" }} turnSeq={1} />);
    expect(await screen.findByText("B-내용")).toBeInTheDocument();
    expect((screen.getByLabelText("문서 선택") as HTMLSelectElement).value).toBe("aiplc-docs/b.md");
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run components/workspace/WorkspaceDocPanel.test.tsx`
Expected: 신규 3건 FAIL(`문서 선택` 라벨 부재), 기존 테스트 통과 유지.

- [ ] **Step 3: 구현**

`WorkspaceDocPanel.tsx` 수정 요지 (기존 구조 유지, 헤더만 교체 + 선택 상태 추가):

```tsx
// 추가 import
import { useEffect, useState } from "react";
import { listArtifacts, readArtifact, ApiError } from "@/lib/api/client";

// 컴포넌트 본문 상단에:
  // 드롭다운 선택 상태. null = activeDoc 따름. activeDoc이 바뀌면(새 문서
  // 이벤트) 수동 선택을 리셋해 대화를 따라간다 — 스펙의 우선순위.
  const [manualPath, setManualPath] = useState<string | null>(null);
  useEffect(() => { setManualPath(null); }, [activeDoc?.path]);
  const path = manualPath ?? activeDoc?.path ?? null;

  // 산출물 목록 — 턴 종료(turnSeq)마다 재조회해 새 문서를 반영.
  const artifacts = useAsync(() => listArtifacts(projectId), [projectId, turnSeq]);
  const options = artifacts.data ?? (path ? [path] : []);
```

헤더 블록 교체:

```tsx
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-2">
        {options.length > 0 ? (
          <select
            aria-label="문서 선택"
            value={path ?? ""}
            onChange={(e) => setManualPath(e.target.value)}
            className="min-w-0 flex-1 text-xs font-bold text-slate-600 bg-transparent border border-slate-200 rounded-lg px-2 py-1.5 truncate focus:outline-none focus:ring-2 focus:ring-violet-300"
          >
            {options.map((p) => (
              <option key={p} value={p}>{p.slice(p.lastIndexOf("/") + 1)}</option>
            ))}
          </select>
        ) : (
          <p className="text-xs font-bold text-slate-400 uppercase tracking-wide truncate">생성된 문서</p>
        )}
        {versionLabel && manualPath === null && (
          <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">
            {versionLabel}
          </span>
        )}
      </div>
```

(버전 배지는 activeDoc를 따를 때만 — 수동 선택 문서의 버전은 모름. `name` 변수 사용처 제거. 기존 content useAsync의 `path` 의존은 새 `path` 파생값을 그대로 사용.)

- [ ] **Step 4: 통과 + 전체 + Commit**

Run: `cd frontend && npx vitest run components/workspace/WorkspaceDocPanel.test.tsx && npm test && npx tsc --noEmit`
Expected: 그린.

```bash
git add frontend/components/workspace/WorkspaceDocPanel.tsx frontend/components/workspace/WorkspaceDocPanel.test.tsx
git commit -m "feat(frontend): workspace doc panel gains artifact dropdown selector

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 채팅 stick-to-bottom 자동 스크롤

**Files:**
- Modify: `frontend/components/canvas/ChatTimeline.tsx`
- Test: `frontend/components/canvas/ChatTimeline.test.tsx` (기존 파일 확인 후 추가)

**Interfaces:**
- Consumes/Produces: `ChatTimeline` props에 `stickSignal: number` 추가 — 부모(workspace/canvas 페이지)가 메시지 전송 시 증가시키는 시퀀스. 페이지 쪽은 `send`/`submitAnswers` 호출부에서 `setStickSignal((n)=>n+1)`.

- [ ] **Step 1: 실패 테스트**

`frontend/components/canvas/ChatTimeline.test.tsx`에 추가 (기존 파일의 렌더 헬퍼에 맞춰; scrollTop은 jsdom에서 수동 설정으로 시뮬레이션):

```tsx
describe("ChatTimeline — stick-to-bottom", () => {
  function scroller(): HTMLElement {
    return screen.getByLabelText("대화 타임라인");
  }
  function fakeScrollGeometry(el: HTMLElement, { height = 400, content = 1000 }) {
    Object.defineProperty(el, "clientHeight", { value: height, configurable: true });
    Object.defineProperty(el, "scrollHeight", { value: content, configurable: true });
  }

  it("items가 추가되면 바닥으로 스크롤한다 (기본 stick)", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });

  it("사용자가 위로 스크롤하면 자동 스크롤이 멈춘다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    // 사용자가 위로: scrollTop을 바닥에서 멀리 두고 scroll 이벤트 발생
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(100);   // 위치 보존 — 끌려 내려가지 않음
  });

  it("stickSignal이 증가하면(메시지 전송) 무조건 바닥으로 복귀한다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={1} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });
});
```

(`Harness`/`msg`는 기존 테스트 파일의 헬퍼를 재사용하거나 동일 형태로 정의. `stickSignal` prop이 없으면 컴파일부터 실패 — 그게 Step 2의 기대 실패.)

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 구현**

`ChatTimeline.tsx` — props에 `stickSignal?: number` 추가, 스크롤 블록 교체:

```tsx
  const scrollerRef = useRef<HTMLDivElement>(null);
  // stick-to-bottom: 기본 켜짐. 사용자가 위로 스크롤하면 꺼지고, 바닥 근처로
  // 돌아오거나 메시지를 보내면(stickSignal 증가) 다시 켜진다. 스트리밍으로
  // 긴 응답이 자라도 stick이 켜져 있는 한 계속 바닥을 따라간다 — 기존
  // "바닥 120px 이내일 때만" 정책은 긴 응답에서 따라가기가 끊기는 원인이었다.
  const stickRef = useRef(true);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    // 프로그램적 스크롤(scrollTop=scrollHeight 직후)도 이 핸들러를 타지만,
    // 그 경우 바닥 판정이 참이라 stick이 유지된다. 사용자가 위로 올렸을 때만
    // 바닥에서 멀어져 stick이 꺼진다.
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  useEffect(() => {
    // 메시지 전송 = 무조건 바닥 복귀 (사용자 요청의 직접 해결).
    stickRef.current = true;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [stickSignal]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !stickRef.current) return;
    // 기존 주의사항 유지: scrollIntoView 금지(문서 스크롤 오염), 컨테이너만.
    el.scrollTop = el.scrollHeight;
  }, [items]);
```

스크롤 div에 `onScroll={onScroll}` 추가.

`frontend/app/projects/[projectId]/workspace/page.tsx` — `const [stickSignal, setStickSignal] = useState(0);` 추가, `send`/`submitAnswers`를 부르는 모든 곳(ChatInput onSend, WelcomeCard onStart, QuestionForm onSubmit/submitAnswersFromSheet)에서 호출 직전 `setStickSignal((n) => n + 1)`. `<ChatTimeline ... stickSignal={stickSignal} />` 전달. (canvas 경로에 별도 페이지가 있으면 동일 배선 — `grep -rn "ChatTimeline" frontend/app`으로 확인 후 워크스페이스와 동일 처리.)

- [ ] **Step 4: 통과 + 전체 + Commit**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx && npm test && npx tsc --noEmit`
Expected: 그린.

```bash
git add frontend/components/canvas/ChatTimeline.tsx frontend/components/canvas/ChatTimeline.test.tsx 'frontend/app/projects/[projectId]/workspace/page.tsx'
git commit -m "feat(frontend): chat sticks to bottom while streaming; sending always rescrolls

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 수정 요청 → 워크스페이스 채팅 이동 + draft 프리필

**Files:**
- Modify: `frontend/components/review/ApprovalGate.tsx` (인라인 폼 제거, 라우팅)
- Modify: `frontend/app/projects/[projectId]/review/page.tsx` (onRevise 제거)
- Modify: `frontend/components/canvas/ChatInput.tsx` (initialText + 포커스)
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx` (?draft= 처리)
- Test: `frontend/components/review/ApprovalGate.test.tsx`, `frontend/components/canvas/ChatInput.test.tsx` (기존 파일 확인 후 추가/수정)

**Interfaces:**
- Produces: `ApprovalGate({ onApprove, busy, stageStatus, reviseHref })` — `onRevise` 제거, `reviseHref: string` 추가(수정 요청 링크 목적지). `ChatInput`에 `initialText?: string` 추가(마운트 시 1회 프리필 + 포커스).

- [ ] **Step 1: 실패 테스트 — ApprovalGate**

`frontend/components/review/ApprovalGate.test.tsx` — 기존 파일을 먼저 읽고, 인라인 폼 관련 테스트(textarea 열기/제출)를 다음으로 대체:

```tsx
it("수정 요청은 워크스페이스 채팅으로 가는 링크다", () => {
  render(<ApprovalGate onApprove={vi.fn()} busy={false} stageStatus={null}
                       reviseHref="/projects/p1/workspace?draft=discovery-document.md%20%EC%88%98%EC%A0%95%20%EC%9A%94%EC%B2%AD%3A%20" />);
  const link = screen.getByRole("link", { name: /수정 요청/ });
  expect(link).toHaveAttribute("href",
    "/projects/p1/workspace?draft=discovery-document.md%20%EC%88%98%EC%A0%95%20%EC%9A%94%EC%B2%AD%3A%20");
});

it("승인 버튼은 그대로 onApprove를 호출한다", async () => {
  const onApprove = vi.fn();
  render(<ApprovalGate onApprove={onApprove} busy={false} stageStatus={null} reviseHref="/x" />);
  await userEvent.setup().click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
  expect(onApprove).toHaveBeenCalled();
});
```

- [ ] **Step 2: ApprovalGate 구현**

- `onRevise`/`open`/`text` state/인라인 폼 블록 전부 제거. props를 `{ onApprove, busy, stageStatus, reviseHref }`로.
- "수정 요청" 버튼을 `next/link`의 `<Link href={reviseHref}>`로 교체(동일 스타일 유지):

```tsx
<Link
  href={reviseHref}
  className="px-4 py-2.5 rounded-lg bg-white/15 hover:bg-white/25 border border-white/30 text-sm font-medium"
>
  ✏️ 수정 요청
</Link>
```

- 설명 문구 교체: `수정 요청은 워크스페이스 채팅으로 이동해 AI와 대화로 진행합니다 — 초안이 입력창에 채워집니다.` (기존 "자연어로 전달되어…게이트로 돌아옵니다" 문장 대체)
- `review/page.tsx`: `onRevise` prop 제거, `reviseHref` 계산 추가:

```tsx
const docName = selected ? selected.slice(selected.lastIndexOf("/") + 1) : "discovery-document.md";
const reviseHref = `/projects/${projectId}/workspace?draft=${encodeURIComponent(`${docName} 수정 요청: `)}`;
```

`<ApprovalGate onApprove={() => sendTurn("승인")} busy={busy} stageStatus={docStage?.status ?? null} reviseHref={reviseHref} />`

- [ ] **Step 3: 실패 테스트 — ChatInput initialText**

`frontend/components/canvas/ChatInput.test.tsx`에 추가 (기존 파일 패턴 재사용):

```tsx
it("initialText가 있으면 프리필 + 포커스된다", () => {
  render(<ChatInput onSend={vi.fn()} disabled={false} initialText="doc.md 수정 요청: " />);
  const input = screen.getByLabelText("채팅 메시지 입력");
  expect(input).toHaveValue("doc.md 수정 요청: ");
  expect(input).toHaveFocus();
});

it("initialText가 없으면 기존과 동일 (빈 입력, 포커스 강제 없음)", () => {
  render(<ChatInput onSend={vi.fn()} disabled={false} />);
  expect(screen.getByLabelText("채팅 메시지 입력")).toHaveValue("");
});
```

- [ ] **Step 4: ChatInput + workspace page 구현**

`ChatInput.tsx`:

```tsx
// props에 추가: initialText?: string
const [text, setText] = useState(initialText ?? "");
const inputRef = useRef<HTMLTextAreaElement>(null);
useEffect(() => {
  if (initialText) inputRef.current?.focus();
  // 마운트 시 1회 — initialText 변경 추적은 불필요(워크스페이스가 마운트 시 전달)
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);
// textarea에 ref={inputRef} 추가
```

`workspace/page.tsx`:

```tsx
import { useSearchParams, useRouter } from "next/navigation";
// 컴포넌트 상단:
const searchParams = useSearchParams();
const router = useRouter();
const draft = searchParams.get("draft") ?? undefined;
useEffect(() => {
  // 프리필을 넘긴 뒤 URL에서 제거 — 새로고침 시 재프리필 방지.
  if (draft) router.replace(`/projects/${projectId}/workspace`, { scroll: false });
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);
// ChatInput에 initialText={draft} 전달
```

(Next 15에서 `useSearchParams`는 Suspense 경계를 요구할 수 있음 — 페이지가 이미 "use client"라 빌드 에러가 나면 `<Suspense>`로 페이지 본문을 감싸는 대신 `window.location.search` 파싱으로 대체 가능. 구현 시 `npx tsc --noEmit` + `npm run build`가 아닌 vitest로 검증하되, useSearchParams 접근이 테스트에서 문제되면 `next/navigation`의 vitest mock(기존 테스트에 이미 있는지 grep)을 따른다.)

- [ ] **Step 5: 통과 + 전체 + Commit**

Run: `cd frontend && npx vitest run components/review/ApprovalGate.test.tsx components/canvas/ChatInput.test.tsx && npm test && npx tsc --noEmit`
Expected: 그린 (ApprovalGate의 구 인라인폼 테스트는 대체됨).

```bash
git add frontend/components/review/ApprovalGate.tsx frontend/components/canvas/ChatInput.tsx 'frontend/app/projects/[projectId]/review/page.tsx' 'frontend/app/projects/[projectId]/workspace/page.tsx' frontend/components/review/ApprovalGate.test.tsx frontend/components/canvas/ChatInput.test.tsx
git commit -m "feat(frontend): revise-request routes to workspace chat with drafted message

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** 항목 0 → Task 1 (순수 함수 + 통합 + fail-soft + addendum). 항목 1 → Task 3. 항목 2 → Task 2 (백+프론트). 항목 3 → Task 4 (stickSignal + 사용자 스크롤 감지). 항목 4 → Task 5 (링크 라우팅 + 프리필 + URL 정리 + 승인 유지 + 문구 갱신). 테스트 절 항목 전부 매핑. ✓

**Placeholder scan:** "기존 파일 확인 후 패턴 재사용" 지시가 3곳(테스트 파일 헬퍼) 있으나 이는 실제 파일의 기존 헬퍼 이름에 맞추라는 구체 지시이며 코드 골격은 전부 제공됨. ✓

**Type consistency:** `upsert_stage(markdown, stage, status) -> str` Task 1 정의·사용 일치. `ApprovalGate` 새 props(`reviseHref`, `onRevise` 제거) Task 5 내 일치. `ChatTimeline.stickSignal` Task 4 정의·배선 일치. `downloadArtifactsArchive(pid) -> Blob` Task 2 일치. ✓
