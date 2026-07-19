# Strands 엔진 전환 + 분할 워크스페이스 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MicroVM 안의 에이전트 엔진을 Claude Code 서브프로세스에서 Strands Agents SDK로 교체(대화 컨텍스트 S3 영속화 + 구조화 이벤트 계약)하고, 프론트를 3분할 워크스페이스 단일 화면으로 개편한다.

**Architecture:** 하네스의 드라이버 계층만 교체한다(spec 승인안 A). `harness/strands_driver.py`가 Strands Agent를 인프로세스로 돌리고, UI 접점 3개(@tool: ask_questions/report_stage/submit_document)가 구조화 AgentEvent(`questions`/`stage`/`document`)를 발행한다. 질문은 `ToolContext.interrupt()`로 대기하고 `S3SessionManager`가 대화+interrupt 상태를 S3에 영속화한다 — VM이 죽어도 같은 session_id로 복원된다. 백엔드는 파일 파싱 대신 이벤트를 중계하고, 프론트는 `/projects/[id]/workspace` 3분할 화면(1:4.5:4.5)이 질문·캔버스 탭을 대체한다.

**Tech Stack:** Python 3.11 (backend FastAPI / harness Starlette), `strands-agents>=1.48,<2`, Next.js 15 + Vitest, CDK(TypeScript).

**Spec:** `docs/superpowers/specs/2026-07-19-strands-engine-split-workspace-design.md`

## Global Constraints

- Python 3.11 (backend/harness venv). Node 20+ (frontend).
- `strands-agents>=1.48,<2` — 검증된 API: `S3SessionManager`/`FileSessionManager`(`strands.session`), `ToolContext.interrupt(name, reason=None)`(`from strands import ToolContext`), `agent.stream_async()`, `AgentResult.stop_reason == "interrupt"` + `AgentResult.interrupts`, 재개는 `agent.stream_async([{"interruptResponse": {"interruptId": ..., "response": ...}}])`.
- 백엔드/하네스 `AgentEvent` 미러는 필드가 **완전히 동일**해야 한다(kind/text/path/payload) — SSE 계약.
- 모델 env 계약 유지: `ANTHROPIC_MODEL` = Bedrock inference profile id (`global.anthropic.claude-sonnet-5`). 인증은 IAM 롤(장기 키 없음).
- 파일 산출물(aiplc-docs/)은 계속 생성 — 이벤트가 UI 계약, 파일은 기록(스펙 §3).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 테스트 실행: backend `cd backend && .venv/bin/python -m pytest -q`, harness `cd harness && .venv/bin/python -m pytest -q`, frontend `cd frontend && npm test`, infra `cd infra && npx cdk synth`.

## 스펙 대비 확정 세부사항 (구현 중 재결정 금지)

1. **파일 도구는 커스텀 @tool** (`strands_tools` 패키지 대신): `strands_tools.file_write`는 대화형 consent 프롬프트가 있고(`BYPASS_TOOL_CONSENT` env 필요), 워크스페이스 경로 확장(escape) 가드가 없다. 기존 `claude_driver._rel()`과 동일한 confinement를 갖는 커스텀 `file_read`/`file_write`/`list_files` 도구를 하네스에 구현한다. 의존성도 `strands-agents` 하나로 줄어든다.
2. **`/pending`은 POST** (스펙 초안의 GET에서 변경): 하네스가 S3 세션에서 agent를 복원하려면 session 설정(bucket/region/session_id)이 필요한데, 이는 요청 본문으로 전달된다(아래 3). 의미는 스펙과 동일 — 대기 중 질문 조회.
3. **세션 설정은 요청별 전달**: MicroVM 이미지 env는 빌드 시점에 구워지고 `run_microvm`은 VM별 env를 받지 않으므로, project별 session 설정(`{"session_id", "bucket", "region", "prefix"}`)은 백엔드가 `/message`·`/answers`·`/pending` 요청 본문의 `session` 필드로 보낸다. 하네스는 session_id별로 Agent를 캐시한다. `bucket=""`이면 `FileSessionManager`(로컬/테스트 폴백).
4. **IAM 경계 변경(명시적)**: 현재 exec 롤은 "Bedrock only, NO S3"(pathfinder-drill-stack.ts:54). S3SessionManager가 VM 안에서 돌려면 exec 롤에 **`sessions/*` prefix 한정** S3 read/write를 허용해야 한다. `projects/*`(아티팩트)는 여전히 접근 불가 — 경계 완화는 세션 상태 prefix에만 국한된다.
5. **interrupt는 도구 재실행 모델**: 재개 시 `ask_questions` 도구가 처음부터 다시 실행되고 `interrupt()`가 사용자 응답을 반환한다. `interrupt()` 호출 앞에 부작용을 두지 말 것(idempotent).
6. **텍스트는 `data` 델타로, 구조화 이벤트는 assistant 메시지의 toolUse 블록으로** 번역한다. assistant 메시지의 text 블록은 무시(델타와 중복).
7. **드라이버 전환 플래그**: `PATHFINDER_DRIVER` env (`strands` 기본 / `claude` 롤백). `claude_driver.py`는 삭제하지 않고 유지 — 롤백 시 Dockerfile의 claude-code 설치 라인을 되살려 재배포한다.

## File Structure

```
harness/
  events.py            (신규) AgentEvent 미러 단일 정의 — claude_driver/strands_driver 공용
  aiplc_tools.py       (신규) @tool 5종: ask_questions/report_stage/submit_document/file_read/file_write + 큐 발행
  strands_driver.py    (신규) StrandsDriver — agent 캐시, stream_async→AgentEvent 번역, run/run_answers/pending
  claude_driver.py     (수정) AgentEvent 정의 제거 → events.py import
  app.py               (수정) POST /answers, POST /pending 라우트 + session 본문 플럼빙
  serve.py             (수정) PATHFINDER_DRIVER 플래그로 드라이버 선택
  hooks.py             (수정) claude_cli_diagnostic → strands import 진단
  requirements.txt     (수정) + strands-agents
  Dockerfile           (수정) claude-code/npm 제거
backend/pathfinder/
  sandbox/base.py      (수정) AgentEvent kind/payload 확장 + Sandbox.send_answers/pending
  sandbox/local.py     (수정) 구조화 이벤트 스크립트 + send_answers/pending
  sandbox/harness.py   (수정) session 파라미터 + send_answers/pending
  sandbox/microvm.py   (수정) send_answers/pending (턴 가드·sync 동일 패턴)
  routes/turns.py      (수정) GET /answers/stream + GET /pending
  app.py               (수정) session 설정 조립(_make_microvm_sandbox)
infra/lib/pathfinder-drill-stack.ts  (수정) exec 롤 sessions/* S3 grant + PATHFINDER_DRIVER env
frontend/
  lib/api/types.ts     (수정) kind/payload 확장 + QuestionsPayload/StagePayload/DocumentPayload
  lib/api/sse.ts       (수정) streamAnswers 추가
  lib/api/client.ts    (수정) getPending 추가
  lib/useWorkspaceStream.ts (신규) 워크스페이스 상태 훅 — 채팅+질문+스테이지+문서 통합
  components/workspace/WorkspaceRightPanel.tsx (신규) 질문 폼/프리뷰/산출물 전환 패널
  app/projects/[projectId]/workspace/page.tsx  (신규) 3분할 그리드 화면
  app/projects/[projectId]/questions/page.tsx  (교체) → /workspace redirect
  app/projects/[projectId]/canvas/page.tsx     (교체) → /workspace redirect
  components/AppHeader.tsx (수정) 탭: 대시보드|워크스페이스|문서 리뷰
```

---

### Task 1: 이벤트 계약 — AgentEvent 확장 (backend + harness 공용 미러)

**Files:**
- Modify: `backend/pathfinder/sandbox/base.py`
- Create: `harness/events.py`
- Modify: `harness/claude_driver.py` (AgentEvent 정의 → import 교체)
- Test: `backend/tests/test_sandbox_base.py`, `harness/tests/test_events.py`

**Interfaces:**
- Produces: `AgentEvent(kind, text, path, payload)` — kind에 `"questions" | "stage" | "document"` 추가, `payload: str | None` 필드 추가(구조화 JSON 직렬화 문자열). 이후 모든 태스크가 이 모델을 쓴다.
- Produces: `Sandbox.send_answers(answers: dict[str, str]) -> AsyncIterator[AgentEvent]` (abstract), `Sandbox.pending() -> str | None` (abstract) — Task 5/7이 구현.

- [ ] **Step 1: backend 실패 테스트 작성** — `backend/tests/test_sandbox_base.py`에 추가:

```python
def test_agent_event_structured_kinds_and_payload():
    from pathfinder.sandbox.base import AgentEvent
    ev = AgentEvent(kind="questions", payload='{"interrupt_id":"i-1","questions":[]}')
    assert ev.payload == '{"interrupt_id":"i-1","questions":[]}'
    assert AgentEvent(kind="stage").payload is None
    AgentEvent(kind="document")  # must not raise

def test_sandbox_abc_requires_answers_and_pending():
    from pathfinder.sandbox.base import Sandbox
    assert "send_answers" in Sandbox.__abstractmethods__
    assert "pending" in Sandbox.__abstractmethods__
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_sandbox_base.py -q`
Expected: FAIL — `ValidationError`(kind "questions" 미허용) / KeyError

- [ ] **Step 3: base.py 구현** — `backend/pathfinder/sandbox/base.py`의 AgentEvent와 Sandbox를 다음으로 교체(주석·input_holder 블록은 유지):

```python
class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None
    # Structured payload (JSON string) for questions/stage/document — the
    # event IS the UI contract (spec §4); files stay as records only.
    payload: str | None = None
```

Sandbox ABC에 추가:

```python
    @abstractmethod
    def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]: ...
    @abstractmethod
    async def pending(self) -> str | None: ...
```

- [ ] **Step 4: harness/events.py 생성** — 미러 단일 정의(harness는 backend 패키지를 import할 수 없음):

```python
# harness/events.py — mirror of backend/pathfinder/sandbox/base.py AgentEvent.
# Fields MUST stay identical (kind/text/path/payload) or the SSE contract breaks.
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None
    payload: str | None = None
```

`harness/claude_driver.py`에서 `class AgentEvent` 블록(그 위의 미러 주석 포함)을 삭제하고 `from events import AgentEvent`로 교체.

- [ ] **Step 5: harness 테스트 작성** — `harness/tests/test_events.py`:

```python
def test_event_mirror_has_payload_and_structured_kinds():
    from events import AgentEvent
    ev = AgentEvent(kind="stage", payload='{"stage":"Envision"}')
    assert ev.payload == '{"stage":"Envision"}'
    from claude_driver import AgentEvent as DriverEvent
    assert DriverEvent is AgentEvent  # single definition, no drift
```

- [ ] **Step 6: 전체 실행 — 기존 테스트 파손 수리 포함**

Run: `cd backend && .venv/bin/python -m pytest -q && cd ../harness && .venv/bin/python -m pytest -q`
Expected: FAIL — `LocalSandbox`가 새 abstract 메서드 미구현으로 인스턴스화 불가. **이 태스크에서는 최소 스텁만** 넣어 GREEN으로 만든다(구조화 스크립트는 Task 5):

`backend/pathfinder/sandbox/local.py`의 LocalSandbox에 추가:

```python
    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(kind="done")

    async def pending(self) -> str | None:
        return None
```

`backend/pathfinder/sandbox/microvm.py`의 MicroVMSandbox에 추가(Task 7이 실구현으로 교체):

```python
    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(kind="error", text="not implemented")

    async def pending(self) -> str | None:
        return None
```

재실행하여 PASS 확인.

- [ ] **Step 7: Commit**

```bash
git add backend/pathfinder/sandbox/base.py backend/pathfinder/sandbox/local.py \
        backend/pathfinder/sandbox/microvm.py backend/tests/test_sandbox_base.py \
        harness/events.py harness/claude_driver.py harness/tests/test_events.py
git commit -m "feat(contract): AgentEvent questions/stage/document kinds + payload; Sandbox answers/pending seam"
```

---

### Task 2: 하네스 UI 접점 도구 — aiplc_tools.py

**Files:**
- Create: `harness/aiplc_tools.py`
- Test: `harness/tests/test_aiplc_tools.py`

**Interfaces:**
- Consumes: `events.AgentEvent` (Task 1).
- Produces: `build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list` — Strands `Agent(tools=...)`에 그대로 넣는 도구 리스트. `emit`은 도구 실행 중 발생한 구조화 이벤트를 드라이버 큐로 밀어넣는 콜백. `ask_questions`의 interrupt `reason`은 `{"questions_payload": <dict>}` 형태 — Task 3의 드라이버가 `result.interrupts[i].reason["questions_payload"]`로 읽는다.
- Produces: 질문 페이로드 dict 스키마(= backend `models.py`의 QuestionFile 미러 — 프론트 QuestionForm이 무변경 렌더):

```json
{"name": "pain-point-questions", "preamble": "…", "questions": [
  {"number": 1, "category": "고객", "text": "주요 고객은?", "answer": null,
   "options": [{"letter": "A", "text": "사내 PM", "is_other": false, "recommended": true},
               {"letter": "X", "text": "Other", "is_other": true, "recommended": false}]}]}
```

- [ ] **Step 1: 실패 테스트 작성** — `harness/tests/test_aiplc_tools.py`:

```python
import json
import pytest
from events import AgentEvent
from aiplc_tools import build_tools, QUESTIONS_SCHEMA_HINT


class FakeToolContext:
    """Duck-typed ToolContext: interrupt() returns a canned response (resume
    semantics) or raises to emulate the first-pass suspension."""
    def __init__(self, response=None, raise_first=False):
        self._response = response
        self._raise = raise_first
        self.calls = []

    def interrupt(self, name, reason=None):
        self.calls.append((name, reason))
        if self._raise:
            raise RuntimeError("suspended")  # stands in for InterruptException
        return self._response


def _tool_by_name(tools, name):
    return next(t for t in tools if getattr(t, "tool_name", getattr(t, "__name__", "")) == name)


def test_report_stage_emits_stage_event_and_acks(tmp_path):
    emitted: list[AgentEvent] = []
    tools = build_tools(str(tmp_path), emitted.append)
    report_stage = _tool_by_name(tools, "report_stage")
    out = report_stage(stage="Envision", status="in_progress", summary="PR/FAQ 작성 중")
    assert emitted[0].kind == "stage"
    assert json.loads(emitted[0].payload) == {
        "stage": "Envision", "status": "in_progress", "summary": "PR/FAQ 작성 중"}
    assert "Envision" in out

def test_submit_document_emits_document_event(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    submit_document = _tool_by_name(tools, "submit_document")
    submit_document(path="aiplc-docs/discovery/discovery-document.md",
                    version="v2", summary="솔루션 분석 반영")
    assert emitted[0].kind == "document"
    assert json.loads(emitted[0].payload)["version"] == "v2"

def test_ask_questions_interrupts_with_payload_and_returns_answers(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    ask = _tool_by_name(tools, "ask_questions")
    payload = {"name": "pain-point-questions", "preamble": None, "questions": [
        {"number": 1, "category": None, "text": "주요 고객은?", "answer": None,
         "options": [{"letter": "A", "text": "사내 PM", "is_other": False, "recommended": True}]}]}
    ctx = FakeToolContext(response={"1": "A"})
    result = ask(questions_file=payload, tool_context=ctx)
    name, reason = ctx.calls[0]
    assert name == "ask_questions"
    assert reason["questions_payload"] == payload
    assert "1" in result  # answers are returned to the model as the tool result

def test_file_write_confined_and_emits_file_changed(tmp_path):
    emitted = []
    tools = build_tools(str(tmp_path), emitted.append)
    fw = _tool_by_name(tools, "file_write")
    fw(path="aiplc-docs/audit.md", content="# audit")
    assert (tmp_path / "aiplc-docs" / "audit.md").read_text() == "# audit"
    assert emitted[0].kind == "file_changed" and emitted[0].path == "aiplc-docs/audit.md"
    with pytest.raises(ValueError):
        fw(path="../etc/passwd", content="x")

def test_file_read_confined(tmp_path):
    (tmp_path / "aiplc-rules").mkdir()
    (tmp_path / "aiplc-rules" / "r.md").write_text("rule")
    tools = build_tools(str(tmp_path), lambda e: None)
    fr = _tool_by_name(tools, "file_read")
    assert fr(path="aiplc-rules/r.md") == "rule"
    with pytest.raises(ValueError):
        fr(path="/etc/passwd")
```

- [ ] **Step 2: 실패 확인**

Run: `cd harness && .venv/bin/pip install "strands-agents>=1.48,<2" && .venv/bin/python -m pytest tests/test_aiplc_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: aiplc_tools`

- [ ] **Step 3: 구현** — `harness/aiplc_tools.py`:

```python
# harness/aiplc_tools.py — the agent's UI contact points (spec §3).
# Code enforces the UI contract; the rules (markdown) drive the content.
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from strands import tool
from events import AgentEvent

# Injected into the system prompt so the model produces payloads the frontend
# QuestionForm renders unchanged (mirror of backend models.QuestionFile).
QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "questions": [{"number": int, '
    '"category": str|null, "text": str, "answer": null, "options": '
    '[{"letter": "A".."F"|"X", "text": str, "is_other": bool, "recommended": bool}]}]}'
)


def _confine(workspace: str, rel: str) -> Path:
    """Resolve rel against the workspace and reject escapes (same guarantee
    as claude_driver._rel, but raising — a tool error is surfaced to the
    model as a tool failure, not silently ignored)."""
    ws = Path(workspace).resolve()
    p = (ws / rel).resolve()
    if not p.is_relative_to(ws) or rel.startswith("/"):
        raise ValueError(f"path escapes workspace: {rel}")
    return p


def build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list:
    """Build the five tools bound to this workspace + event sink. `emit` is
    called synchronously during tool execution; the driver drains it into
    the SSE stream."""

    @tool(context=True)
    def ask_questions(questions_file: dict, tool_context: Any) -> str:
        """사용자에게 객관식 질문 세트를 제시하고 답변을 기다린다. 질문은
        반드시 이 도구로만 전달한다(파일로만 남기지 말 것). questions_file은
        QUESTIONS_SCHEMA_HINT의 JSON 스키마를 따라야 한다.

        Args:
            questions_file: 질문 파일 페이로드(dict) — name/preamble/questions.
        """
        # NOTE: interrupt() 앞에 부작용 금지 — resume 시 이 함수는 처음부터
        # 재실행되고 interrupt()가 사용자 답변을 반환한다(재실행 모델).
        answers = tool_context.interrupt(
            "ask_questions", reason={"questions_payload": questions_file})
        return f"사용자 답변: {json.dumps(answers, ensure_ascii=False)}"

    @tool
    def report_stage(stage: str, status: str, summary: str = "") -> str:
        """Discovery 스테이지 전이를 선언한다. 스테이지를 시작/완료할 때마다
        반드시 호출한다(aiplc-state.md 기록과 별개).

        Args:
            stage: 스테이지 이름 (예: "Envision").
            status: "pending" | "in_progress" | "completed".
            summary: 한 줄 요약.
        """
        emit(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage, "status": status, "summary": summary}, ensure_ascii=False)))
        return f"stage recorded: {stage} ({status})"

    @tool
    def submit_document(path: str, version: str, summary: str = "") -> str:
        """discovery-document 등 리뷰 대상 문서가 준비/갱신되었음을 선언한다.

        Args:
            path: 워크스페이스 상대 경로.
            version: 버전 라벨 (예: "v2").
            summary: 변경 요약.
        """
        emit(AgentEvent(kind="document", payload=json.dumps(
            {"path": path, "version": version, "summary": summary}, ensure_ascii=False)))
        return f"document submitted: {path} {version}"

    @tool
    def file_read(path: str) -> str:
        """워크스페이스 파일을 읽는다 (룰 상세 로드 등).

        Args:
            path: 워크스페이스 상대 경로.
        """
        return _confine(workspace, path).read_text(encoding="utf-8")

    @tool
    def file_write(path: str, content: str) -> str:
        """워크스페이스 파일을 쓴다 (aiplc-docs/ 산출물 등).

        Args:
            path: 워크스페이스 상대 경로.
            content: 파일 전체 내용.
        """
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        emit(AgentEvent(kind="file_changed", path=path))
        return f"written: {path}"

    return [ask_questions, report_stage, submit_document, file_read, file_write]
```

- [ ] **Step 4: 통과 확인**

Run: `cd harness && .venv/bin/python -m pytest tests/test_aiplc_tools.py -q`
Expected: PASS (6 tests). 주의: `@tool` 데코레이터가 함수를 감싸므로 `_tool_by_name`은 `tool_name` 속성 우선으로 찾는다 — strands 버전에 따라 직접 호출 시그니처가 다르면 테스트의 호출부를 `ask(questions_file=..., tool_context=ctx)` 대신 데코레이터가 노출하는 원함수(`ask.original_function` 등)로 조정하되, **도구 이름 5종과 emit/confine 동작 단언은 유지**한다.

- [ ] **Step 5: Commit**

```bash
git add harness/aiplc_tools.py harness/tests/test_aiplc_tools.py harness/requirements.txt
git commit -m "feat(harness): aiplc UI-contact tools — ask_questions interrupt, stage/document events, confined file io"
```

---

### Task 3: StrandsDriver — stream_async → AgentEvent 번역 + 세션/interrupt

**Files:**
- Create: `harness/strands_driver.py`
- Test: `harness/tests/test_strands_driver.py`

**Interfaces:**
- Consumes: `build_tools(workspace, emit)` (Task 2), `events.AgentEvent` (Task 1).
- Produces: `StrandsDriver(workspace: str, agent_factory: Callable[[dict, Callable], Any] | None = None)`:
  - `run(text: str, session: dict) -> AsyncIterator[AgentEvent]` — 자유 텍스트 턴
  - `run_answers(interrupt_id: str, answers: dict[str, str], session: dict) -> AsyncIterator[AgentEvent]` — interrupt 재개
  - `pending(session: dict) -> str | None` — 대기 질문 payload(JSON str) 또는 None
  - `session` dict: `{"session_id": str, "bucket": str, "region": str, "prefix": str}` — Task 4의 app.py가 요청 본문에서 그대로 전달.
- `agent_factory(session, emit) -> agent`: 테스트 주입 시임. 실물은 `_default_agent_factory`가 S3/FileSessionManager + BedrockModel + build_tools로 Agent를 만든다.

- [ ] **Step 1: 실패 테스트 작성** — `harness/tests/test_strands_driver.py`:

```python
import json
import pytest
from strands_driver import StrandsDriver, _questions_event_from_interrupts


class FakeInterrupt:
    def __init__(self, id="i-1", reason=None):
        self.id = id
        self.name = "ask_questions"
        self.reason = reason or {"questions_payload": {"name": "q", "questions": []}}


class FakeResult:
    def __init__(self, stop_reason="end_turn", interrupts=None):
        self.stop_reason = stop_reason
        self.interrupts = interrupts


class FakeAgent:
    """Duck-typed strands Agent: stream_async yields scripted event dicts.
    The last event carries {"result": FakeResult}."""
    def __init__(self, script):
        self._script = script
        self.calls = []

    async def stream_async(self, prompt):
        self.calls.append(prompt)
        for ev in self._script:
            yield ev


def make_driver(script, emitted_during_tools=()):
    def factory(session, emit):
        for ev in emitted_during_tools:
            pass  # tools emit via `emit` at runtime; tests emit inline via script
        return FakeAgent(script)
    return StrandsDriver(workspace="/workspace", agent_factory=factory)


async def collect(aiter):
    return [e async for e in aiter]


SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-1", "prefix": "sessions"}


@pytest.mark.asyncio
async def test_text_deltas_become_message_events_and_done():
    drv = make_driver([{"data": "안녕"}, {"data": "하세요"},
                       {"result": FakeResult("end_turn")}])
    evs = await collect(drv.run("hi", SESSION))
    assert [(e.kind, e.text) for e in evs[:2]] == [("message", "안녕"), ("message", "하세요")]
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_interrupt_result_yields_questions_then_done():
    payload = {"name": "pain-point-questions", "questions": []}
    drv = make_driver([{"data": "질문 준비"},
                       {"result": FakeResult("interrupt", [FakeInterrupt("i-9", {"questions_payload": payload})])}])
    evs = await collect(drv.run("시작", SESSION))
    q = next(e for e in evs if e.kind == "questions")
    body = json.loads(q.payload)
    assert body["interrupt_id"] == "i-9"
    assert body["questions"] == payload
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_run_answers_resumes_with_interrupt_response():
    drv = make_driver([{"data": "반영"}, {"result": FakeResult("end_turn")}])
    evs = await collect(drv.run_answers("i-9", {"1": "A"}, SESSION))
    agent = drv._agents[SESSION["session_id"]]
    resume_prompt = agent.calls[0]
    assert resume_prompt == [{"interruptResponse": {"interruptId": "i-9", "response": {"1": "A"}}}]
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_agent_cached_per_session_id():
    drv = make_driver([{"result": FakeResult("end_turn")}])
    await collect(drv.run("a", SESSION))
    first = drv._agents["p1"]
    await collect(drv.run("b", SESSION))
    assert drv._agents["p1"] is first

@pytest.mark.asyncio
async def test_stream_error_yields_error_event():
    class Boom(FakeAgent):
        async def stream_async(self, prompt):
            yield {"data": "x"}
            raise RuntimeError("bedrock down")
    drv = StrandsDriver(workspace="/workspace", agent_factory=lambda s, e: Boom([]))
    evs = await collect(drv.run("hi", SESSION))
    assert evs[-1].kind == "error"
    assert "bedrock down" not in (evs[-1].text or "")  # no raw internals to the user
```

- [ ] **Step 2: 실패 확인**

Run: `cd harness && .venv/bin/python -m pytest tests/test_strands_driver.py -q`
Expected: FAIL — `ModuleNotFoundError: strands_driver`

- [ ] **Step 3: 구현** — `harness/strands_driver.py`:

```python
# harness/strands_driver.py — Strands agent loop INSIDE the MicroVM.
# Replaces claude_driver's subprocess+stream-json with an in-process agent.
# Conversation context persists to S3 via S3SessionManager (spec §2): the VM
# can die and a new one resumes the same session_id, pending interrupt included.
from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from events import AgentEvent
from aiplc_tools import build_tools, QUESTIONS_SCHEMA_HINT

_log = logging.getLogger("harness.strands")

_RULES_DIR = "aiplc-rules/aws-aiplc-rules"
_COMMON_DIR = "aiplc-rules/aws-aiplc-rule-details/common"

_CONTACT_ADDENDUM = f"""
## Pathfinder 통합 규약 (UI 접점 — 반드시 준수)
- 사용자에게 객관식 질문을 할 때는 반드시 ask_questions 도구를 사용한다.
  질문 파일(aiplc-docs/**-questions.md)은 기록용으로 계속 작성하되, 질문
  전달 자체는 도구로만 한다. {QUESTIONS_SCHEMA_HINT}
- 스테이지를 시작/완료할 때마다 report_stage 도구를 호출한다.
- discovery-document를 생성/갱신할 때마다 submit_document 도구를 호출한다.
- 파일 접근은 file_read / file_write 도구만 사용한다 (경로는 워크스페이스 상대).
"""


def _system_prompt(workspace: str) -> str:
    """core-workflow + common rules verbatim (rules stay data — spec §1),
    then the integration addendum. Stage-detail rules are NOT inlined; the
    core workflow instructs the agent to file_read them on demand."""
    ws = Path(workspace)
    parts = [(ws / _RULES_DIR / "core-workflow.md").read_text(encoding="utf-8")]
    common = ws / _COMMON_DIR
    if common.is_dir():
        for f in sorted(common.glob("*.md")):
            parts.append(f"\n\n---\n# RULE DETAIL: common/{f.name}\n" + f.read_text(encoding="utf-8"))
    parts.append(_CONTACT_ADDENDUM)
    return "".join(parts)


def _session_manager(session: dict):
    if session.get("bucket"):
        from strands.session import S3SessionManager
        return S3SessionManager(
            session_id=session["session_id"], bucket=session["bucket"],
            prefix=session.get("prefix", "sessions"),
            region_name=session.get("region") or None)
    # Local/test fallback: file sessions under the workspace (survives within
    # the VM only — fine for tests and the local drill).
    from strands.session import FileSessionManager
    return FileSessionManager(session_id=session["session_id"],
                              storage_dir="/workspace/.sessions")


def _default_agent_factory(workspace: str):
    def factory(session: dict, emit: Callable[[AgentEvent], None]):
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=os.environ["ANTHROPIC_MODEL"])
        return Agent(
            model=model,
            system_prompt=_system_prompt(workspace),
            tools=build_tools(workspace, emit),
            session_manager=_session_manager(session),
            callback_handler=None,   # we consume stream_async, not callbacks
        )
    return factory


def _questions_event_from_interrupts(interrupts) -> AgentEvent | None:
    for itr in interrupts or []:
        reason = getattr(itr, "reason", None) or {}
        if "questions_payload" in reason:
            return AgentEvent(kind="questions", payload=json.dumps(
                {"interrupt_id": itr.id, "questions": reason["questions_payload"]},
                ensure_ascii=False))
    return None


class StrandsDriver:
    def __init__(self, workspace: str,
                 agent_factory: Callable[[dict, Callable], Any] | None = None):
        self._workspace = workspace
        self._factory = agent_factory or _default_agent_factory(workspace)
        self._agents: dict[str, Any] = {}
        self._queues: dict[str, asyncio.Queue] = {}

    def _agent_for(self, session: dict):
        sid = session["session_id"]
        if sid not in self._agents:
            queue: asyncio.Queue = asyncio.Queue()
            # Tools run on the event loop thread during stream_async, so a
            # plain (non-threadsafe) put_nowait is correct here.
            self._agents[sid] = self._factory(session, queue.put_nowait)
            self._queues[sid] = queue
        return self._agents[sid], self._queues[sid]

    async def _stream(self, prompt, session: dict) -> AsyncIterator[AgentEvent]:
        agent, queue = self._agent_for(session)
        result = None
        try:
            async for ev in agent.stream_async(prompt):
                # Drain tool-emitted structured events first (stage/document/
                # file_changed land here mid-stream, in tool-execution order).
                while not queue.empty():
                    yield queue.get_nowait()
                if "data" in ev:
                    yield AgentEvent(kind="message", text=ev["data"])
                elif "current_tool_use" in ev:
                    name = (ev["current_tool_use"] or {}).get("name")
                    if name:
                        yield AgentEvent(kind="status", text=name)
                if "result" in ev:
                    result = ev["result"]
        except Exception:
            _log.exception("strands turn failed")
            while not queue.empty():
                yield queue.get_nowait()
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        while not queue.empty():
            yield queue.get_nowait()
        if result is not None and getattr(result, "stop_reason", None) == "interrupt":
            q_ev = _questions_event_from_interrupts(result.interrupts)
            if q_ev is not None:
                yield q_ev
        yield AgentEvent(kind="done")

    def run(self, text: str, session: dict) -> AsyncIterator[AgentEvent]:
        return self._stream(text, session)

    def run_answers(self, interrupt_id: str, answers: dict[str, str],
                    session: dict) -> AsyncIterator[AgentEvent]:
        prompt = [{"interruptResponse": {"interruptId": interrupt_id,
                                         "response": answers}}]
        return self._stream(prompt, session)

    async def pending(self, session: dict) -> str | None:
        """Pending interrupt after restore. No public accessor exists in the
        SDK (verified v1.48); _interrupt_state is the documented-in-source
        session-persisted field."""
        agent, _ = self._agent_for(session)
        state = getattr(agent, "_interrupt_state", None)
        if state is None or not getattr(state, "activated", False):
            return None
        ev = _questions_event_from_interrupts(list(state.interrupts.values()))
        return ev.payload if ev else None
```

- [ ] **Step 4: 통과 확인**

Run: `cd harness && .venv/bin/python -m pytest tests/test_strands_driver.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: 전체 하네스 스위트 회귀 확인**

Run: `cd harness && .venv/bin/python -m pytest -q`
Expected: PASS (기존 claude_driver/app/hooks 테스트 포함)

- [ ] **Step 6: Commit**

```bash
git add harness/strands_driver.py harness/tests/test_strands_driver.py
git commit -m "feat(harness): StrandsDriver — stream_async translation, interrupt questions, session cache, pending"
```

---

### Task 4: 하네스 HTTP — /answers, /pending, session 본문 + 드라이버 플래그

**Files:**
- Modify: `harness/app.py`, `harness/serve.py`, `harness/hooks.py`
- Test: `harness/tests/test_app.py` (추가), `harness/tests/test_hooks.py` (수정)

**Interfaces:**
- Consumes: `StrandsDriver.run/run_answers/pending` (Task 3).
- Produces (HTTP, 백엔드 Task 6이 호출):
  - `POST /message` body `{"text": str, "session": {...}}` → SSE (기존 body에 session 추가; session 없으면 legacy 경로 = claude_driver 스타일 `run(text, continue_session)` 호출 유지)
  - `POST /answers` body `{"interrupt_id": str, "answers": {str: str}, "session": {...}}` → SSE
  - `POST /pending` body `{"session": {...}}` → JSON `{"pending": <payload str | null>}`
- `build_app(driver, workspace)` 시그니처 유지 — driver가 StrandsDriver면 새 라우트 활성.

- [ ] **Step 1: 실패 테스트 작성** — `harness/tests/test_app.py`에 추가 (기존 스타일: httpx.ASGITransport로 인프로세스 호출):

```python
class ScriptedStrandsDriver:
    """StrandsDriver method surface, no strands import."""
    def __init__(self):
        from events import AgentEvent
        self.answer_calls = []
        self._pending = '{"interrupt_id":"i-1","questions":{"name":"q","questions":[]}}'

    def run(self, text, session):
        from events import AgentEvent
        async def gen():
            yield AgentEvent(kind="message", text=f"seen:{session['session_id']}:{text}")
            yield AgentEvent(kind="done")
        return gen()

    def run_answers(self, interrupt_id, answers, session):
        from events import AgentEvent
        self.answer_calls.append((interrupt_id, answers, session["session_id"]))
        async def gen():
            yield AgentEvent(kind="message", text="반영 완료")
            yield AgentEvent(kind="done")
        return gen()

    async def pending(self, session):
        return self._pending


SESSION = {"session_id": "p1", "bucket": "b", "region": "ap-northeast-1", "prefix": "sessions"}


@pytest.mark.asyncio
async def test_message_with_session_routes_to_strands_run(tmp_path):
    drv = ScriptedStrandsDriver()
    app = build_app(drv, str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        async with c.stream("POST", "/message", json={"text": "hi", "session": SESSION}) as r:
            lines = [l async for l in r.aiter_lines() if l.startswith("data:")]
    assert "seen:p1:hi" in lines[0]

@pytest.mark.asyncio
async def test_answers_endpoint_streams_resume(tmp_path):
    drv = ScriptedStrandsDriver()
    app = build_app(drv, str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        async with c.stream("POST", "/answers", json={
                "interrupt_id": "i-1", "answers": {"1": "A"}, "session": SESSION}) as r:
            lines = [l async for l in r.aiter_lines() if l.startswith("data:")]
    assert drv.answer_calls == [("i-1", {"1": "A"}, "p1")]
    assert "반영 완료" in lines[0]

@pytest.mark.asyncio
async def test_pending_endpoint_returns_payload(tmp_path):
    drv = ScriptedStrandsDriver()
    app = build_app(drv, str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/pending", json={"session": SESSION})
    assert r.json()["pending"].startswith('{"interrupt_id"')

@pytest.mark.asyncio
async def test_message_without_session_keeps_legacy_claude_path(tmp_path):
    """Rollback safety: a body with no `session` must still drive the old
    run(text, continue_session=...) surface (claude_driver)."""
    calls = []
    class LegacyDriver:
        def run(self, text, *, continue_session):
            from events import AgentEvent
            calls.append(continue_session)
            async def gen():
                yield AgentEvent(kind="done")
            return gen()
    app = build_app(LegacyDriver(), str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        async with c.stream("POST", "/message", json={"text": "hi"}) as r:
            [l async for l in r.aiter_lines()]
        async with c.stream("POST", "/message", json={"text": "again"}) as r:
            [l async for l in r.aiter_lines()]
    assert calls == [False, True]
```

- [ ] **Step 2: 실패 확인**

Run: `cd harness && .venv/bin/python -m pytest tests/test_app.py -q`
Expected: FAIL — 404 (라우트 없음) / TypeError (run 시그니처)

- [ ] **Step 3: app.py 구현** — `build_app`의 `message` 핸들러 교체 + 라우트 추가:

```python
    async def message(request):
        body = await request.json()
        text = body["text"]
        session = body.get("session")
        if session is not None:
            gen_src = driver.run(text, session)
        else:
            # Legacy path (claude_driver rollback): per-process continue flag.
            continue_session = state["turn_seen"]
            state["turn_seen"] = True
            gen_src = driver.run(text, continue_session=continue_session)

        async def gen():
            async for ev in gen_src:
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    async def answers(request):
        body = await request.json()
        async def gen():
            async for ev in driver.run_answers(
                    body["interrupt_id"], body["answers"], body["session"]):
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    async def pending(request):
        body = await request.json()
        return JSONResponse({"pending": await driver.pending(body["session"])})
```

라우트 추가:

```python
        Route("/answers", answers, methods=["POST"]),
        Route("/pending", pending, methods=["POST"]),
```

- [ ] **Step 4: serve.py 드라이버 플래그** — main()의 드라이버 생성부 교체:

```python
def make_driver(workspace: str = WORKSPACE):
    """PATHFINDER_DRIVER=claude keeps the legacy subprocess driver (rollback);
    default is the Strands in-process agent."""
    if os.environ.get("PATHFINDER_DRIVER") == "claude":
        from claude_driver import ClaudeDriver
        return ClaudeDriver(workspace=workspace)
    from strands_driver import StrandsDriver
    return StrandsDriver(workspace=workspace)


def main() -> None:
    driver = make_driver()
    ...
```

(파일 상단에 `import os` 추가. `from claude_driver import ClaudeDriver` top-level import는 제거 — 함수 내부로 이동.)

- [ ] **Step 5: hooks.py 진단 교체** — `claude_cli_diagnostic`을 유지하되 새 진단 추가 + serve.py가 선택:

```python
def strands_diagnostic() -> str:
    """Diagnostic only, never a build gate (same policy as claude_cli_diagnostic:
    the first image build 503-looped on a CLI gate; we only log)."""
    try:
        import strands  # noqa: F401
        return f"strands import ok ({getattr(strands, '__version__', '?')})"
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        return f"strands import failed {type(exc).__name__}: {exc}"
```

`serve.py`의 `build_hooks_app(...)` 호출에 `cli_diagnostic=strands_diagnostic if os.environ.get("PATHFINDER_DRIVER") != "claude" else claude_cli_diagnostic` 전달. `harness/tests/test_hooks.py`에 진단 문자열 스모크 1건 추가:

```python
def test_strands_diagnostic_never_raises():
    from hooks import strands_diagnostic
    assert isinstance(strands_diagnostic(), str)
```

- [ ] **Step 6: 전체 통과 확인**

Run: `cd harness && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add harness/app.py harness/serve.py harness/hooks.py harness/tests/test_app.py harness/tests/test_hooks.py
git commit -m "feat(harness): /answers + /pending endpoints, per-request session, PATHFINDER_DRIVER flag"
```

---

### Task 5: MicroVM 이미지 — Dockerfile/requirements + CDK (S3 세션 grant)

**Files:**
- Modify: `harness/Dockerfile`, `harness/requirements.txt`
- Modify: `infra/lib/pathfinder-drill-stack.ts`
- Test: `cd infra && npx cdk synth` (합성 검증)

**Interfaces:**
- Consumes: Task 3/4의 하네스 코드가 이미지 안에서 돈다.
- Produces: exec 롤에 `sessions/*` prefix 한정 S3 권한 → Task 6의 backend가 session 설정에 이 bucket을 넣는다. 이미지 env `PATHFINDER_DRIVER=strands`.

- [ ] **Step 1: requirements.txt에 추가**

```
strands-agents>=1.48,<2
```

- [ ] **Step 2: Dockerfile 수정** — claude-code 제거:

```dockerfile
# 삭제할 라인 2개:
#   RUN dnf install -y python3.11 python3.11-pip nodejs npm shadow-utils && dnf clean all
#   RUN npm install -g @anthropic-ai/claude-code
# 교체:
RUN dnf install -y python3.11 python3.11-pip shadow-utils && dnf clean all
```

(non-root `harness` 유저·워크스페이스 chown 등 나머지는 유지 — Strands는 root 제약이 없지만 최소권한 원칙 유지.)

- [ ] **Step 3: CDK 수정** — `infra/lib/pathfinder-drill-stack.ts`:

(a) exec 롤 주석과 정책 갱신 — "NO S3" 경계를 세션 prefix 예외로 완화:

```typescript
    // Execution role: assumed by the RUNNING VM. Bedrock invoke + S3 access
    // SCOPED TO THE SESSION-STATE PREFIX ONLY (S3SessionManager persistence,
    // spec §2). The artifacts prefix (projects/*) stays unreachable from the
    // VM — the durable-workspace boundary is preserved.
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/sessions/*`],
    }));
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': 'sessions/*' } },
    }));
```

(b) 이미지 env에 드라이버 플래그 추가 (`environmentVariables` 배열):

```typescript
        { key: 'PATHFINDER_DRIVER', value: 'strands' },
```

- [ ] **Step 4: 합성 검증**

Run: `cd infra && npx cdk synth > /dev/null && echo SYNTH_OK`
Expected: `SYNTH_OK` (배포는 실 드릴 Task 12에서)

- [ ] **Step 5: Commit**

```bash
git add harness/Dockerfile harness/requirements.txt infra/lib/pathfinder-drill-stack.ts
git commit -m "feat(image+infra): strands runtime in the harness image; exec-role S3 grant scoped to sessions/*"
```

---

### Task 6: 백엔드 전송 계층 — HarnessClient session/answers/pending

**Files:**
- Modify: `backend/pathfinder/sandbox/harness.py`
- Modify: `backend/tests/fakes/harness_app.py`, `backend/tests/fakes/in_memory_harness.py`
- Test: `backend/tests/test_harness_client.py` (추가)

**Interfaces:**
- Consumes: Task 4의 하네스 HTTP 계약.
- Produces (Task 7의 MicroVMSandbox가 소비):
  - `HarnessClient(base_url, http, headers=None, session: dict | None = None)` — session이 있으면 `/message` body에 포함
  - `send_answers(interrupt_id: str, answers: dict[str, str]) -> AsyncIterator[AgentEvent]`
  - `pending() -> str | None`

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_harness_client.py`에 추가 (기존 fake_harness_app 픽스처 스타일):

```python
SESSION = {"session_id": "p1", "bucket": "b", "region": "ap-northeast-1", "prefix": "sessions"}

@pytest.mark.asyncio
async def test_send_message_includes_session_in_body():
    seen = {}
    app = build_fake_harness_app(capture=seen)   # Step 3에서 capture 파라미터 추가
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://h") as http:
        hc = HarnessClient("http://h", http, session=SESSION)
        [e async for e in hc.send_message("hi")]
    assert seen["message_body"]["session"] == SESSION

@pytest.mark.asyncio
async def test_send_answers_streams_events():
    app = build_fake_harness_app(scripted_events=[
        {"kind": "message", "text": "반영", "path": None, "payload": None},
        {"kind": "done", "text": None, "path": None, "payload": None}])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://h") as http:
        hc = HarnessClient("http://h", http, session=SESSION)
        evs = [e async for e in hc.send_answers("i-1", {"1": "A"})]
    assert [e.kind for e in evs] == ["message", "done"]

@pytest.mark.asyncio
async def test_pending_round_trip():
    app = build_fake_harness_app(pending_payload='{"interrupt_id":"i-1","questions":{}}')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://h") as http:
        hc = HarnessClient("http://h", http, session=SESSION)
        assert (await hc.pending()).startswith('{"interrupt_id"')
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_harness_client.py -q`
Expected: FAIL — TypeError(session kwarg) / capture 미지원

- [ ] **Step 3: 구현**

`backend/pathfinder/sandbox/harness.py` — 생성자에 `session: dict | None = None` 추가(`self._session = session`), `send_message` body를 `{"text": text, **({"session": self._session} if self._session else {})}`로, 그리고 메서드 2개 추가:

```python
    async def send_answers(self, interrupt_id: str,
                           answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        async with self._http.stream(
            "POST", f"{self._base}/answers",
            json={"interrupt_id": interrupt_id, "answers": answers,
                  "session": self._session},
            headers=self._headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = AgentEvent(**json.loads(payload))
                yield event
                if event.kind in _TERMINAL:
                    return

    async def pending(self) -> str | None:
        resp = await self._http.post(
            f"{self._base}/pending", json={"session": self._session},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()["pending"]
```

`backend/tests/fakes/harness_app.py`의 `build_fake_harness_app`에 `capture: dict | None = None, pending_payload: str | None = None` 파라미터 추가 — `message` 핸들러 첫 줄에서 `if capture is not None: capture["message_body"] = body`, 그리고 라우트 추가:

```python
    async def answers(request):
        body = await request.json()
        if capture is not None:
            capture["answers_body"] = body
        events = scripted_events or [
            {"kind": "message", "text": "answers ok", "path": None, "payload": None},
            {"kind": "done", "text": None, "path": None, "payload": None}]
        async def gen():
            for ev in events:
                yield {"data": json.dumps(ev)}
        return EventSourceResponse(gen())

    async def pending(request):
        return JSONResponse({"pending": pending_payload})
```

(Route 등록: `Route("/answers", answers, methods=["POST"])`, `Route("/pending", pending, methods=["POST"])`.)

`backend/tests/fakes/in_memory_harness.py`의 FakeHarness에도 동일 표면 추가:

```python
    def __init__(self, events_for=None, answers_events=None, pending_payload=None):
        ...  # 기존 유지
        self._answers_events = answers_events or (lambda i, a: [
            AgentEvent(kind="message", text="answers ok"), AgentEvent(kind="done")])
        self.pending_payload = pending_payload
        self.answer_calls: list[tuple[str, dict]] = []

    async def send_answers(self, interrupt_id, answers):
        self.answer_calls.append((interrupt_id, answers))
        for ev in self._answers_events(interrupt_id, answers):
            yield ev

    async def pending(self):
        return self.pending_payload
```

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/harness.py backend/tests/fakes/harness_app.py \
        backend/tests/fakes/in_memory_harness.py backend/tests/test_harness_client.py
git commit -m "feat(backend): HarnessClient session body + send_answers/pending transport"
```

---

### Task 7: MicroVMSandbox — send_answers/pending 실구현 + session 조립

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm.py` (Task 1의 스텁 교체)
- Modify: `backend/pathfinder/app.py` (`_make_microvm_sandbox`에 session 조립)
- Test: `backend/tests/test_microvm_sandbox.py` (추가), `backend/tests/test_make_sandbox.py` (추가)

**Interfaces:**
- Consumes: `HarnessClient.send_answers/pending` (Task 6), FakeHarness 확장 표면.
- Produces: `MicroVMSandbox.send_answers(answers)` — **주의: interrupt_id는 sandbox가 보관**한다. `send_message`/`send_answers` 스트림에서 `questions` 이벤트를 관찰하면 payload의 `interrupt_id`를 `self._pending_interrupt_id`에 저장; `send_answers`는 그 저장값으로 하네스를 호출한다(라우트/프론트는 interrupt_id를 몰라도 된다). `pending()`은 부팅 없이 라이브 VM이 있을 때만 하네스에 질의(없으면 None — 파일 ops와 같은 "never boot" 원칙).

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_microvm_sandbox.py`에 추가(기존 FakeMicroVMController/FakeS3Store/FakeHarness 픽스처 스타일 준수):

```python
import json
import pytest
from pathfinder.sandbox.base import AgentEvent
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController, BootSpec
from tests.fakes.in_memory_harness import FakeHarness
from tests.fakes.in_memory_s3 import FakeS3Store

Q_PAYLOAD = json.dumps({"interrupt_id": "i-7", "questions": {"name": "q", "questions": []}})

def _sandbox(harness):
    ctrl = FakeMicroVMController(base_url="http://fake")
    return MicroVMSandbox(project_id="p1", controller=ctrl, spec=BootSpec(),
                          harness_factory=lambda h: harness, s3=FakeS3Store())

@pytest.mark.asyncio
async def test_questions_event_records_interrupt_id_and_answers_resume():
    harness = FakeHarness(events_for=lambda t: [
        AgentEvent(kind="questions", payload=Q_PAYLOAD), AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("시작")]
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert harness.answer_calls == [("i-7", {"1": "A"})]
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_send_answers_without_pending_interrupt_errors():
    sb = _sandbox(FakeHarness())
    await sb.start()
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert evs[0].kind == "error"

@pytest.mark.asyncio
async def test_send_answers_syncs_workspace_on_done():
    harness = FakeHarness(
        events_for=lambda t: [AgentEvent(kind="questions", payload=Q_PAYLOAD),
                              AgentEvent(kind="done")],
        answers_events=lambda i, a: [AgentEvent(kind="done")])
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("시작")]
    harness.files["aiplc-docs/audit.md"] = "# audit"   # written "during" the resumed turn
    [e async for e in sb.send_answers({"1": "A"})]
    assert "aiplc-docs/audit.md" in sb._s3.blobs  # post-turn sync ran

@pytest.mark.asyncio
async def test_pending_returns_none_when_no_live_vm():
    sb = _sandbox(FakeHarness(pending_payload=Q_PAYLOAD))
    await sb.start()
    assert await sb.pending() is None  # never boots just to ask

@pytest.mark.asyncio
async def test_pending_queries_live_harness():
    harness = FakeHarness(events_for=lambda t: [AgentEvent(kind="done")],
                          pending_payload=Q_PAYLOAD)
    sb = _sandbox(harness)
    await sb.start()
    [e async for e in sb.send_message("부팅 유발")]
    assert await sb.pending() == Q_PAYLOAD
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_microvm_sandbox.py -q`
Expected: FAIL — Task 1 스텁("not implemented" error 이벤트)

- [ ] **Step 3: 구현** — `backend/pathfinder/sandbox/microvm.py`의 Task 1 스텁을 교체. `__init__`에 `self._pending_interrupt_id: str | None = None` 추가. `send_message`의 이벤트 루프에서 questions 관찰(기존 I1 sync 로직 유지):

```python
            async for event in harness.send_message(text):
                if event.kind == "questions" and event.payload:
                    # The sandbox owns the interrupt id: routes/frontend send
                    # only answers; we resume the interrupt they belong to.
                    self._pending_interrupt_id = json.loads(event.payload)["interrupt_id"]
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3(harness)
                yield event
```

send_answers/pending 실구현:

```python
    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
            async for event in harness.send_answers(interrupt_id, answers):
                if event.kind == "questions" and event.payload:
                    # A resumed turn can raise the NEXT question set.
                    self._pending_interrupt_id = json.loads(event.payload)["interrupt_id"]
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3(harness)
                yield event
        finally:
            self._turn_active = False

    async def pending(self) -> str | None:
        # File-ops principle applies: never boot a VM just to ask. A live
        # harness exists only after a turn has booted one.
        if self._harness is None:
            return None
        payload = await self._harness.pending()
        if payload:
            self._pending_interrupt_id = json.loads(payload)["interrupt_id"]
        return payload
```

(파일 상단 `import json` 추가.)

- [ ] **Step 4: app.py session 조립** — `_make_microvm_sandbox`의 harness_factory에 session 전달. `_build_harness_for_test` 시그니처에 `session: dict | None = None` 추가 후:

```python
    session = {
        "session_id": project_id,
        "bucket": os.environ.get("PATHFINDER_S3_BUCKET", ""),
        "region": os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"),
        "prefix": "sessions",
    }
    def harness_factory(handle: VMHandle) -> HarnessClient:
        return _build_harness_for_test(handle, shared_http, region, session=session)
```

`backend/tests/test_make_sandbox.py`에 확인 테스트 추가:

```python
def test_microvm_harness_carries_session(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "bkt")
    monkeypatch.setenv("PATHFINDER_S3_REGION", "ap-northeast-2")
    from pathfinder.app import _build_harness_for_test
    from pathfinder.sandbox.microvm_control import VMHandle
    import httpx
    hc = _build_harness_for_test(
        VMHandle(vm_id="fake-x", base_url="http://h", status="ready"),
        httpx.AsyncClient(), "ap-northeast-1",
        session={"session_id": "p9", "bucket": "bkt",
                 "region": "ap-northeast-2", "prefix": "sessions"})
    assert hc._session["session_id"] == "p9"
```

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/sandbox/microvm.py backend/pathfinder/app.py \
        backend/tests/test_microvm_sandbox.py backend/tests/test_make_sandbox.py
git commit -m "feat(backend): MicroVMSandbox answers/pending — sandbox-owned interrupt id, turn guard, post-turn sync"
```

---

### Task 8: LocalSandbox 구조화 시나리오 — AWS 없이 UI 개발 가능하게

**Files:**
- Modify: `backend/pathfinder/sandbox/local.py` (Task 1 스텁 교체)
- Test: `backend/tests/test_local_sandbox.py` (추가)

**Interfaces:**
- Consumes: AgentEvent 확장 (Task 1).
- Produces: LocalSandbox 기본 스크립트가 구조화 이벤트 데모 턴을 낸다 — 첫 send_message는 `stage(in_progress)` + `questions`(질문 2개, interrupt_id `local-i-1`), `send_answers`는 `message`(답변 요약) + `stage(completed)` + `document` + `done`. `pending()`은 마지막 미답변 questions payload 반환. 프론트 e2e/수동 개발이 이 시나리오로 3분할 화면 전체를 구동한다.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_local_sandbox.py`에 추가:

```python
import json
import pytest
from pathlib import Path
import tempfile
from pathfinder.sandbox.local import LocalSandbox

@pytest.mark.asyncio
async def test_default_script_first_turn_emits_stage_and_questions():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    evs = [e async for e in sb.send_message("시작")]
    kinds = [e.kind for e in evs]
    assert "stage" in kinds and "questions" in kinds and kinds[-1] == "done"
    q = next(e for e in evs if e.kind == "questions")
    body = json.loads(q.payload)
    assert body["interrupt_id"] == "local-i-1"
    assert len(body["questions"]["questions"]) == 2
    # questions pending until answered
    assert await sb.pending() == q.payload

@pytest.mark.asyncio
async def test_answers_complete_stage_and_emit_document():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    [e async for e in sb.send_message("시작")]
    evs = [e async for e in sb.send_answers({"1": "A", "2": "B"})]
    kinds = [e.kind for e in evs]
    assert "message" in kinds and "stage" in kinds and "document" in kinds
    assert kinds[-1] == "done"
    assert await sb.pending() is None

@pytest.mark.asyncio
async def test_answers_without_pending_errors():
    sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
    await sb.start()
    evs = [e async for e in sb.send_answers({"1": "A"})]
    assert evs[0].kind == "error"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_local_sandbox.py -q`
Expected: FAIL — 스텁이 done/None만 반환

- [ ] **Step 3: 구현** — `backend/pathfinder/sandbox/local.py`. 기존 `AgentScript` 시그니처/`_default_script`는 유지(회귀 보호)하되 구조화 데모를 기본으로 승격:

```python
import json

_DEMO_QUESTIONS = {
    "name": "pain-point-questions",
    "preamble": "데모 시나리오입니다 — 실제 방법론 질문은 microvm 모드에서 생성됩니다.",
    "questions": [
        {"number": 1, "category": "고객", "text": "주요 사용자는 누구인가요?", "answer": None,
         "options": [
             {"letter": "A", "text": "사내 PM", "is_other": False, "recommended": True},
             {"letter": "B", "text": "외부 고객", "is_other": False, "recommended": False},
             {"letter": "X", "text": "Other", "is_other": True, "recommended": False}]},
        {"number": 2, "category": "문제", "text": "가장 큰 페인포인트는?", "answer": None,
         "options": [
             {"letter": "A", "text": "도구 접근성", "is_other": False, "recommended": True},
             {"letter": "B", "text": "속도", "is_other": False, "recommended": False},
             {"letter": "X", "text": "Other", "is_other": True, "recommended": False}]}],
}


def _structured_first_turn(text: str, sb: "LocalSandbox") -> list[AgentEvent]:
    payload = json.dumps({"interrupt_id": "local-i-1", "questions": _DEMO_QUESTIONS},
                         ensure_ascii=False)
    return [
        AgentEvent(kind="message", text=f"'{text}' 요청을 받았습니다. 질문을 준비합니다."),
        AgentEvent(kind="stage", payload=json.dumps(
            {"stage": "Envision", "status": "in_progress", "summary": "질문 생성"},
            ensure_ascii=False)),
        AgentEvent(kind="questions", payload=payload),
        AgentEvent(kind="done"),
    ]
```

LocalSandbox 본체 — `__init__`에 `self._pending_payload: str | None = None`, 기본 스크립트를 `_structured_first_turn`으로, 그리고:

```python
    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for event in self._script(text, self):
            if event.kind == "questions":
                self._pending_payload = event.payload
            yield event

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._pending_payload is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._pending_payload = None
        summary = ", ".join(f"{k}={v}" for k, v in sorted(answers.items()))
        for event in [
            AgentEvent(kind="message", text=f"답변({summary})을 반영했습니다."),
            AgentEvent(kind="stage", payload=json.dumps(
                {"stage": "Envision", "status": "completed", "summary": "답변 반영"},
                ensure_ascii=False)),
            AgentEvent(kind="document", payload=json.dumps(
                {"path": "aiplc-docs/discovery/discovery-document.md",
                 "version": "v1", "summary": "초안 생성"}, ensure_ascii=False)),
            AgentEvent(kind="done"),
        ]:
            yield event

    async def pending(self) -> str | None:
        return self._pending_payload
```

**주의**: 커스텀 `script`를 주입하는 기존 테스트(골든 패스 등)는 `send_message`만 쓰므로 무변경 통과해야 한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (test_golden_path_replay 포함)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/local.py backend/tests/test_local_sandbox.py
git commit -m "feat(backend): LocalSandbox structured demo scenario — questions/stage/document without AWS"
```

---

### Task 9: 백엔드 라우트 — answers 스트림 + pending 조회

**Files:**
- Modify: `backend/pathfinder/routes/turns.py`
- Test: `backend/tests/test_routes_turns.py` (추가)

**Interfaces:**
- Consumes: `Sandbox.send_answers/pending` (Tasks 7/8).
- Produces (프론트 Task 10이 호출):
  - `GET /projects/{pid}/answers/stream?answers=<url-encoded JSON>` → SSE (EventSource는 GET만 가능 — 기존 `GET /events?text=` 패턴과 동일)
  - `GET /projects/{pid}/pending` → `{"pending": str | null}`
- 기존 `_redacted` 레다크션을 questions/stage/document payload에도 적용한다.

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_routes_turns.py`에 추가(기존 파일의 TestClient/monkeypatch make_sandbox 스타일):

```python
def test_answers_stream_relays_events(monkeypatch, client_with_local_project):
    # 픽스처가 없다면: 기존 테스트 파일의 프로젝트 생성 헬퍼 패턴을 따라
    # LocalSandbox 기본 시나리오 프로젝트 "p1"을 만든 뒤 진행.
    client = client_with_local_project
    # arm the pending interrupt
    with client.stream("GET", "/projects/p1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    import json as _json
    answers = _json.dumps({"1": "A", "2": "B"})
    with client.stream("GET", "/projects/p1/answers/stream",
                       params={"answers": answers}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    kinds = [_json.loads(l[5:].strip())["kind"] for l in lines]
    assert "document" in kinds and kinds[-1] == "done"

def test_pending_endpoint(client_with_local_project):
    client = client_with_local_project
    assert client.get("/projects/p1/pending").json() == {"pending": None}
    with client.stream("GET", "/projects/p1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    body = client.get("/projects/p1/pending").json()
    assert body["pending"] is not None

def test_answers_stream_bad_json_400(client_with_local_project):
    r = client_with_local_project.get("/projects/p1/answers/stream",
                                      params={"answers": "not-json"})
    assert r.status_code == 400

def test_payload_is_redacted(monkeypatch, client_factory):
    """questions payload with a credential-looking string is redacted at the
    route seam, same as text."""
    # client_factory: 기존 파일의 make_sandbox monkeypatch 헬퍼로, 커스텀
    # script를 가진 LocalSandbox 프로젝트를 만든다.
    from pathfinder.sandbox.base import AgentEvent
    leak = '{"interrupt_id":"i","questions":{"note":"aws_secret_access_key=AKIAXXXXYYYY"}}'
    def script(text, sb):
        return [AgentEvent(kind="questions", payload=leak), AgentEvent(kind="done")]
    client = client_factory(script)
    with client.stream("GET", "/projects/px/events", params={"text": "hi"}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    assert "AKIAXXXXYYYY" not in "".join(lines)
```

(픽스처 이름이 기존 파일과 다르면 기존 헬퍼에 맞춰 조정하되 4개 시나리오는 유지.)

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_turns.py -q`
Expected: FAIL — 404

- [ ] **Step 3: 구현** — `backend/pathfinder/routes/turns.py`:

`_redacted`를 payload까지 확장:

```python
def _redacted(event: AgentEvent) -> AgentEvent:
    """Copy with credential-bearing content redacted. text AND payload are
    agent-authored; kind/path stay structural."""
    updates = {}
    if event.text is not None:
        updates["text"] = redact_credentials(event.text)
    if event.payload is not None:
        updates["payload"] = redact_credentials(event.payload)
    return event.model_copy(update=updates) if updates else event
```

라우트 추가:

```python
import json
from fastapi import HTTPException

@router.get("/projects/{pid}/answers/stream")
async def stream_answers(pid: str, answers: str):
    ws = get_workspace(pid)
    try:
        parsed = json.loads(answers)
        assert isinstance(parsed, dict)
    except (json.JSONDecodeError, AssertionError):
        raise HTTPException(status_code=400, detail="answers must be a JSON object")
    async def gen():
        async for event in ws.sandbox.send_answers(parsed):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())

@router.get("/projects/{pid}/pending")
async def get_pending(pid: str):
    ws = get_workspace(pid)
    payload = await ws.sandbox.pending()
    if payload is not None:
        payload = redact_credentials(payload)
    return {"pending": payload}
```

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/routes/turns.py backend/tests/test_routes_turns.py
git commit -m "feat(backend): answers SSE stream + pending endpoint with payload redaction"
```

---

### Task 10: 프론트 데이터 계층 — 타입/클라이언트/워크스페이스 훅

**Files:**
- Modify: `frontend/lib/api/types.ts`, `frontend/lib/api/sse.ts`, `frontend/lib/api/client.ts`
- Create: `frontend/lib/useWorkspaceStream.ts`
- Test: `frontend/lib/useWorkspaceStream.test.tsx`, `frontend/lib/api/sse.test.ts` (추가)

**Interfaces:**
- Consumes: Task 9의 `GET /answers/stream`, `GET /pending`; Task 1의 AgentEvent 확장.
- Produces (Task 11의 화면이 소비):

```typescript
// types.ts 추가분
export type AgentEventKind =
  | "message" | "questions" | "stage" | "document"
  | "file_changed" | "status" | "done" | "error";
export interface AgentEvent {
  kind: AgentEventKind;
  text: string | null;
  path: string | null;
  payload: string | null;
}
export interface QuestionsPayload { interrupt_id: string; questions: QuestionFile; }
export interface StagePayload { stage: string; status: StageStatus; summary: string; }
export interface DocumentPayload { path: string; version: string; summary: string; }

// useWorkspaceStream.ts
export interface WorkspaceStream {
  items: ChatItem[];              // useTurnStream과 동일한 채팅 아이템
  streaming: boolean;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => void;
  pendingQuestions: QuestionsPayload | null;   // 우측 패널 질문 폼 소스
  stages: StagePayload[];                      // 좌측 사이드바 소스 (최신이 뒤)
  lastDocument: DocumentPayload | null;        // 문서 리뷰 배지/알림 소스
  changedPaths: string[];                      // 우측 패널 산출물 목록 소스
}
export function useWorkspaceStream(projectId: string): WorkspaceStream;
```

- [ ] **Step 1: 실패 테스트 작성** — `frontend/lib/useWorkspaceStream.test.tsx` (기존 useTurnStream.test.tsx의 streamEvents mock 패턴을 따른다 — `vi.mock("@/lib/api/sse")`):

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspaceStream } from "./useWorkspaceStream";
import * as sse from "@/lib/api/sse";
import * as client from "@/lib/api/client";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/sse");
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig()), getPending: vi.fn().mockResolvedValue(null),
}));

const QUESTIONS_PAYLOAD = JSON.stringify({
  interrupt_id: "i-1",
  questions: { name: "q", preamble: null, parse_ok: true, raw_markdown: null,
    questions: [{ number: 1, category: null, text: "누구?", answer: null,
      options: [{ letter: "A", text: "PM", is_other: false, recommended: true }] }] },
});

function drive(events: AgentEvent[], impl: "streamEvents" | "streamAnswers") {
  vi.mocked(sse[impl]).mockImplementation((_pid, _arg, handlers) => {
    for (const ev of events) handlers.onEvent(ev);
    handlers.onDone();
    return () => {};
  });
}

describe("useWorkspaceStream", () => {
  beforeEach(() => vi.clearAllMocks());

  it("questions event fills pendingQuestions; stage event appends stages", () => {
    drive([
      { kind: "message", text: "준비", path: null, payload: null },
      { kind: "stage", text: null, path: null,
        payload: JSON.stringify({ stage: "Envision", status: "in_progress", summary: "" }) },
      { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
      { kind: "done", text: null, path: null, payload: null },
    ], "streamEvents");
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
    expect(result.current.stages).toEqual([
      { stage: "Envision", status: "in_progress", summary: "" }]);
  });

  it("submitAnswers streams via streamAnswers and clears pendingQuestions", () => {
    drive([{ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
           { kind: "done", text: null, path: null, payload: null }], "streamEvents");
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("시작"));
    drive([
      { kind: "document", text: null, path: null,
        payload: JSON.stringify({ path: "d.md", version: "v1", summary: "" }) },
      { kind: "done", text: null, path: null, payload: null },
    ], "streamAnswers");
    act(() => result.current.submitAnswers({ "1": "A" }));
    expect(vi.mocked(sse.streamAnswers).mock.calls[0][1]).toEqual({ "1": "A" });
    expect(result.current.pendingQuestions).toBeNull();
    expect(result.current.lastDocument?.version).toBe("v1");
  });

  it("malformed payload does not crash the stream (fallback: chat keeps going)", () => {
    drive([{ kind: "questions", text: null, path: null, payload: "not-json{" },
           { kind: "message", text: "계속", path: null, payload: null },
           { kind: "done", text: null, path: null, payload: null }], "streamEvents");
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions).toBeNull();
    expect(result.current.items.some((i) => i.role === "ai" && i.text.includes("계속"))).toBe(true);
  });

  it("restores pending questions from GET /pending on mount", async () => {
    vi.mocked(client.getPending).mockResolvedValue(QUESTIONS_PAYLOAD);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});  // flush the mount effect
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
  });
});
```

`frontend/lib/api/sse.test.ts`에 streamAnswers 스모크 추가:

```typescript
it("streamAnswers opens the answers/stream URL with encoded JSON", () => {
  const es = mockEventSource();  // 기존 sse.test.ts의 EventSource mock 헬퍼 재사용
  streamAnswers("p1", { "1": "A" }, { onEvent: () => {}, onDone: () => {} });
  expect(es.lastUrl).toContain("/projects/p1/answers/stream?answers=");
  expect(decodeURIComponent(es.lastUrl)).toContain('{"1":"A"}');
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npm test -- --run lib/useWorkspaceStream lib/api/sse`
Expected: FAIL — 모듈/함수 없음

- [ ] **Step 3: 구현**

`types.ts`: 위 Interfaces 블록대로 kind/payload 확장 + 3개 payload 인터페이스 추가.

`sse.ts`: streamEvents의 URL만 다른 쌍둥이 추가 (공통 골격 추출):

```typescript
function openStream(url: string, handlers: StreamHandlers): () => void {
  // 기존 streamEvents 본문의 EventSource 로직을 이 함수로 이동
}
export function streamEvents(pid: string, text: string, handlers: StreamHandlers): () => void {
  return openStream(
    `${API_BASE_URL}/projects/${encodeURIComponent(pid)}/events?text=${encodeURIComponent(text)}`,
    handlers);
}
export function streamAnswers(pid: string, answers: Record<string, string>,
                              handlers: StreamHandlers): () => void {
  return openStream(
    `${API_BASE_URL}/projects/${encodeURIComponent(pid)}/answers/stream?answers=${encodeURIComponent(JSON.stringify(answers))}`,
    handlers);
}
```

`client.ts`에 추가:

```typescript
export async function getPending(pid: string): Promise<string | null> {
  const r = await request<{ pending: string | null }>(
    `/projects/${encodeURIComponent(pid)}/pending`);
  return r.pending;
}
```

`useWorkspaceStream.ts`: useTurnStream을 복제·확장한 신규 훅(useTurnStream은 다른 화면이 아직 쓰므로 수정하지 않는다). 핵심 로직:

```typescript
// frontend/lib/useWorkspaceStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamEvents, streamAnswers } from "@/lib/api/sse";
import { getPending } from "@/lib/api/client";
import type { AgentEvent, QuestionsPayload, StagePayload, DocumentPayload } from "@/lib/api/types";
import type { ChatItem, AiItem } from "@/lib/useTurnStream";

function safeParse<T>(payload: string | null): T | null {
  if (!payload) return null;
  try { return JSON.parse(payload) as T; } catch { return null; }
}
```

훅 본체는 useTurnStream의 send 골격을 따르되: (a) onEvent에서 `questions`→`setPendingQuestions(safeParse(...))`, `stage`→`setStages(prev => [...prev, parsed])`, `document`→`setLastDocument(parsed)`, `file_changed`→`changedPaths` 누적 + 기존 trace 처리, (b) `submitAnswers(answers)`는 `setPendingQuestions(null)` 후 streamAnswers로 동일한 AI 버블 스트림 구동(채팅에 `답변 제출` user 아이템 추가), (c) mount 시 `getPending(projectId)`로 복원. malformed payload는 `safeParse`가 null을 반환해 해당 이벤트만 무시된다(스펙 §4 폴백 원칙 — 진행이 막히지 않는다).

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd frontend && npm test`
Expected: PASS (types.test.ts 등 기존 테스트 포함 — AgentEventKind 확장으로 깨지는 단언이 있으면 새 kind를 포함하도록 갱신)

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/types.ts frontend/lib/api/sse.ts frontend/lib/api/client.ts \
        frontend/lib/useWorkspaceStream.ts frontend/lib/useWorkspaceStream.test.tsx \
        frontend/lib/api/sse.test.ts
git commit -m "feat(frontend): workspace stream hook — structured events, answers submit, pending restore"
```

---

### Task 11: 워크스페이스 화면 — 3분할 그리드 + 라우트 정리

**Files:**
- Create: `frontend/app/projects/[projectId]/workspace/page.tsx`, `frontend/components/workspace/WorkspaceRightPanel.tsx`, `frontend/components/workspace/StageSidebar.tsx`
- Modify: `frontend/components/AppHeader.tsx`, `frontend/app/projects/[projectId]/questions/page.tsx`, `frontend/app/projects/[projectId]/canvas/page.tsx`
- Test: `frontend/app/projects/[projectId]/workspace/page.test.tsx`, `frontend/components/workspace/WorkspaceRightPanel.test.tsx`, `frontend/components/AppHeader.test.tsx` (수정)

**Interfaces:**
- Consumes: `useWorkspaceStream` (Task 10), 재사용 컴포넌트 — `ChatTimeline`/`ChatInput`(canvas), `QuestionForm`(questions), `PreviewPanelBody`(canvas), `CanvasSidebar`의 StageRow 시각 패턴.
- Produces: 라우트 `/projects/[id]/workspace`; `/questions`·`/canvas`는 redirect. AppHeader 탭 3개: 대시보드 | 워크스페이스 | 문서 리뷰.

- [ ] **Step 1: 실패 테스트 작성**

`frontend/components/workspace/WorkspaceRightPanel.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceRightPanel } from "./WorkspaceRightPanel";

const QP = { interrupt_id: "i-1", questions: {
  name: "q", preamble: null, parse_ok: true, raw_markdown: null,
  questions: [{ number: 1, category: null, text: "누구?", answer: null,
    options: [{ letter: "A", text: "PM", is_other: false, recommended: true }] }] } };

describe("WorkspaceRightPanel mode switching", () => {
  it("renders QuestionForm when pendingQuestions is set", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={QP}
      stages={[]} changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByText("누구?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /답변 제출/ })).toBeInTheDocument();
  });

  it("renders preview when the prototype stage is active and no questions pend", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={null}
      stages={[{ stage: "Prototype & Validation", status: "in_progress", summary: "" }]}
      changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByLabelText("프로토타입 프리뷰")).toBeInTheDocument();
  });

  it("renders recent artifacts otherwise", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={null} stages={[]}
      changedPaths={["aiplc-docs/audit.md"]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByText("aiplc-docs/audit.md")).toBeInTheDocument();
  });
});
```

`frontend/app/projects/[projectId]/workspace/page.test.tsx` (기존 canvas/page.test.tsx의 mock 패턴 — useWorkspaceStream/getState mock):

```typescript
it("renders the three-pane grid: stage sidebar, chat, context panel", async () => {
  // mock useWorkspaceStream → 고정 상태, getState → 스테이지 fixture
  render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
  expect(await screen.findByLabelText("스테이지 진행 상황")).toBeInTheDocument();
  expect(screen.getByLabelText("대화 타임라인")).toBeInTheDocument();
  expect(screen.getByLabelText("컨텍스트 패널")).toBeInTheDocument();
});
```

`AppHeader.test.tsx` 수정: 탭 기대값을 `대시보드/워크스페이스/문서 리뷰`로, `질문 답변`·`빌드 캔버스` 부재 단언 추가.

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npm test -- --run components/workspace app/projects components/AppHeader`
Expected: FAIL

- [ ] **Step 3: 구현**

`StageSidebar.tsx` — CanvasSidebar의 StageRow 시각 패턴을 재사용하되 데이터 소스가 `StagePayload[]`(이벤트 누적, 같은 stage명은 최신 status가 이김) + 초기값은 `getState()`(aiplc-state.md 파싱 결과 — 이벤트 도착 전 폴백):

```typescript
// stages 이벤트를 StageState[]로 병합: 이벤트가 없으면 서버 state 그대로,
// 이벤트가 있으면 stage명 매칭으로 status/summary(→note) 덮어쓰기.
export function mergeStages(server: StageState[], events: StagePayload[]): StageState[] {
  const byName = new Map(server.map((s) => [s.name, { ...s }]));
  for (const ev of events) {
    const cur = byName.get(ev.stage) ?? { name: ev.stage, status: "pending", note: null };
    byName.set(ev.stage, { ...cur, status: ev.status, note: ev.summary || cur.note });
  }
  return [...byName.values()];
}
```

`WorkspaceRightPanel.tsx` — 모드 상태 머신 (스펙 §5 우선순위: 질문 > 프리뷰 > 산출물):

```typescript
const PROTOTYPE_STAGES = ["Prototype & Validation", "프로토타입"];
type Mode = "questions" | "preview" | "artifacts";
function deriveMode(pending: QuestionsPayload | null, stages: StagePayload[]): Mode {
  if (pending) return "questions";
  const active = stages.filter((s) => s.status === "in_progress").map((s) => s.stage);
  if (active.some((s) => PROTOTYPE_STAGES.some((p) => s.includes(p)))) return "preview";
  return "artifacts";
}
```

questions 모드는 `QuestionForm file={pendingQuestions.questions} onSubmit={onSubmitAnswers} submitting={busy}` 그대로 재사용(payload 스키마가 QuestionFile 미러이므로 무변환). preview 모드는 `PreviewPanelBody`(aria-label="프로토타입 프리뷰" 래퍼), artifacts 모드는 changedPaths 리스트. 패널 래퍼: `<aside aria-label="컨텍스트 패널" className="hidden lg:flex flex-col ...">`.

`workspace/page.tsx` — 3분할 그리드 **1:4.5:4.5** (Global Constraints의 비율):

```tsx
<div className="flex-1 grid min-h-0 grid-cols-1 lg:grid-cols-[1fr_4.5fr_4.5fr]">
  <StageSidebar ... />                          {/* 좌 */}
  <main className="flex flex-col min-w-0 bg-slate-50">
    <ChatTimeline items={items} projectId={projectId} onChoose={send}
                  onOpenArtifact={() => {}} busy={streaming} />
    <ChatInput onSend={send} disabled={streaming} />
  </main>
  <WorkspaceRightPanel ... />                   {/* 우 */}
</div>
```

모바일(<lg): 사이드바·우측 패널은 `hidden lg:flex`, 채팅 위에 질문 대기 배지 버튼 → 하단 시트(단순 `fixed bottom-0` 오버레이에 WorkspaceRightPanel 내용 재사용).

`questions/page.tsx`·`canvas/page.tsx` 교체:

```tsx
import { redirect } from "next/navigation";
export default async function Page({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  redirect(`/projects/${projectId}/workspace`);
}
```

(해당 page.test.tsx 2개는 redirect 단언으로 교체하거나 삭제 — 컴포넌트 테스트는 workspace로 이동한 것이 대체한다.)

`AppHeader.tsx`: nav를 `대시보드/워크스페이스/문서 리뷰` 3개로, HeaderTab 타입을 `"dashboard" | "workspace" | "review" | "projects"`로 갱신(참조하는 dashboard/review 페이지의 activeTab 값도 함께).

- [ ] **Step 4: 통과 + 전체 회귀 확인**

Run: `cd frontend && npm test`
Expected: PASS. e2e(`canvas.spec.ts`/`wizard.spec.ts`)는 INTEGRATION 마커라 유닛 러너에서 제외 — Task 12에서 워크스페이스 시나리오로 갱신.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/projects frontend/components/workspace frontend/components/AppHeader.tsx \
        frontend/components/AppHeader.test.tsx
git commit -m "feat(frontend): 3-pane workspace (1:4.5:4.5) replaces questions/canvas tabs"
```

---

### Task 12: 골든 패스 이식 + e2e + 실 VM 드릴

**Files:**
- Modify: `backend/tests/test_golden_path_replay.py` (이벤트 계약 버전 추가)
- Modify: `frontend/e2e/wizard.spec.ts`, `frontend/e2e/canvas.spec.ts` → `frontend/e2e/workspace.spec.ts`로 통합
- Create: `docs/superpowers/plans/2026-07-19-strands-drill-checklist.md` (수동 드릴 절차)

**Interfaces:**
- Consumes: 전체 스택 (Tasks 1–11).
- Produces: 회귀 스위트 + 배포 검증 절차. **이 태스크 완료 = 스펙 §7·§8 달성.**

- [ ] **Step 1: 골든 패스 이벤트 버전 작성** — `test_golden_path_replay.py`에 추가 (기존 파일 파싱 버전은 유지 — 파일은 여전히 기록으로 생성되므로 둘 다 유효):

```python
def test_replay_via_answers_stream_advances_stages(monkeypatch):
    """Spec §7: the pilot1 stage sequence driven through the EVENT contract —
    each send_answers round completes one stage via a stage event."""
    from pathfinder.sandbox.base import AgentEvent
    import json as _json
    round_n = {"i": 0}

    def script(text, sb):
        payload = _json.dumps({"interrupt_id": f"i-{round_n['i']}",
                               "questions": {"name": "q", "preamble": None,
                                             "parse_ok": True, "raw_markdown": None,
                                             "questions": []}})
        return [AgentEvent(kind="questions", payload=payload), AgentEvent(kind="done")]

    async def make(project_id):
        from pathfinder.sandbox.local import LocalSandbox
        import tempfile
        from pathlib import Path
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)

        async def send_answers(answers):
            i = round_n["i"] = round_n["i"] + 1
            stage = STAGES[min(i, len(STAGES) - 1)]
            yield AgentEvent(kind="stage", payload=_json.dumps(
                {"stage": stage, "status": "completed", "summary": ""}))
            nxt = _json.dumps({"interrupt_id": f"i-{i}", "questions":
                               {"name": "q", "preamble": None, "parse_ok": True,
                                "raw_markdown": None, "questions": []}})
            yield AgentEvent(kind="questions", payload=nxt)
            yield AgentEvent(kind="done")
        sb.send_answers = send_answers  # scripted structured rounds
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)

    client.post("/projects", json={"project_id": "replay-ev"})
    with client.stream("GET", "/projects/replay-ev/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    completed = []
    for _ in range(len(STAGES) - 1):
        import json as _j
        with client.stream("GET", "/projects/replay-ev/answers/stream",
                           params={"answers": _j.dumps({"1": "A"})}) as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    ev = _j.loads(line[5:].strip())
                    if ev["kind"] == "stage":
                        completed.append(_j.loads(ev["payload"])["stage"])
    assert completed == STAGES[1:]
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_golden_path_replay.py -q` → PASS 후 커밋:

```bash
git add backend/tests/test_golden_path_replay.py
git commit -m "test(backend): golden-path replay over the event contract (answers stream)"
```

- [ ] **Step 2: e2e 통합** — `wizard.spec.ts` + `canvas.spec.ts`를 `workspace.spec.ts`로 교체(로컬 백엔드 = LocalSandbox 데모 시나리오 전제):

시나리오: 프로젝트 생성 → `/workspace` 진입 → 채팅에 "시작" 전송 → 우측 패널에 질문 폼 렌더 확인(데모 질문 2개) → 라디오 선택 → "답변 제출" → 채팅에 답변 반영 메시지 + 좌측 사이드바 Envision completed + 문서 이벤트 확인 → `/questions`·`/canvas` 접속 시 `/workspace`로 redirect 확인.

Run: `cd frontend && npm run test:e2e` (백엔드 기동 필요 — INTEGRATION). PASS 후 커밋:

```bash
git add frontend/e2e
git commit -m "test(e2e): workspace three-pane flow replaces wizard/canvas specs"
```

- [ ] **Step 3: 드릴 체크리스트 작성** — `docs/superpowers/plans/2026-07-19-strands-drill-checklist.md` (수동 절차 문서 — 실 AWS에서 사람이 실행):

```markdown
# Strands 전환 실 VM 드릴 (수동, 도쿄)

전제: Task 5 커밋 반영 후 `./package-harness.sh && npx cdk deploy`.

1. **이미지 빌드 확인**: CfnOutputs 갱신 → CloudWatch `/pathfinder/microvm/harness`에서
   ready hook 로그의 `strands import ok` 확인.
2. **스모크 턴**: microvm 모드 백엔드 기동(README B-2, PATHFINDER_S3_BUCKET는 CDK
   Artifacts 버킷) → 캔버스 아님 **워크스페이스**에서 "AI-PLC를 시작해줘" 전송 →
   welcome message 스트림 + report_stage 이벤트로 좌측 사이드바 갱신 확인.
3. **질문 왕복**: 우측 패널 질문 폼 → 답변 제출 → 다음 스테이지 진행 확인.
   S3 콘솔에서 `sessions/p*/` 오브젝트 생성 확인.
4. **컨텍스트 복구 리허설 (핵심)**: 질문 대기 상태에서 콘솔로 MicroVM terminate →
   같은 프로젝트에서 새 메시지/새로고침 → `GET /pending`이 같은 질문을 복원하고,
   답변 제출이 정상 재개되는지 확인. (S3SessionManager interrupt 복원 검증)
5. **IAM 경계 확인**: VM 롤 자격으로 `aws s3 cp s3://<bucket>/projects/... -` 시도
   → AccessDenied (sessions/*만 허용) 확인.
6. **롤백 리허설**: `PATHFINDER_DRIVER=claude` + Dockerfile claude 라인 복원 재배포로
   구 경로가 살아있는지 1턴 확인 (선택).
7. 완료 후 비용 정리: `npx cdk destroy` 또는 VM terminate.
```

```bash
git add docs/superpowers/plans/2026-07-19-strands-drill-checklist.md
git commit -m "docs: real-VM drill checklist for the strands engine cutover"
```

- [ ] **Step 4: 최종 전체 검증**

Run:
```bash
cd backend && .venv/bin/python -m pytest -q && \
cd ../harness && .venv/bin/python -m pytest -q && \
cd ../frontend && npm test && \
cd ../infra && npx cdk synth > /dev/null && echo ALL_GREEN
```
Expected: `ALL_GREEN`

---

## Self-Review 결과 (작성 시 수행)

- **스펙 커버리지**: §2 아키텍처(T3/T5), §3 에이전트·도구(T2/T3), §4 이벤트 계약·/answers·/pending·레다크션·폴백(T1/T4/T6/T7/T9, 폴백은 T10 safeParse), §5 UI 3분할·비율·redirect·모바일(T11), §6 수명주기·복구(T3 세션·T12 드릴4), §7 테스트·골든패스(각 태스크+T12), §8 순서(태스크 순서가 곧 §8) — 갭 없음. 스펙의 "GET /pending"은 하네스 레벨에서 POST로 확정(사유: 세부사항 2) — 백엔드→프론트는 GET 유지로 스펙 문구와 일치.
- **플레이스홀더**: 없음 (모든 코드 스텝에 실제 코드).
- **타입 일관성 확인**: AgentEvent(kind/text/path/payload) 4곳(backend/harness/frontend/fakes) 동일. `send_answers(answers: dict[str,str])`·`pending() -> str|None` 시그니처 T1 선언 = T6/T7/T8 구현 = T9 소비. `session` dict 키(session_id/bucket/region/prefix) T3 정의 = T4/T6/T7 사용. `QuestionsPayload.interrupt_id` T3 발행 = T7 저장 = T10 파싱.
