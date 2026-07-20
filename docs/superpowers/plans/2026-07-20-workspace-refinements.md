# 워크스페이스 개선 7건 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 채팅 히스토리 복원(S3 세션 → API), 마크다운 렌더, 시작 웰컴 카드, 질문 복수선택, 첨부파일 컨텍스트, 스크롤 분리, 문서 리뷰 파일 트리를 구현한다.

**Architecture:** 백엔드는 S3 strands 세션을 직접 읽는 히스토리 변환 모듈과 업로드 변환 모듈을 추가한다(VM 부팅 없음 — lazy 원칙). 프론트는 react-markdown 공용 컴포넌트, useWorkspaceStream의 히스토리 주입, 웰컴 카드, 체크박스 복수선택, 첨부 칩→자동 멘션, min-h-0 스크롤 교정, artifacts 기반 문서 트리를 추가한다. 하네스는 QUESTIONS_SCHEMA_HINT에 multi_select 규칙만 추가한다(이미지 재배포 필요).

**Tech Stack:** FastAPI + boto3(기존), `openpyxl`/`pypdf`/`python-multipart`(신규 backend), `react-markdown`+`remark-gfm`(신규 frontend), Next.js 15 + Vitest.

**Spec:** `docs/superpowers/specs/2026-07-20-workspace-refinements-design.md`

## Global Constraints

- Python 3.11 / Node 20+. 테스트: backend `cd backend && .venv/bin/python -m pytest -q`(현재 180), harness `cd harness && .venv/bin/python -m pytest -q`(현재 60), frontend `cd frontend && npm test`(현재 143) + `npx tsc --noEmit`.
- 히스토리·업로드는 **VM을 부팅하지 않는다** — S3 직접 접근(기존 write_file 경로는 S3-only).
- 업로드 한도: 원본 5MB(초과 413), 변환 후 50,000자 절단(말미 `[... 50,000자 초과분 생략]` 표기).
- 복수선택 답변 형식: letter 콤마 조인 `"A,C"`. `multi_select` 필드 기본 false(없으면 false 해석 — 하위호환).
- 히스토리 API는 실패 시에도 200 + 빈 배열(보조 데이터 — 절대 500으로 화면을 막지 않는다).
- 사용자 메시지는 plain text 유지, AI 메시지·문서·preamble만 마크다운 렌더. raw HTML 렌더 금지(XSS).
- 레다크션: 히스토리 텍스트에 `redact_credentials` 적용(기존 route seam 정책 유지).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 하네스 변경(Task 3)은 이미지 재배포가 있어야 실 VM에 반영된다 — 코드·테스트만 이 플랜의 스코프이고 재배포는 최종 드릴 단계.

## 실 S3 메시지 shape (드릴 세션에서 캡처 — 히스토리 픽스처의 근거)

```json
// message_0.json (user 텍스트)
{"message": {"role": "user", "content": [{"text": "AI-PLC를 시작해줘"}], "tracking_id": "..."},
 "message_id": 0, "redact_message": null, "created_at": "...", "updated_at": "..."}
// message_2.json (user, toolResult만 — file_write/report_stage 결과)
{"message": {"role": "user", "content": [
  {"toolResult": {"toolUseId": "...", "status": "success", "content": [{"text": "written: aiplc-docs/audit.md"}]}}]}}
// message_5.json (assistant: reasoning + text + ask_questions toolUse)
{"message": {"role": "assistant", "content": [
  {"reasoningContent": {"reasoningText": {"text": "", "signature": "..."}}},
  {"text": "# 👋 AI-PLC Discovery Phase에 오신 것을 환영합니다! ..."},
  {"toolUse": {"toolUseId": "...", "name": "ask_questions", "input": {"questions_file": {"name": "discovery-mode-selection", ...}}}}]}}
// message_6.json (user, ask_questions의 toolResult = 답변 제출)
{"message": {"role": "user", "content": [
  {"toolResult": {"toolUseId": "tooluse_j4AO...", "status": "success", "content": [{"text": "사용자 답변: {\"1\": \"A\"}"}]}}]}}
```

주의: 답변 제출을 식별하려면 toolResult의 `toolUseId`가 **ask_questions의 toolUse id와 매칭**되어야 한다(다른 toolResult는 file_write 등의 결과). 변환기는 1패스로 ask_questions toolUseId 집합을 만들고 2패스에서 매칭한다.

## File Structure

```
backend/pathfinder/
  session_history.py        (신규) S3 strands 세션 → HistoryItem 변환 (list_history)
  parsers/uploads.py         (신규) 업로드 변환: xlsx→md표, pdf→텍스트, 절단, slug
  routes/history.py          (신규) GET /projects/{pid}/history
  routes/uploads.py          (신규) POST /projects/{pid}/uploads
  models.py                  (수정) Question.multi_select + HistoryItem
  sandbox/microvm.py         (수정) _SYNC_GLOBS/_RESTORE_PREFIXES에 uploads/
  app.py                     (수정) 라우터 등록 + 세션 S3 리더 팩토리
  pyproject.toml             (수정) openpyxl, pypdf, python-multipart
harness/
  aiplc_tools.py             (수정) QUESTIONS_SCHEMA_HINT multi_select 규칙
frontend/
  components/Markdown.tsx    (신규) react-markdown 공용 래퍼
  components/workspace/WelcomeCard.tsx  (신규)
  components/workspace/AttachmentChips.tsx (신규) 칩 목록 + 제거
  components/review/DocTree.tsx (신규) artifacts 파일 트리
  lib/api/client.ts          (수정) getHistory, uploadFile, readArtifact
  lib/api/types.ts           (수정) HistoryItem, Question.multi_select
  lib/useWorkspaceStream.ts  (수정) 히스토리 초기 주입 + attachments 상태
  components/canvas/AiMessage.tsx (수정) Markdown 렌더
  components/canvas/ChatTimeline.tsx (수정) 히스토리 카드 아이템
  components/questions/QuestionCard.tsx (수정) multi_select 체크박스
  components/questions/QuestionForm.tsx (수정) 콤마 조인
  components/canvas/ChatInput.tsx (수정) 클립 버튼 슬롯(children)
  app/projects/[projectId]/workspace/page.tsx (수정) 웰컴 카드·스크롤·첨부 배선
  app/projects/[projectId]/review/page.tsx (수정) 트리+뷰어 개편
```

---

### Task 1: 백엔드 히스토리 변환 모듈 + GET /history

**Files:**
- Create: `backend/pathfinder/session_history.py`
- Create: `backend/pathfinder/routes/history.py`
- Modify: `backend/pathfinder/models.py` (HistoryItem)
- Modify: `backend/pathfinder/app.py` (라우터 등록 + `session_s3_factory`)
- Test: `backend/tests/test_session_history.py`, `backend/tests/test_routes_history.py`

**Interfaces:**
- Produces: `models.HistoryItem(role: Literal["user","ai","card"], text: str | None = None, card: Literal["questions"] | None = None, name: str | None = None)`.
- Produces: `session_history.transform_messages(raw: list[dict]) -> list[HistoryItem]` — raw는 message_*.json 파싱 dict의 message_id 오름차순 리스트.
- Produces: `session_history.list_history(s3, session_id: str) -> list[HistoryItem]` (async) — s3는 `S3StoreLike`(prefix가 `sessions/`인 스토어), 키 `session_<id>/agents/agent_default/messages/` 아래를 나열·조회.
- Produces: `GET /projects/{pid}/history` → `{"items": [...]}`. 실패·부재 시 `{"items": []}`.
- Produces: `app.session_s3_factory() -> S3StoreLike` — 모듈 레벨, 테스트에서 monkeypatch(기존 `s3_store_factory` 관례).

- [ ] **Step 1: 변환 유닛 실패 테스트 작성** — `backend/tests/test_session_history.py` (플랜 상단의 실 캡처 shape을 픽스처로):

```python
import json
import pytest
from pathfinder.session_history import transform_messages, list_history
from pathfinder.models import HistoryItem

def _msg(role, content, mid):
    return {"message": {"role": role, "content": content}, "message_id": mid}

RAW = [
    _msg("user", [{"text": "AI-PLC를 시작해줘"}], 0),
    _msg("assistant", [
        {"reasoningContent": {"reasoningText": {"text": "", "signature": "sig"}}},
        {"text": "환영합니다."},
        {"toolUse": {"toolUseId": "tu-write", "name": "file_write",
                     "input": {"path": "aiplc-docs/audit.md", "content": "x"}}}], 1),
    _msg("user", [
        {"toolResult": {"toolUseId": "tu-write", "status": "success",
                        "content": [{"text": "written: aiplc-docs/audit.md"}]}}], 2),
    _msg("assistant", [
        {"text": "질문 드립니다."},
        {"toolUse": {"toolUseId": "tu-ask", "name": "ask_questions",
                     "input": {"questions_file": {"name": "discovery-mode-selection",
                                                  "questions": []}}}}], 3),
    _msg("user", [
        {"toolResult": {"toolUseId": "tu-ask", "status": "success",
                        "content": [{"text": '사용자 답변: {"1": "A"}'}]}}], 4),
]

def test_transform_user_and_assistant_text():
    items = transform_messages(RAW)
    assert items[0] == HistoryItem(role="user", text="AI-PLC를 시작해줘")
    assert HistoryItem(role="ai", text="환영합니다.") in items

def test_transform_ask_questions_becomes_card_and_answer_message():
    items = transform_messages(RAW)
    assert HistoryItem(role="card", card="questions", name="discovery-mode-selection") in items
    answers = [i for i in items if i.role == "user" and i.text and i.text.startswith("답변 제출")]
    assert answers and '"1": "A"' in answers[0].text

def test_transform_skips_reasoning_and_other_tool_blocks():
    items = transform_messages(RAW)
    texts = [i.text or "" for i in items]
    assert not any("written: aiplc-docs" in t for t in texts)  # file_write toolResult 생략
    assert len(items) == 4  # user, ai(환영), ai(질문 드립니다)+card, 답변 → 4~5 확인 후 고정

def test_transform_joins_multiple_text_blocks():
    raw = [_msg("assistant", [{"text": "앞"}, {"text": "뒤"}], 0)]
    assert transform_messages(raw) == [HistoryItem(role="ai", text="앞\n뒤")]

def test_transform_redacts_credentials():
    raw = [_msg("assistant", [{"text": "key AKIAIOSFODNN7EXAMPLE here"}], 0)]
    assert "AKIAIOSFODNN7EXAMPLE" not in transform_messages(raw)[0].text

@pytest.mark.asyncio
async def test_list_history_reads_sorted_and_tolerates_empty():
    from tests.fakes.in_memory_s3 import FakeS3Store
    s3 = FakeS3Store()
    base = "session_p1/agents/agent_default/messages"
    s3.blobs[f"{base}/message_10.json"] = json.dumps(_msg("user", [{"text": "열번째"}], 10))
    s3.blobs[f"{base}/message_2.json"] = json.dumps(_msg("user", [{"text": "두번째"}], 2))
    items = await list_history(s3, "p1")
    assert [i.text for i in items] == ["두번째", "열번째"]  # 숫자 정렬 (문자열 정렬이면 10<2)
    assert await list_history(FakeS3Store(), "없는세션") == []
```

주의: `test_transform_skips...`의 기대 아이템 수는 구현 후 실제 규칙(질문 카드가 ai 텍스트와 별개 아이템)에 맞춰 정확 값으로 고정한다 — 규칙: 위 RAW는 `[user텍스트, ai"환영합니다.", ai"질문 드립니다.", card, user"답변 제출..."]` 5개가 정답.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_session_history.py -q`
Expected: FAIL — `ModuleNotFoundError: pathfinder.session_history`

- [ ] **Step 3: models.py에 HistoryItem 추가**

```python
class HistoryItem(BaseModel):
    role: Literal["user", "ai", "card"]
    text: str | None = None
    card: Literal["questions"] | None = None
    name: str | None = None
```

- [ ] **Step 4: session_history.py 구현**

```python
# backend/pathfinder/session_history.py
"""S3 strands 세션 메시지 → 채팅 히스토리 변환.

세션 저장소는 sandbox 추상화 밖의 인프라(strands SDK가 쓰는 S3 오브젝트)라서
Sandbox 메서드가 아니라 이 모듈이 직접 읽는다. VM은 절대 부팅하지 않는다.
"""
from __future__ import annotations
import json
import logging
import re
from pathfinder.models import HistoryItem
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.sandbox.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MSG_KEY = re.compile(r"message_(\d+)\.json$")


def transform_messages(raw: list[dict]) -> list[HistoryItem]:
    # 1패스: ask_questions toolUse id 수집 (답변 toolResult 식별용 — 실 세션에는
    # file_write 등 다른 toolResult가 섞여 있어 이름 매칭이 필수다).
    ask_ids: set[str] = set()
    for m in raw:
        for block in m.get("message", {}).get("content", []):
            tu = block.get("toolUse")
            if tu and tu.get("name") == "ask_questions":
                ask_ids.add(tu.get("toolUseId", ""))

    items: list[HistoryItem] = []
    for m in raw:
        msg = m.get("message", {})
        role = msg.get("role")
        texts: list[str] = []
        cards: list[HistoryItem] = []
        for block in msg.get("content", []):
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                if tu.get("name") == "ask_questions":
                    name = (tu.get("input", {}).get("questions_file") or {}).get("name")
                    cards.append(HistoryItem(role="card", card="questions", name=name))
            elif "toolResult" in block:
                tr = block["toolResult"]
                if tr.get("toolUseId") in ask_ids:
                    inner = "".join(c.get("text", "") for c in tr.get("content", []))
                    # 도구 결과 원문("사용자 답변: {...}")에서 답변부만 살린 요약
                    answer = inner.replace("사용자 답변: ", "", 1)
                    items.append(HistoryItem(
                        role="user", text=redact_credentials(f"답변 제출: {answer}")))
            # reasoningContent 및 기타 블록은 생략
        if texts:
            joined = redact_credentials("\n".join(texts))
            items.append(HistoryItem(role="ai" if role == "assistant" else "user",
                                     text=joined))
        items.extend(cards)
    return items


async def list_history(s3: S3StoreLike, session_id: str) -> list[HistoryItem]:
    """세션의 message_*.json을 message_id 순으로 읽어 변환. 어떤 실패도
    빈 리스트로 강등(히스토리는 보조 데이터 — 화면을 막지 않는다)."""
    prefix = f"session_{session_id}/agents/agent_default/messages/"
    try:
        keys = await s3.list(prefix)
        numbered: list[tuple[int, str]] = []
        for k in keys:
            match = _MSG_KEY.search(k)
            if match:
                numbered.append((int(match.group(1)), k))
        raw = []
        for _, key in sorted(numbered):
            raw.append(json.loads(await s3.get(key)))
        return transform_messages(raw)
    except Exception:
        _log.exception("history read failed for %s", session_id)
        return []
```

- [ ] **Step 5: 라우트 + app 배선** — `backend/pathfinder/routes/history.py`:

```python
# backend/pathfinder/routes/history.py
from fastapi import APIRouter
from pathfinder import app as app_module
from pathfinder.routes.deps import get_workspace
from pathfinder.session_history import list_history

router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    get_workspace(pid)  # 404 gate (unknown project)
    s3 = app_module.session_s3_factory()
    return {"items": await list_history(s3, pid)}
```

`backend/pathfinder/app.py` — `s3_store_factory` 아래에 추가(같은 monkeypatch 관례):

```python
# Monkeypatchable in tests. Reads the strands session objects (sessions/ prefix)
# that S3SessionManager writes from inside the VM; the backend only READS them.
def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)
```

라우터 등록(기존 include_router 블록 뒤): `from pathfinder.routes import history` + `app.include_router(history.router)`.

- [ ] **Step 6: 라우트 테스트** — `backend/tests/test_routes_history.py` (기존 test_routes_turns.py의 monkeypatch 관례):

```python
import json
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

def _local_project(monkeypatch, pid):
    import tempfile
    from pathlib import Path
    from pathfinder.sandbox.local import LocalSandbox
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def test_history_returns_items_from_session_store(monkeypatch):
    _local_project(monkeypatch, "h1")
    s3 = FakeS3Store()
    s3.blobs["session_h1/agents/agent_default/messages/message_0.json"] = json.dumps(
        {"message": {"role": "user", "content": [{"text": "안녕"}]}, "message_id": 0})
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: s3)
    body = client.get("/projects/h1/history").json()
    assert body == {"items": [{"role": "user", "text": "안녕", "card": None, "name": None}]}

def test_history_empty_when_no_session(monkeypatch):
    _local_project(monkeypatch, "h2")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/h2/history").json() == {"items": []}

def test_history_unknown_project_404(monkeypatch):
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/ghost/history").status_code == 404
```

- [ ] **Step 7: 전체 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (180 + 신규)

- [ ] **Step 8: Commit**

```bash
git add backend/pathfinder/session_history.py backend/pathfinder/routes/history.py \
        backend/pathfinder/models.py backend/pathfinder/app.py \
        backend/tests/test_session_history.py backend/tests/test_routes_history.py
git commit -m "feat(backend): chat history API — S3 strands session transform, no VM boot"
```

---

### Task 2: 백엔드 업로드 — 변환 파서 + POST /uploads + sync prefix

**Files:**
- Create: `backend/pathfinder/parsers/uploads.py`
- Create: `backend/pathfinder/routes/uploads.py`
- Modify: `backend/pathfinder/sandbox/microvm.py` (`_SYNC_GLOBS`, `_RESTORE_PREFIXES`)
- Modify: `backend/pathfinder/app.py` (라우터 등록), `backend/pyproject.toml` (deps)
- Test: `backend/tests/test_uploads_parser.py`, `backend/tests/test_routes_uploads.py`

**Interfaces:**
- Produces: `uploads.convert(filename: str, data: bytes) -> tuple[str, bool]` — (마크다운/텍스트 내용, truncated 여부). 지원 외 확장자는 `ValueError`.
- Produces: `uploads.safe_name(filename: str, existing: set[str]) -> str` — slug + 충돌 시 `-2` 접미사, 항상 `.md` 확장자.
- Produces: `POST /projects/{pid}/uploads` (multipart `file`) → `{"path": "uploads/<name>.md", "chars": int, "truncated": bool}`. 413(>5MB), 415(미지원 확장자).
- 상수: `MAX_UPLOAD_BYTES = 5 * 1024 * 1024`, `MAX_CHARS = 50_000`.

- [ ] **Step 1: deps 설치**

`backend/pyproject.toml` dependencies에 `"openpyxl>=3.1"`, `"pypdf>=4.0"`, `"python-multipart>=0.0.9"` 추가 후:
Run: `cd backend && .venv/bin/pip install -e ".[dev]" -q && .venv/bin/python -c "import openpyxl, pypdf, multipart; print('ok')"`
Expected: `ok`

- [ ] **Step 2: 파서 실패 테스트** — `backend/tests/test_uploads_parser.py`:

```python
import io
import pytest
from pathfinder.parsers.uploads import convert, safe_name, MAX_CHARS

def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "의견"
    ws.append(["이름", "의견"])
    ws.append(["김PM", "너무 느려요"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def test_md_passthrough():
    content, truncated = convert("노트.md", "# 제목\n내용".encode("utf-8"))
    assert content == "# 제목\n내용" and truncated is False

def test_xlsx_becomes_markdown_table():
    content, _ = convert("survey.xlsx", _xlsx_bytes())
    assert "## 의견" in content            # 시트명 헤더
    assert "| 이름 | 의견 |" in content
    assert "| 김PM | 너무 느려요 |" in content

def test_truncation_marks_and_cuts():
    content, truncated = convert("big.txt", ("가" * (MAX_CHARS + 100)).encode("utf-8"))
    assert truncated is True
    assert content.endswith("[... 50,000자 초과분 생략]")
    assert len(content) <= MAX_CHARS + 30   # 마커 길이 여유

def test_unsupported_extension_raises():
    with pytest.raises(ValueError):
        convert("virus.exe", b"MZ")

def test_safe_name_slug_and_collision():
    assert safe_name("고객 의견/2026.xlsx", set()) == "고객-의견-2026.md"
    assert safe_name("a.md", {"a.md"}) == "a-2.md"
    assert safe_name("a.md", {"a.md", "a-2.md"}) == "a-3.md"
```

- [ ] **Step 3: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_uploads_parser.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: 파서 구현** — `backend/pathfinder/parsers/uploads.py`:

```python
# backend/pathfinder/parsers/uploads.py
"""업로드 파일 → 에이전트가 file_read로 읽을 텍스트 변환.

xlsx는 VM 안 에이전트가 직접 못 읽으므로(텍스트 도구뿐) 업로드 시점에
마크다운 표로 변환한다. 변환 결과는 룰의 URL 모드와 같은 50,000자 한도로
절단한다(spec §6). 내용은 신뢰하지 않는 입력 — 텍스트로만 저장한다.
"""
from __future__ import annotations
import io
import re

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CHARS = 50_000
_TRUNC_MARK = "\n[... 50,000자 초과분 생략]"
ALLOWED = {".md", ".txt", ".csv", ".xlsx", ".pdf"}


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _xlsx_to_markdown(data: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        rows = [[("" if c is None else str(c)) for c in row]
                for row in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        parts.append(f"## {ws.title}")
        parts.append("| " + " | ".join(rows[0]) + " |")
        parts.append("|" + "---|" * len(rows[0]))
        for row in rows[1:]:
            parts.append("| " + " | ".join(row) + " |")
    return "\n".join(parts)


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def convert(filename: str, data: bytes) -> tuple[str, bool]:
    ext = _ext(filename)
    if ext not in ALLOWED:
        raise ValueError(f"unsupported extension: {ext or '(none)'}")
    if ext == ".xlsx":
        content = _xlsx_to_markdown(data)
    elif ext == ".pdf":
        content = _pdf_to_text(data)
    else:  # .md .txt .csv — 텍스트 그대로 (lossy 디코드)
        content = data.decode("utf-8", errors="replace")
    if len(content) > MAX_CHARS:
        cut = MAX_CHARS - len(_TRUNC_MARK)
        return content[:cut] + _TRUNC_MARK, True
    return content, False


def safe_name(filename: str, existing: set[str]) -> str:
    """원본 이름을 워크스페이스 안전 슬러그로. 한글 유지, 경로·특수문자 제거,
    확장자는 항상 .md(변환 결과물이므로). 충돌 시 -2, -3… 접미사."""
    stem = filename.rsplit("/", 1)[-1]
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    stem = re.sub(r"[^\w가-힣-]+", "-", stem).strip("-") or "upload"
    candidate = f"{stem}.md"
    n = 2
    while candidate in existing:
        candidate = f"{stem}-{n}.md"
        n += 1
    return candidate
```

- [ ] **Step 5: 파서 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_uploads_parser.py -q`
Expected: PASS

- [ ] **Step 6: 라우트 실패 테스트** — `backend/tests/test_routes_uploads.py`:

```python
import io
from fastapi.testclient import TestClient
import pathfinder.app as app_module

client = TestClient(app_module.app)

def _local_project(monkeypatch, pid):
    import tempfile
    from pathlib import Path
    from pathfinder.sandbox.local import LocalSandbox
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def test_upload_md_saved_to_uploads_prefix(monkeypatch):
    _local_project(monkeypatch, "u1")
    r = client.post("/projects/u1/uploads",
                    files={"file": ("의견.md", io.BytesIO("# 의견".encode()), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "uploads/의견.md" and body["truncated"] is False
    # 저장 확인: 같은 sandbox의 read_file 경유 (files API가 없으므로 questions 경로 재사용 불가 →
    # workspace registry로 직접)
    ws = app_module.registry.get("u1")
    import asyncio
    assert asyncio.get_event_loop().run_until_complete(
        ws.sandbox.read_file("uploads/의견.md")) == "# 의견"

def test_upload_collision_gets_suffix(monkeypatch):
    _local_project(monkeypatch, "u2")
    for _ in range(2):
        r = client.post("/projects/u2/uploads",
                        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
    assert r.json()["path"] == "uploads/a-2.md"

def test_upload_rejects_big_and_unsupported(monkeypatch):
    _local_project(monkeypatch, "u3")
    big = io.BytesIO(b"0" * (5 * 1024 * 1024 + 1))
    assert client.post("/projects/u3/uploads",
                       files={"file": ("big.txt", big, "text/plain")}).status_code == 413
    assert client.post("/projects/u3/uploads",
                       files={"file": ("run.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}
                       ).status_code == 415

def test_upload_unknown_project_404():
    assert client.post("/projects/ghost/uploads",
                       files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")}).status_code == 404
```

주의: TestClient 동기 컨텍스트에서의 read_file 검증이 이벤트 루프 문제를 일으키면 `client.get(f"/projects/u1/questions/uploads/의견.md")` 같은 우회 대신 `anyio.from_thread`/`asyncio.run` 등 파일의 기존 테스트 관례에 맞춰 조정하되 "저장 내용 일치" 단언은 유지.

- [ ] **Step 7: 라우트 구현** — `backend/pathfinder/routes/uploads.py`:

```python
# backend/pathfinder/routes/uploads.py
from fastapi import APIRouter, HTTPException, UploadFile
from pathfinder.routes.deps import get_workspace
from pathfinder.parsers.uploads import convert, safe_name, MAX_UPLOAD_BYTES

router = APIRouter()

@router.post("/projects/{pid}/uploads")
async def upload_file(pid: str, file: UploadFile):
    ws = get_workspace(pid)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 5MB limit")
    try:
        content, truncated = convert(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    existing = set(
        p.removeprefix("uploads/") for p in await ws.sandbox.list_files("uploads/*"))
    name = safe_name(file.filename or "upload", existing)
    path = f"uploads/{name}"
    await ws.sandbox.write_file(path, content)
    return {"path": path, "chars": len(content), "truncated": truncated}
```

`app.py` 라우터 등록: `from pathfinder.routes import uploads` + `app.include_router(uploads.router)`.

- [ ] **Step 8: microvm.py sync prefix 추가** — 기존 상수 2곳 수정:

```python
    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")
```

`backend/tests/test_microvm_sandbox.py`에 확인 테스트 1건 추가:

```python
@pytest.mark.asyncio
async def test_uploads_prefix_restored_to_vm():
    harness = FakeHarness(events_for=lambda t: [AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    sb._s3.blobs["uploads/의견.md"] = "# 의견"
    [e async for e in sb.send_message("읽어줘")]
    assert harness.files.get("uploads/의견.md") == "# 의견"  # S3 → VM 복원
```

- [ ] **Step 9: 전체 통과 + Commit**

Run: `cd backend && .venv/bin/python -m pytest -q` → PASS

```bash
git add backend/pathfinder/parsers/uploads.py backend/pathfinder/routes/uploads.py \
        backend/pathfinder/sandbox/microvm.py backend/pathfinder/app.py backend/pyproject.toml \
        backend/tests/test_uploads_parser.py backend/tests/test_routes_uploads.py \
        backend/tests/test_microvm_sandbox.py
git commit -m "feat(backend): file uploads — xlsx/pdf conversion, uploads/ prefix sync, 5MB/50k limits"
```

---

### Task 3: multi_select 계약 — backend 모델 + 하네스 스키마 힌트

**Files:**
- Modify: `backend/pathfinder/models.py` (Question)
- Modify: `harness/aiplc_tools.py` (QUESTIONS_SCHEMA_HINT)
- Test: `backend/tests/test_models.py` (추가), `harness/tests/test_aiplc_tools.py` (추가)

**Interfaces:**
- Produces: `models.Question.multi_select: bool = False` — 파서·기존 코드 무변경(기본값 하위호환).
- Produces: 스키마 힌트에 multi_select 규칙 — Task 6(프론트 체크박스)은 payload의 `multi_select`를 읽는다.

- [ ] **Step 1: 실패 테스트 2건**

`backend/tests/test_models.py`에:

```python
def test_question_multi_select_defaults_false():
    from pathfinder.models import Question
    q = Question(number=1, text="누구?", options=[])
    assert q.multi_select is False
    assert Question(number=1, text="누구?", options=[], multi_select=True).multi_select
```

`harness/tests/test_aiplc_tools.py`에:

```python
def test_schema_hint_mentions_multi_select():
    from aiplc_tools import QUESTIONS_SCHEMA_HINT
    assert "multi_select" in QUESTIONS_SCHEMA_HINT
    assert "false" in QUESTIONS_SCHEMA_HINT  # 기본값 안내
```

- [ ] **Step 2: 실패 확인** — 각 스위트에서 해당 테스트 FAIL 확인.

- [ ] **Step 3: 구현**

`models.py` Question에 `multi_select: bool = False` 필드 추가.

`harness/aiplc_tools.py`의 `QUESTIONS_SCHEMA_HINT` 문자열에서 questions[] 항목 스키마에 `"multi_select": bool` 추가하고 규칙 문장 덧붙임:

```python
QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "parse_ok": true, "raw_markdown": null, '
    '"questions": [{"number": int, "category": str|null, "text": str, "answer": null, '
    '"multi_select": bool, "options": [{"letter": "A".."F"|"X", "text": str, '
    '"is_other": bool, "recommended": bool}]}]}. '
    "multi_select 규칙: 여러 개를 골라도 자연스러운 질문(대상 고객군, 페인포인트 유형 등)은 "
    "true, 배타적 선택(Path/모드 선택 등)은 false(기본). "
    "multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다."
)
```

- [ ] **Step 4: 통과 확인 + Commit**

Run: `cd backend && .venv/bin/python -m pytest -q && cd ../harness && .venv/bin/python -m pytest -q` → PASS

```bash
git add backend/pathfinder/models.py backend/tests/test_models.py \
        harness/aiplc_tools.py harness/tests/test_aiplc_tools.py
git commit -m "feat(contract): per-question multi_select flag — model field + agent schema hint"
```

---

### Task 4: 프론트 Markdown 컴포넌트 + AiMessage 렌더

**Files:**
- Create: `frontend/components/Markdown.tsx`
- Modify: `frontend/components/canvas/AiMessage.tsx`
- Test: `frontend/components/Markdown.test.tsx`, `frontend/components/canvas/AiMessage.test.tsx` (수정)

**Interfaces:**
- Produces: `<Markdown text={string} />` — react-markdown+remark-gfm, raw HTML 미렌더, 링크 `target="_blank" rel="noopener noreferrer"`, `prose prose-sm` 스타일 래퍼. Task 5(히스토리)·Task 8(리뷰 뷰어)·우측 패널 preamble이 재사용.

- [ ] **Step 1: deps 설치**

Run: `cd frontend && npm install react-markdown remark-gfm`
Expected: 성공 (react-markdown 9.x, remark-gfm 4.x)

- [ ] **Step 2: 실패 테스트** — `frontend/components/Markdown.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders headings, bold, and GFM tables", () => {
    render(<Markdown text={"# 제목\n**굵게**\n\n| a | b |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("heading", { name: "제목" })).toBeInTheDocument();
    expect(screen.getByText("굵게").tagName).toBe("STRONG");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("does not render raw HTML (XSS)", () => {
    render(<Markdown text={'<img src=x onerror="alert(1)">텍스트'} />);
    expect(document.querySelector("img")).toBeNull();
  });

  it("opens links in a new tab with noopener", () => {
    render(<Markdown text={"[링크](https://example.com)"} />);
    const a = screen.getByRole("link", { name: "링크" });
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("renders incomplete markdown as plain text (streaming fallback)", () => {
    render(<Markdown text={"**미완성 굵"} />);
    expect(screen.getByText(/미완성 굵/)).toBeInTheDocument(); // 크래시 없이 표시
  });
});
```

- [ ] **Step 3: 실패 확인**

Run: `cd frontend && npm test -- --run components/Markdown`
Expected: FAIL — 모듈 없음

- [ ] **Step 4: 구현** — `frontend/components/Markdown.tsx`:

```tsx
// 공용 마크다운 렌더러 — AI 메시지·문서 뷰어·질문 preamble 전용.
// 사용자 입력은 여기로 보내지 않는다(plain text 유지 — spec §3).
// raw HTML은 react-markdown 기본 동작대로 렌더하지 않는다(XSS).
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm prose-slate max-w-none [&_table]:text-xs">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
```

주의: Tailwind `prose`는 @tailwindcss/typography 플러그인 필요 — `frontend/tailwind.config.*`에 플러그인이 없으면 `npm install -D @tailwindcss/typography` 후 plugins에 추가. 이미 있으면 스킵.

- [ ] **Step 5: AiMessage 적용** — `frontend/components/canvas/AiMessage.tsx`의 `<p className="whitespace-pre-wrap">{item.text}</p>`를 `<Markdown text={item.text} />`로 교체(import 추가). "AI가 작성 중…"·error 표시는 유지.

`AiMessage.test.tsx`에 렌더 확인 1건 추가:

```tsx
it("renders markdown in the AI bubble", () => {
  render(<AiMessage item={{ id: "1", role: "ai", text: "**중요**", trace: [], streaming: false, error: null }} />);
  expect(screen.getByText("중요").tagName).toBe("STRONG");
});
```

- [ ] **Step 6: 전체 통과 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add frontend/components/Markdown.tsx frontend/components/Markdown.test.tsx \
        frontend/components/canvas/AiMessage.tsx frontend/components/canvas/AiMessage.test.tsx \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): shared Markdown renderer — AI messages render markdown, XSS-safe"
```

---

### Task 5: 프론트 히스토리 주입 — getHistory + useWorkspaceStream + 타임라인 카드

**Files:**
- Modify: `frontend/lib/api/types.ts` (HistoryItem), `frontend/lib/api/client.ts` (getHistory)
- Modify: `frontend/lib/useWorkspaceStream.ts` (mount 로드 + historyLoading)
- Modify: `frontend/components/canvas/ChatTimeline.tsx` (히스토리 질문 카드 렌더)
- Test: `frontend/lib/useWorkspaceStream.test.tsx` (추가), `frontend/lib/api/client.test.ts` (추가)

**Interfaces:**
- Consumes: `GET /projects/{pid}/history` → `{"items": HistoryItem[]}` (Task 1).
- Produces: `types.HistoryItem { role: "user"|"ai"|"card"; text: string|null; card: "questions"|null; name: string|null }`.
- Produces: `client.getHistory(pid: string): Promise<HistoryItem[]>`.
- Produces: `useWorkspaceStream` 반환에 `historyLoading: boolean` 추가; `items` 초기값이 히스토리에서 변환된 ChatItem들로 채워짐. 히스토리 카드 아이템은 `{ id, role: "history-card", name }` 타입으로 ChatItem union 확장(기존 user/ai와 구분 — QuestionCardSlot 재사용 안 함, 단순 요약 표시).
- Produces: Task 6의 WelcomeCard 표시 조건이 `!historyLoading && items.length === 0 && !pendingQuestions`.

- [ ] **Step 1: 실패 테스트** — `useWorkspaceStream.test.tsx`에 추가(기존 vi.mock 패턴 — client mock에 getHistory 추가):

```tsx
it("loads history into items on mount", async () => {
  vi.mocked(client.getHistory).mockResolvedValue([
    { role: "user", text: "시작", card: null, name: null },
    { role: "ai", text: "환영", card: null, name: null },
    { role: "card", text: null, card: "questions", name: "mode-selection" },
  ]);
  const { result } = renderHook(() => useWorkspaceStream("p1"));
  expect(result.current.historyLoading).toBe(true);
  await act(async () => {});
  expect(result.current.historyLoading).toBe(false);
  expect(result.current.items.map((i) => i.role)).toEqual(["user", "ai", "history-card"]);
});

it("history load failure degrades to empty chat", async () => {
  vi.mocked(client.getHistory).mockRejectedValue(new Error("boom"));
  const { result } = renderHook(() => useWorkspaceStream("p1"));
  await act(async () => {});
  expect(result.current.historyLoading).toBe(false);
  expect(result.current.items).toEqual([]);
});
```

(기존 `vi.mock("@/lib/api/client", ...)` 팩토리에 `getHistory: vi.fn().mockResolvedValue([])` 기본값 추가 — 기존 테스트는 빈 히스토리로 무영향.)

`client.test.ts`에:

```tsx
it("getHistory GETs /history and returns items", async () => {
  server.use(http.get(`${API_BASE_URL}/projects/p1/history`, () =>
    HttpResponse.json({ items: [{ role: "user", text: "hi", card: null, name: null }] })));
  expect(await getHistory("p1")).toEqual([{ role: "user", text: "hi", card: null, name: null }]);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npm test -- --run lib/useWorkspaceStream lib/api/client`
Expected: FAIL

- [ ] **Step 3: 구현**

`types.ts`:

```typescript
export interface HistoryItem {
  role: "user" | "ai" | "card";
  text: string | null;
  card: "questions" | null;
  name: string | null;
}
```

`client.ts`:

```typescript
export async function getHistory(pid: string): Promise<HistoryItem[]> {
  const r = await request<{ items: HistoryItem[] }>(
    `/projects/${encodeURIComponent(pid)}/history`);
  return r.items;
}
```

`useWorkspaceStream.ts` — ChatItem union에 히스토리 카드 추가:

```typescript
export interface HistoryCardItem {
  id: string;
  role: "history-card";
  name: string | null;
}
export type ChatItem = UserItem | AiItem | HistoryCardItem;
```

훅에 `historyLoading` state(초기 true) + mount 효과(기존 getPending 효과와 별도):

```typescript
useEffect(() => {
  let cancelled = false;
  getHistory(projectId)
    .then((h) => {
      if (cancelled) return;
      setItems(h.map((it): ChatItem =>
        it.role === "card"
          ? { id: nextId(), role: "history-card", name: it.name }
          : it.role === "user"
            ? { id: nextId(), role: "user", text: it.text ?? "" }
            : { id: nextId(), role: "ai", text: it.text ?? "", trace: [], streaming: false, error: null }));
    })
    .catch(() => {})
    .finally(() => { if (!cancelled) setHistoryLoading(false); });
  return () => { cancelled = true; };
}, [projectId]);
```

`ChatTimeline.tsx` — `role === "history-card"` 분기 추가(간단 요약 카드, ml-11 idiom):

```tsx
if (item.role === "history-card") {
  return (
    <div key={item.id} className="ml-11 max-w-[85%]">
      <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-xs text-violet-700">
        📋 질문지 제시됨{item.name ? ` — ${item.name}` : ""}
      </div>
    </div>
  );
}
```

(ChatTimeline의 items prop 타입은 useWorkspaceStream의 ChatItem — useTurnStream의 CardItem 분기와 공존해야 하면 타입 좁히기는 `"card" in`이 아닌 `role` 스위치로 처리. useTurnStream 쪽 타입과 충돌 시 ChatTimeline의 prop을 두 union의 합집합으로 선언.)

- [ ] **Step 4: 전체 통과 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add frontend/lib/api/types.ts frontend/lib/api/client.ts frontend/lib/api/client.test.ts \
        frontend/lib/useWorkspaceStream.ts frontend/lib/useWorkspaceStream.test.tsx \
        frontend/components/canvas/ChatTimeline.tsx
git commit -m "feat(frontend): restore chat history on workspace mount"
```

---

### Task 6: 웰컴 카드 + 스크롤 분리 + 자동 스크롤

**Files:**
- Create: `frontend/components/workspace/WelcomeCard.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx` (웰컴 배선 + min-h-0 스크롤 교정)
- Modify: `frontend/components/canvas/ChatTimeline.tsx` (자동 스크롤)
- Test: `frontend/components/workspace/WelcomeCard.test.tsx`, `frontend/app/projects/[projectId]/workspace/page.test.tsx` (추가)

**Interfaces:**
- Produces: `<WelcomeCard onStart={(text: string) => void} />` — Path A/B 버튼 + 자유입력 안내.
- 표시 조건(페이지가 판단): `!historyLoading && items.length === 0 && !pendingQuestions && !streaming`.
- Path A 버튼 전송 문구: `"AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어."`
- Path B 버튼 전송 문구: `"AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어."`

- [ ] **Step 1: 실패 테스트** — `WelcomeCard.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WelcomeCard } from "./WelcomeCard";

describe("WelcomeCard", () => {
  it("sends the Path A message on button click", () => {
    const onStart = vi.fn();
    render(<WelcomeCard onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Path A/ }));
    expect(onStart).toHaveBeenCalledWith(
      "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.");
  });

  it("sends the Path B message on button click", () => {
    const onStart = vi.fn();
    render(<WelcomeCard onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: /Path B/ }));
    expect(onStart).toHaveBeenCalledWith(
      "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.");
  });

  it("mentions free-form input", () => {
    render(<WelcomeCard onStart={vi.fn()} />);
    expect(screen.getByText(/직접 입력해도/)).toBeInTheDocument();
  });
});
```

page.test.tsx에 표시 조건 테스트(기존 mock 패턴 — useWorkspaceStream mock 반환값 제어):

```tsx
it("shows the welcome card only when history is empty and loaded", async () => {
  mockStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false });
  render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
  expect(await screen.findByText(/어떻게 시작할까요/)).toBeInTheDocument();
});

it("hides the welcome card while history is loading or items exist", async () => {
  mockStream({ items: [], historyLoading: true, pendingQuestions: null, streaming: false });
  const { rerender } = render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
  expect(screen.queryByText(/어떻게 시작할까요/)).toBeNull();
});
```

(`mockStream`은 파일의 기존 useWorkspaceStream mock 헬퍼 — 없으면 그 파일 관례대로 작성.)

- [ ] **Step 2: 실패 확인** — 해당 테스트 FAIL.

- [ ] **Step 3: WelcomeCard 구현**

```tsx
// frontend/components/workspace/WelcomeCard.tsx
const PATH_A_MSG = "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.";
const PATH_B_MSG = "AI-PLC를 시작해줘. Path B(이미 정리된 유스케이스에서 시작)로 진행하고 싶어.";

export function WelcomeCard({ onStart }: { onStart: (text: string) => void }) {
  return (
    <div className="max-w-xl mx-auto mt-12 rounded-2xl border border-slate-200 bg-white p-6 text-center space-y-4">
      <p className="text-lg font-bold">어떻게 시작할까요?</p>
      <p className="text-sm text-slate-500">
        AI-PLC Discovery는 두 가지 경로로 시작할 수 있습니다.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 text-left">
        <button type="button" onClick={() => onStart(PATH_A_MSG)}
          className="rounded-xl border border-violet-200 bg-violet-50 hover:bg-violet-100 p-4">
          <p className="font-bold text-violet-700 text-sm">Path A — 페인 포인트에서 시작</p>
          <p className="mt-1 text-xs text-slate-600">
            고객 문제를 수집·분석해 PR/FAQ를 작성하고 솔루션을 도출합니다.
          </p>
        </button>
        <button type="button" onClick={() => onStart(PATH_B_MSG)}
          className="rounded-xl border border-sky-200 bg-sky-50 hover:bg-sky-100 p-4">
          <p className="font-bold text-sky-700 text-sm">Path B — 유스케이스에서 시작</p>
          <p className="mt-1 text-xs text-slate-600">
            이미 정리된 유스케이스가 있다면 우선순위화부터 진행합니다.
          </p>
        </button>
      </div>
      <p className="text-xs text-slate-400">직접 입력해도 됩니다 — 아래 입력창에 자유롭게 시작하세요.</p>
    </div>
  );
}
```

- [ ] **Step 4: 페이지 배선 + 스크롤 교정** — `workspace/page.tsx`:

(a) 웰컴: 중앙 컬럼에서 `showWelcome = !historyLoading && items.length === 0 && !pendingQuestions && !streaming`이면 `<ChatTimeline …>` 위(또는 timeline 빈 상태 대신) `<WelcomeCard onStart={send} />` 렌더.

(b) 스크롤 분리: 그리드 자식 3개 컬럼 각각에 `min-h-0` 클래스 확인·추가(누락이 스크롤 죽는 원인). 중앙 `<main className="flex flex-col min-w-0 min-h-0 ...">`, 우측 aside 내부 콘텐츠 영역에 `flex-1 min-h-0 overflow-y-auto`.

(c) 자동 스크롤 — `ChatTimeline.tsx`에 하단 앵커 + 근접 시에만 스크롤:

```tsx
const bottomRef = useRef<HTMLDivElement>(null);
const scrollerRef = useRef<HTMLDivElement>(null);
useEffect(() => {
  const el = scrollerRef.current;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  if (nearBottom) bottomRef.current?.scrollIntoView({ block: "end" });
}, [items]);
```

(스크롤 컨테이너 div에 `ref={scrollerRef}`, 리스트 끝에 `<div ref={bottomRef} />`. "use client" 지시어가 파일에 없으면 추가.)

- [ ] **Step 5: 전체 통과 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add frontend/components/workspace/WelcomeCard.tsx frontend/components/workspace/WelcomeCard.test.tsx \
        frontend/app/projects/\[projectId\]/workspace/page.tsx \
        frontend/app/projects/\[projectId\]/workspace/page.test.tsx \
        frontend/components/canvas/ChatTimeline.tsx
git commit -m "feat(frontend): welcome card with Path A/B starters + independent scroll with smart autoscroll"
```

---

### Task 7: 질문 복수선택 — 체크박스 + 콤마 조인

**Files:**
- Modify: `frontend/lib/api/types.ts` (Question.multi_select)
- Modify: `frontend/components/questions/QuestionCard.tsx`
- Test: `frontend/components/questions/QuestionCard.test.tsx` (추가)

**Interfaces:**
- Consumes: 질문 payload의 `multi_select?: boolean` (Task 3 — 없으면 false).
- Produces: multi_select 질문의 value 계약은 **기존과 동일한 string** — 복수 선택 시 `"A,C"` (letter를 정렬 없이 선택 순서 무관하게 letter 알파벳순 조인). QuestionForm의 answers dict·제출 경로는 무변경(문자열 그대로 흐름).

- [ ] **Step 1: 실패 테스트** — `QuestionCard.test.tsx`에 추가(파일의 기존 렌더 헬퍼 관례):

```tsx
const MULTI_Q = {
  number: 1, category: null, text: "페인포인트 유형은?", answer: null, multi_select: true,
  options: [
    { letter: "A", text: "속도", is_other: false, recommended: false },
    { letter: "B", text: "비용", is_other: false, recommended: false },
    { letter: "C", text: "품질", is_other: false, recommended: false },
  ],
};

it("renders checkboxes for multi_select and joins letters with comma", () => {
  let value = "";
  const onChange = (v: string) => { value = v; };
  const { rerender } = render(<QuestionCard question={MULTI_Q} value={value} onChange={onChange} />);
  expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  fireEvent.click(screen.getByRole("checkbox", { name: /속도/ }));
  rerender(<QuestionCard question={MULTI_Q} value={value} onChange={onChange} />);
  fireEvent.click(screen.getByRole("checkbox", { name: /품질/ }));
  expect(value).toBe("A,C");
});

it("unchecking removes the letter", () => {
  let value = "A,C";
  render(<QuestionCard question={MULTI_Q} value={value} onChange={(v) => { value = v; }} />);
  fireEvent.click(screen.getByRole("checkbox", { name: /속도/ }));
  expect(value).toBe("C");
});

it("single-select questions still render radios", () => {
  render(<QuestionCard question={{ ...MULTI_Q, multi_select: false }} value="" onChange={() => {}} />);
  expect(screen.getAllByRole("radio").length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: 실패 확인** — FAIL.

- [ ] **Step 3: 구현**

`types.ts` Question에 `multi_select?: boolean;` 추가(옵셔널 — 파일 파싱 경로엔 없음).

`QuestionForm.tsx` — preamble `<p className="text-sky-800">{file.preamble}</p>`를 `<Markdown text={file.preamble} />`로 교체(스펙 §3 — preamble 마크다운 렌더).

`QuestionCard.tsx` — multi_select 분기: 선택 상태는 `value.split(",").filter(Boolean)`의 Set, 토글 시:

```tsx
const selected = new Set(value.split(",").filter((s) => s && !s.startsWith("X:")));
function toggle(letter: string) {
  const next = new Set(selected);
  if (next.has(letter)) next.delete(letter); else next.add(letter);
  onChange([...next].sort().join(","));
}
```

`question.multi_select`면 `<input type="checkbox" checked={selected.has(opt.letter)} onChange={() => toggle(opt.letter)} />`, 아니면 기존 radio 유지. Other(X) 옵션의 자유 텍스트 처리는 기존 코드 경로를 확인해 multi에서도 동일 UX 유지(X 체크 시 텍스트 입력 노출, 값은 `"A,X:자유텍스트"` 형태가 아니라 기존 단일 관례를 따름 — 기존 QuestionCard의 Other 값 인코딩을 그대로 사용하되 콤마 조인과 충돌하면 X만 단독 선택으로 제한하고 주석으로 명시).

- [ ] **Step 4: 전체 통과 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add frontend/lib/api/types.ts frontend/components/questions/QuestionCard.tsx \
        frontend/components/questions/QuestionCard.test.tsx
git commit -m "feat(frontend): multi-select questions — checkboxes with comma-joined answers"
```

---

### Task 8: 첨부 업로드 UI — 클립 버튼 + 칩 + 자동 멘션

**Files:**
- Create: `frontend/components/workspace/AttachmentChips.tsx`
- Modify: `frontend/lib/api/client.ts` (uploadFile)
- Modify: `frontend/components/canvas/ChatInput.tsx` (클립 버튼 + 파일 선택)
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx` (첨부 상태 + 멘션 삽입)
- Test: `frontend/components/workspace/AttachmentChips.test.tsx`, `frontend/lib/api/client.test.ts` (추가), `frontend/app/projects/[projectId]/workspace/page.test.tsx` (추가)

**Interfaces:**
- Produces: `client.uploadFile(pid: string, file: File): Promise<{path: string; chars: number; truncated: boolean}>` — multipart POST.
- Produces: `<AttachmentChips paths={string[]} onRemove={(path) => void} />`.
- Produces: ChatInput에 `onAttach?: (file: File) => void` prop — 클립 버튼+숨은 `<input type="file" accept=".md,.txt,.csv,.xlsx,.pdf">`.
- 멘션 형식(전송 시 본문 앞): 각 첨부마다 `[첨부 파일: <path> — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 file_read로 읽으세요.]` 줄 + 빈 줄 + 원문.

- [ ] **Step 1: 실패 테스트**

`AttachmentChips.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AttachmentChips } from "./AttachmentChips";

it("renders one chip per path and removes on click", () => {
  const onRemove = vi.fn();
  render(<AttachmentChips paths={["uploads/의견.md", "uploads/설문-2.md"]} onRemove={onRemove} />);
  expect(screen.getByText("의견.md")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: /제거/ })[0]);
  expect(onRemove).toHaveBeenCalledWith("uploads/의견.md");
});

it("renders nothing when empty", () => {
  const { container } = render(<AttachmentChips paths={[]} onRemove={() => {}} />);
  expect(container.firstChild).toBeNull();
});
```

`client.test.ts`:

```tsx
it("uploadFile POSTs multipart and returns the stored path", async () => {
  let form: FormData | undefined;
  server.use(http.post(`${API_BASE_URL}/projects/p1/uploads`, async ({ request }) => {
    form = await request.formData();
    return HttpResponse.json({ path: "uploads/a.md", chars: 3, truncated: false });
  }));
  const r = await uploadFile("p1", new File(["abc"], "a.md", { type: "text/markdown" }));
  expect((form!.get("file") as File).name).toBe("a.md");
  expect(r.path).toBe("uploads/a.md");
});
```

page.test.tsx — 멘션 삽입(useWorkspaceStream mock의 send 스파이):

```tsx
it("prepends attachment mentions to the next message and clears chips", async () => {
  const send = vi.fn();
  mockStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false, send });
  // 페이지의 첨부 상태에 uploads/의견.md가 있는 상황을 만들고(업로드 mock 경유)
  // 입력창에 "이 파일 기반으로 진행해줘" 전송 →
  expect(send).toHaveBeenCalledWith(
    "[첨부 파일: uploads/의견.md — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 file_read로 읽으세요.]\n\n이 파일 기반으로 진행해줘");
});
```

(page.test의 구체 시나리오 구성은 파일의 기존 mock 헬퍼 관례에 맞춘다 — uploadFile은 `vi.mock("@/lib/api/client")`로 resolve 고정, 클립 input의 change 이벤트로 업로드 트리거.)

- [ ] **Step 2: 실패 확인** — FAIL.

- [ ] **Step 3: 구현**

`client.ts`:

```typescript
export async function uploadFile(
  pid: string, file: File,
): Promise<{ path: string; chars: number; truncated: boolean }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(pid)}/uploads`, {
    method: "POST",
    headers: authHeaders(),   // Content-Type은 브라우저가 boundary와 함께 설정
    body: form,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res.json();
}
```

`AttachmentChips.tsx`:

```tsx
export function AttachmentChips({ paths, onRemove }: {
  paths: string[]; onRemove: (path: string) => void;
}) {
  if (paths.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 px-1 pb-2">
      {paths.map((p) => (
        <span key={p} className="inline-flex items-center gap-1.5 rounded-full bg-violet-50 border border-violet-200 px-3 py-1 text-xs text-violet-700">
          📎 {p.replace(/^uploads\//, "")}
          <button type="button" aria-label={`${p} 제거`} onClick={() => onRemove(p)}
            className="text-violet-400 hover:text-violet-700">✕</button>
        </span>
      ))}
    </div>
  );
}
```

`ChatInput.tsx` — `onAttach?: (file: File) => void` prop 추가; textarea 왼쪽에 클립 버튼:

```tsx
{onAttach && (
  <>
    <button type="button" aria-label="파일 첨부" disabled={disabled}
      onClick={() => fileRef.current?.click()}
      className="shrink-0 text-slate-400 hover:text-violet-600 disabled:opacity-50">📎</button>
    <input ref={fileRef} type="file" hidden accept=".md,.txt,.csv,.xlsx,.pdf"
      onChange={(e) => {
        const f = e.target.files?.[0];
        if (f) onAttach(f);
        e.target.value = "";   // 같은 파일 재선택 허용
      }} />
  </>
)}
```

`workspace/page.tsx` — 첨부 상태 배선:

```tsx
const [attachments, setAttachments] = useState<string[]>([]);
const [uploadError, setUploadError] = useState<string | null>(null);

async function handleAttach(file: File) {
  setUploadError(null);
  try {
    const r = await uploadFile(projectId, file);
    setAttachments((prev) => [...prev, r.path]);
  } catch {
    setUploadError("업로드에 실패했습니다. 지원 형식(md/txt/csv/xlsx/pdf)·5MB 이하인지 확인하세요.");
  }
}

function sendWithAttachments(text: string) {
  const mentions = attachments.map((p) =>
    `[첨부 파일: ${p} — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 file_read로 읽으세요.]`);
  send(mentions.length ? `${mentions.join("\n")}\n\n${text}` : text);
  setAttachments([]);
}
```

`<ChatInput onSend={sendWithAttachments} onAttach={handleAttach} disabled={streaming} />` + 입력창 위에 `<AttachmentChips paths={attachments} onRemove={(p) => setAttachments((a) => a.filter((x) => x !== p))} />` + uploadError 표시. (WelcomeCard의 onStart는 `sendWithAttachments`가 아닌 `send` 그대로 — 첨부 없는 시작 메시지.)

- [ ] **Step 4: 전체 통과 + Commit**

Run: `cd frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add frontend/components/workspace/AttachmentChips.tsx \
        frontend/components/workspace/AttachmentChips.test.tsx \
        frontend/lib/api/client.ts frontend/lib/api/client.test.ts \
        frontend/components/canvas/ChatInput.tsx \
        frontend/app/projects/\[projectId\]/workspace/page.tsx \
        frontend/app/projects/\[projectId\]/workspace/page.test.tsx
git commit -m "feat(frontend): file attachments — upload, chips, auto-mention on next message"
```

---

### Task 9: 문서 리뷰 개편 — 파일 트리 + 마크다운 뷰어

**Files:**
- Create: `frontend/components/review/DocTree.tsx`
- Modify: `frontend/lib/api/client.ts` (readArtifact)
- Modify: `frontend/app/projects/[projectId]/review/page.tsx`
- Test: `frontend/components/review/DocTree.test.tsx`, `frontend/app/projects/[projectId]/review/page.test.tsx` (수정)

**Interfaces:**
- Consumes: `listArtifacts(pid)` (기존 — aiplc-docs/ 전체 경로 리스트), `GET /projects/{pid}/questions/{path}`는 질문 파싱용이므로 **범용 파일 조회로 부적합** — 신규 `client.readArtifact(pid, path)`가 필요하다. 백엔드에 범용 파일 read 라우트가 없으므로 이 태스크에서 추가한다: `GET /projects/{pid}/files/{path:path}` → `{"content": str}` (aiplc-docs/ 하위만 허용 — 그 외 403).
- Produces: `<DocTree paths={string[]} selected={string|null} onSelect={(p) => void} />` — 디렉토리 그룹핑.
- Produces: 리뷰 페이지 — 좌 트리 / 우 `<Markdown>` 뷰어, discovery-document.md 기본 선택, ApprovalGate는 discovery-document 선택 시에만.

- [ ] **Step 1: 백엔드 파일 조회 라우트 실패 테스트** — `backend/tests/test_routes_artifacts.py`에 추가:

```python
def test_read_artifact_returns_content_and_guards_prefix(monkeypatch):
    # 기존 파일의 로컬 프로젝트 헬퍼 관례로 프로젝트 생성 후:
    ws = app_module.registry.get(PID)
    # aiplc-docs 파일 심기 (LocalSandbox write_file)
    ...
    r = client.get(f"/projects/{PID}/files/aiplc-docs/discovery/prfaq.md")
    assert r.status_code == 200 and r.json()["content"].startswith("# PR")
    assert client.get(f"/projects/{PID}/files/uploads/x.md").status_code == 403
    assert client.get(f"/projects/{PID}/files/aiplc-docs/none.md").status_code == 404
```

(파일의 기존 헬퍼로 완성 — write는 `ws.sandbox.write_file` async 호출 관례.)

- [ ] **Step 2: 백엔드 라우트 구현** — `backend/pathfinder/routes/artifacts.py`에 추가:

```python
@router.get("/projects/{pid}/files/{path:path}")
async def read_artifact(pid: str, path: str):
    # 리뷰 화면 전용 범용 파일 뷰어 — 산출물(aiplc-docs/)만. uploads/ 등
    # 입력물·기타 경로는 산출물이 아니므로 노출하지 않는다(403).
    if not path.startswith("aiplc-docs/"):
        raise HTTPException(status_code=403, detail="artifacts only")
    try:
        content = await get_workspace(pid).sandbox.read_file(path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="not found")
    from pathfinder.parsers.redaction import redact_credentials
    return {"content": redact_credentials(content)}
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_artifacts.py -q` → PASS 후 전체 스위트 확인.

- [ ] **Step 3: DocTree 실패 테스트** — `DocTree.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DocTree } from "./DocTree";

const PATHS = [
  "aiplc-docs/aiplc-state.md",
  "aiplc-docs/audit.md",
  "aiplc-docs/discovery/prfaq.md",
  "aiplc-docs/discovery/discovery-document.md",
];

it("groups files by directory and highlights the selection", () => {
  render(<DocTree paths={PATHS} selected="aiplc-docs/discovery/prfaq.md" onSelect={vi.fn()} />);
  expect(screen.getByText("discovery")).toBeInTheDocument();  // 디렉토리 그룹 헤더
  expect(screen.getByRole("button", { name: /prfaq\.md/ })).toHaveAttribute("aria-current", "true");
});

it("selects a file on click", () => {
  const onSelect = vi.fn();
  render(<DocTree paths={PATHS} selected={null} onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: /audit\.md/ }));
  expect(onSelect).toHaveBeenCalledWith("aiplc-docs/audit.md");
});
```

- [ ] **Step 4: 프론트 구현**

`client.ts`:

```typescript
export async function readArtifact(pid: string, path: string): Promise<string> {
  const r = await request<{ content: string }>(
    `/projects/${encodeURIComponent(pid)}/files/${encodePath(path)}`);
  return r.content;
}
```

`DocTree.tsx`:

```tsx
export function DocTree({ paths, selected, onSelect }: {
  paths: string[]; selected: string | null; onSelect: (path: string) => void;
}) {
  const groups = new Map<string, string[]>();
  for (const p of paths) {
    const rel = p.replace(/^aiplc-docs\//, "");
    const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
    groups.set(dir, [...(groups.get(dir) ?? []), p]);
  }
  return (
    <nav aria-label="산출물 문서" className="text-sm space-y-3">
      {[...groups.entries()].sort().map(([dir, files]) => (
        <div key={dir || "(root)"}>
          {dir && <p className="px-2 pb-1 text-[11px] font-bold uppercase text-slate-400">{dir}</p>}
          {files.sort().map((p) => {
            const name = p.slice(p.lastIndexOf("/") + 1);
            const active = p === selected;
            return (
              <button key={p} type="button" onClick={() => onSelect(p)}
                aria-current={active ? "true" : undefined}
                className={`w-full text-left px-2.5 py-1.5 rounded-lg truncate ${
                  active ? "bg-violet-50 text-violet-700 font-medium" : "text-slate-600 hover:bg-slate-100"}`}>
                📄 {name}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
```

`review/page.tsx` 개편 — 상태 `selected: string | null`; `listArtifacts` 로드 후 `discovery-document.md`로 끝나는 경로가 있으면 기본 선택; `readArtifact(projectId, selected)`를 useAsync로 로드해 우측에 `<Markdown text={content} />`; `selected?.endsWith("discovery-document.md")`일 때만 기존 `<ApprovalGate …>`/수정요청 UI 렌더. 좌측은 `<DocTree paths={artifacts} selected={selected} onSelect={setSelected} />` (기존 DocumentPanel/VerificationSummary는 discovery-document 선택 시 유지 배치 — 파일 기존 구성 존중). 레이아웃: `grid lg:grid-cols-[240px_1fr] gap-6`.

page.test.tsx 수정: 트리 렌더 + 기본 선택 + 게이트 조건부 3단언(기존 mock 관례).

- [ ] **Step 5: 전체 통과 + Commit**

Run: `cd backend && .venv/bin/python -m pytest -q && cd ../frontend && npm test && npx tsc --noEmit` → PASS

```bash
git add backend/pathfinder/routes/artifacts.py backend/tests/test_routes_artifacts.py \
        frontend/components/review/DocTree.tsx frontend/components/review/DocTree.test.tsx \
        frontend/lib/api/client.ts frontend/app/projects/\[projectId\]/review/page.tsx \
        frontend/app/projects/\[projectId\]/review/page.test.tsx
git commit -m "feat(review): document tree + markdown viewer — all artifacts browsable, gate on discovery-document only"
```

---

### Task 10: e2e 확장 + 최종 검증

**Files:**
- Modify: `frontend/e2e/workspace.spec.ts`
- Test: 전체 4 스위트

**Interfaces:**
- Consumes: 전체 (Tasks 1–9). LocalSandbox 데모 시나리오 전제(local 모드 백엔드).

- [ ] **Step 1: e2e 시나리오 확장** — `workspace.spec.ts`에 추가/수정:

1. **웰컴 카드 시작**: 새 프로젝트 → `/workspace` → 웰컴 카드 표시 확인 → "Path A" 버튼 클릭 → 데모 질문 폼 도착(웰컴 카드 사라짐).
2. **복수선택**: LocalSandbox 데모 질문에 multi_select가 없으므로(전부 radio) — 이 검증은 유닛에 위임하고 e2e는 radio 폼 왕복 유지.
3. **스크롤 컨테이너**: 채팅 스크롤 영역(`overflow-y-auto`)과 우측 패널이 각각 존재함을 locator로 확인.
4. **마크다운**: 데모 응답 메시지가 `<strong>`/heading으로 렌더되는지 확인 — LocalSandbox 데모 메시지에 마크다운이 없으면 이 단계는 "AI 메시지 컨테이너에 prose 클래스 존재" 확인으로 대체.
5. **히스토리**: local 모드는 세션 오브젝트가 없어 빈 히스토리(웰컴 카드 재표시)가 정상 — 탭 이동 후 복귀 시나리오는 검증 불가하므로 spec대로 실 microvm 드릴 항목으로 코멘트 처리.

- [ ] **Step 2: e2e 실행** (백엔드 local 모드 + 프론트 기동 — Task 12 방식과 동일 포트 충돌 회피)

Run: `cd frontend && npm run test:e2e`
Expected: PASS

- [ ] **Step 3: 최종 4중 검증**

```bash
cd backend && .venv/bin/python -m pytest -q && \
cd ../harness && .venv/bin/python -m pytest -q && \
cd ../frontend && npm test && npx tsc --noEmit && \
cd ../infra && npx cdk synth > /dev/null && echo ALL_GREEN
```
Expected: `ALL_GREEN`

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/workspace.spec.ts
git commit -m "test(e2e): welcome-card start flow + scroll containers"
```

---

## 실 VM 반영 노트 (플랜 스코프 밖 — 완료 후 수동)

- Task 3의 하네스 변경(QUESTIONS_SCHEMA_HINT)은 `cd infra && ./package-harness.sh && npx cdk deploy`로 이미지 재배포해야 실 VM에 반영된다.
- 실 microvm 모드에서 확인할 것: 히스토리 복원(탭 이동·새로고침), multi_select 질문 실제 생성, 첨부 파일 file_read 왕복(uploads/ → VM 복원 → 에이전트 읽기).

## Self-Review 결과

- **스펙 커버리지**: §2 히스토리(T1/T5), §3 마크다운(T4, 뷰어는 T9, preamble은 T9의 Markdown 재사용 — QuestionForm preamble 적용은 T4 Step 5에 포함시키지 않았으므로 T9에서 리뷰 뷰어와 함께 확인), §4 웰컴(T6), §5 복수선택(T3/T7), §6 첨부(T2/T8, uploads sync T2 Step 8), §7 스크롤(T6), §8 리뷰 트리(T9), §9 테스트(각 태스크+T10). 갭: preamble 마크다운은 우측 패널 QuestionForm에서 렌더 — T7 Step 3에서 QuestionForm preamble `<p>`를 `<Markdown>`으로 교체하는 한 줄을 T7에 포함한다(수정 반영).
- **플레이스홀더**: Step 1 테스트 코드 2곳(T9 백엔드 테스트, T8 page.test)이 파일 관례 의존으로 축약됨 — 의도적(기존 헬퍼 재사용 지시), 단언 내용은 명시됨.
- **타입 일관성**: HistoryItem(role/text/card/name) T1=T5, uploadFile 반환(path/chars/truncated) T2=T8, multi_select T3=T7, readArtifact T9 내 자기완결. `historyLoading` T5 정의=T6 소비. 확인 완료.
