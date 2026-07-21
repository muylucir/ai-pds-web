# MicroVM 제거 — Strands 에이전트 백엔드 내장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MicroVM/하네스 인프라를 제거하고 Strands 에이전트를 백엔드 프로세스 안에서 직접 실행하도록 이전한다.

**Architecture:** `harness/`의 에이전트 로직(strands_driver·aiplc_tools)을 `backend/pathfinder/agent/`로 옮기고, `MicroVMSandbox`의 턴 오케스트레이션(S3 restore → in-process 실행 → S3 sync)을 새 `AgentRunner`로 승계한다. `Sandbox` ABC를 해체하고 `Workspace`가 `AgentRunner`를 직접 소유한다. VM 부팅/HTTP 중계/토큰 민팅/local 모드는 전부 삭제한다.

**Tech Stack:** Python 3.11, FastAPI, Strands Agents SDK(`strands-agents>=1.48,<2`), boto3(S3 + Bedrock via `BedrockModel`), pytest/pytest-asyncio, moto[s3].

## Global Constraints

- Python `requires-python = ">=3.11"` — 백엔드/에이전트 모두 3.11.
- boto3 하한은 유지하되 lambda-microvms 사유 주석은 삭제 대상 — Bedrock/S3만 쓰므로 `boto3>=1.43.35` 값 자체는 유지(다운그레이드하지 않음).
- Strands 의존성: `strands-agents>=1.48,<2` — harness/requirements.txt에서 backend/pyproject.toml로 이동.
- 이벤트 계약 불변: `AgentEvent`는 `kind: Literal["message","questions","stage","document","file_changed","status","done","error"]`, `text: str|None`, `path: str|None`, `payload: str|None` — 프론트 SSE 계약이므로 필드/리터럴 변경 금지.
- API 표면(라우트 경로·요청/응답 형태·SSE 프레임) 불변 — 프론트 코드 수정 없음(e2e 단언 조정 제외).
- 턴 직렬화 메시지 문자열 "turn already in progress", pending 없음 "no pending questions", 에이전트 실패 "agent turn failed" — 기존 문자열 그대로 유지(테스트가 부분 매칭).
- S3 레이아웃 불변: 프로젝트 산출물 `projects/{pid}/...`, strands 세션 `sessions/session_{pid}/...`.
- 커밋은 각 Task 끝에서. 커밋 메시지 말미:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**최종 backend/pathfinder/ 구조:**

```
backend/pathfinder/
  agent/
    __init__.py
    driver.py        # StrandsDriver (구 harness/strands_driver.py)
    tools.py         # build_tools + QUESTIONS_SCHEMA_HINT (구 harness/aiplc_tools.py, 룰 라우팅 추가)
  runner.py          # AgentRunner (구 MicroVMSandbox 턴 오케스트레이션 승계)
  workspace.py       # Workspace(runner 직접 소유) + ProjectRegistry (수정)
  s3store.py         # sandbox/에서 승격
  pathsafe.py        # sandbox/에서 승격
  globmatch.py       # sandbox/에서 승격
  models.py          # + AgentEvent, TurnResult (base.py에서 합류)
  app.py             # make_sandbox → make_workspace, VM 배선 제거 (수정)
  project_store.py   # import 경로만 수정
  session_history.py # import 경로만 수정
  routes/*.py        # ws.sandbox → ws.runner (수정)
```

**삭제:**
- `backend/pathfinder/sandbox/` 전체 (base.py, local.py, microvm.py, microvm_control.py, microvm_control_aws.py, harness.py, s3store.py→승격, pathsafe.py→승격, globmatch.py→승격)
- `harness/` 전체
- `infra/`의 MicroVM 이미지·빌드 롤·하네스 asset·로그그룹, `infra/package-harness.sh`
- 대응 테스트: test_microvm_*.py, test_sandbox_*.py, test_local_sandbox.py, test_harness_*.py, test_make_sandbox.py(재작성), test_input_holder.py(재작성), test_app_harness_factory.py, sandbox_contract.py, fakes/harness_app.py, fakes/in_memory_harness.py

**이 계획의 순서 원칙:** 승격/이전을 먼저 하여 새 모듈을 세우고(Task 1–4), 그 위에 `AgentRunner`를 만들고(Task 5), 배선을 갈아끼운 뒤(Task 6–7), 마지막에 죽은 코드·인프라·문서를 정리(Task 8–11)한다. 각 Task 종료 시 `cd backend && .venv/bin/python -m pytest -q`가 (해당 시점 기준) 통과해야 한다.

---

## 참고: 기존 코드에서 승계하는 핵심 로직

`AgentRunner`(Task 5)는 아래 `MicroVMSandbox` 메서드의 로직을 그대로 물려받는다. 구현 시 원본을 재현할 것:

- `_SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")`
- `_RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")`
- audit.md redaction-at-rest: sync 시 `key == "aiplc-docs/audit.md"`면 `redact_credentials(content)`.
- `_interrupt_id_from(payload)`: JSON 파싱 실패/비-str이면 None 강등.
- interrupt id 소유: `questions` 이벤트 관측 시 payload에서 뽑아 `_pending_interrupt_id`에 보관, `send_answers`가 소비.
- 턴 직렬화: `_turn_active` 가드 → "turn already in progress".
- done/error 전 sync 완료(fail-closed).

---

### Task 1: sandbox/ 유틸 3종을 pathfinder/ 루트로 승격

`s3store.py`, `pathsafe.py`, `globmatch.py`를 `backend/pathfinder/sandbox/`에서 `backend/pathfinder/`로 옮기고, 이들을 참조하는 존속 모듈의 import를 갱신한다. 이 세 파일은 VM과 무관한 순수 유틸이라 가장 먼저, 독립적으로 옮길 수 있다.

**Files:**
- Move: `backend/pathfinder/sandbox/s3store.py` → `backend/pathfinder/s3store.py`
- Move: `backend/pathfinder/sandbox/pathsafe.py` → `backend/pathfinder/pathsafe.py`
- Move: `backend/pathfinder/sandbox/globmatch.py` → `backend/pathfinder/globmatch.py`
- Modify: `backend/pathfinder/project_store.py:12`, `backend/pathfinder/session_history.py:14`
- Modify: `backend/tests/test_s3store.py`, `test_s3store_delete.py`, `test_pathsafe.py` (import 경로)
- Modify: `backend/tests/fakes/in_memory_s3.py` (docstring만 — import 없음, 무변경 가능)

**Interfaces:**
- Produces: `pathfinder.s3store.S3Store`, `pathfinder.s3store.S3StoreLike`, `pathfinder.pathsafe.reject_unsafe`, `pathfinder.globmatch.matches_glob` — 시그니처 무변경.

- [ ] **Step 1: git mv로 세 파일 이동 (히스토리 보존)**

```bash
cd /home/ec2-user/project/pathfinder-sp/backend
git mv pathfinder/sandbox/s3store.py pathfinder/s3store.py
git mv pathfinder/sandbox/pathsafe.py pathfinder/pathsafe.py
git mv pathfinder/sandbox/globmatch.py pathfinder/globmatch.py
```

- [ ] **Step 2: 승계 모듈의 s3store import 갱신**

`pathfinder/project_store.py:12` 및 `pathfinder/session_history.py:14`:
```python
# 변경 전: from pathfinder.sandbox.s3store import S3StoreLike
from pathfinder.s3store import S3StoreLike
```

- [ ] **Step 3: 세 파일 상단 경로 주석 및 pathsafe/globmatch의 내부 참조 정리**

`pathfinder/s3store.py`, `pathfinder/pathsafe.py`, `pathfinder/globmatch.py` 맨 위 주석의 경로(`# backend/pathfinder/sandbox/...`)를 `# backend/pathfinder/...`로 수정. `pathsafe.py` docstring의 "Used by MicroVMSandbox" 문구는 Task 5에서 다시 손대므로 지금은 그대로 둔다.

- [ ] **Step 4: 관련 테스트의 import 갱신**

`backend/tests/test_s3store.py`, `test_s3store_delete.py`, `test_pathsafe.py`에서:
```python
# from pathfinder.sandbox.s3store import ... → from pathfinder.s3store import ...
# from pathfinder.sandbox.pathsafe import ... → from pathfinder.pathsafe import ...
```
grep으로 잔여 확인:
```bash
grep -rn "pathfinder.sandbox.s3store\|pathfinder.sandbox.pathsafe\|pathfinder.sandbox.globmatch" pathfinder/ tests/ | grep -v __pycache__
```
Expected: microvm.py/harness.py 등 **삭제 예정** 파일만 남고, 존속 모듈에는 잔여 없음.

- [ ] **Step 5: 승격된 유틸 테스트만 실행**

Run: `cd backend && .venv/bin/python -m pytest tests/test_s3store.py tests/test_s3store_delete.py tests/test_pathsafe.py -q`
Expected: PASS (전량).

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "refactor(backend): promote s3store/pathsafe/globmatch out of sandbox/ package

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: AgentEvent/TurnResult를 models.py로 합류

`backend/pathfinder/sandbox/base.py`의 `AgentEvent`/`TurnResult`를 `models.py`로 옮긴다. `Sandbox` ABC와 `input_holder`는 Task 5/6에서 Workspace로 흡수되므로 여기서는 이벤트 모델만 이동한다. harness/events.py의 동일 정의는 Task 3에서 자연 삭제된다.

**Files:**
- Modify: `backend/pathfinder/models.py` (AgentEvent, TurnResult 추가)
- Modify: `backend/pathfinder/routes/turns.py:8` (import 경로)
- Modify: `backend/pathfinder/sandbox/base.py` (AgentEvent/TurnResult 제거 — Sandbox ABC만 잔존, Task 6에서 파일째 삭제)

**Interfaces:**
- Produces: `pathfinder.models.AgentEvent`, `pathfinder.models.TurnResult` — 필드 무변경(Global Constraints 참조).

- [ ] **Step 1: models.py 하단에 AgentEvent/TurnResult 추가**

`backend/pathfinder/models.py` 맨 아래에:
```python
class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None
    # Structured payload (JSON string) for questions/stage/document — the
    # event IS the UI contract; files stay as records only.
    payload: str | None = None

class TurnResult(BaseModel):
    events: list[AgentEvent]
```
(`Literal`과 `BaseModel`은 models.py 상단에 이미 import 되어 있음.)

- [ ] **Step 2: base.py에서 AgentEvent/TurnResult 삭제하고 재-export로 임시 호환**

`backend/pathfinder/sandbox/base.py` 상단에서 두 클래스 정의를 지우고, 아직 base.py를 import하는 microvm.py/local.py/harness.py가 Task 6까지 깨지지 않도록 재-export를 남긴다:
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncIterator
from pathfinder.models import AgentEvent, TurnResult  # re-export (파일은 Task 6에서 삭제)

class Sandbox(ABC):
    input_holder: str | None = None
    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder
    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def read_file(self, rel_path: str) -> str: ...
    @abstractmethod
    async def write_file(self, rel_path: str, content: str) -> None: ...
    @abstractmethod
    async def list_files(self, glob: str) -> list[str]: ...
    @abstractmethod
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    @abstractmethod
    def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]: ...
    @abstractmethod
    async def pending(self) -> str | None: ...
    @abstractmethod
    async def stop(self) -> None: ...
```

- [ ] **Step 3: turns.py import 갱신**

`backend/pathfinder/routes/turns.py:8`:
```python
from pathfinder.models import AgentEvent, TurnResult
```

- [ ] **Step 4: 이벤트 모델 테스트 추가**

`backend/tests/test_models.py` 하단에:
```python
def test_agent_event_lives_in_models_with_full_kind_literal():
    from pathfinder.models import AgentEvent, TurnResult
    e = AgentEvent(kind="questions", payload='{"interrupt_id":"i-1"}')
    assert e.kind == "questions" and e.text is None and e.path is None
    tr = TurnResult(events=[e, AgentEvent(kind="done")])
    assert [ev.kind for ev in tr.events] == ["questions", "done"]
```

- [ ] **Step 5: 테스트 실행**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models.py tests/test_routes_turns.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "refactor(backend): move AgentEvent/TurnResult into models.py

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 에이전트 도구를 pathfinder/agent/tools.py로 이전 + 룰 디렉토리 라우팅

`harness/aiplc_tools.py`를 `backend/pathfinder/agent/tools.py`로 옮긴다. 핵심 변경: `file_read`의 `aiplc-rules/` 프리픽스는 읽기 전용 룰 디렉토리로, 그 외는 프로젝트 워크스페이스로 라우팅한다(VM 이미지에 구워진 `/workspace/aiplc-rules`가 없어졌으므로).

**Files:**
- Create: `backend/pathfinder/agent/__init__.py` (빈 파일)
- Create: `backend/pathfinder/agent/tools.py`
- Create: `backend/tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `pathfinder.models.AgentEvent` (Task 2).
- Produces:
  - `pathfinder.agent.tools.build_tools(workspace: str, rules_dir: str, emit: Callable[[AgentEvent], None]) -> list` — **`rules_dir` 인자 신설**(구 버전 대비).
  - `pathfinder.agent.tools.QUESTIONS_SCHEMA_HINT: str` (무변경).

- [ ] **Step 1: 실패 테스트 작성 — 룰 라우팅**

`backend/tests/test_agent_tools.py`:
```python
from pathlib import Path
import pytest
from pathfinder.models import AgentEvent
from pathfinder.agent.tools import build_tools, QUESTIONS_SCHEMA_HINT


def _tool_by_name(tools, name):
    # strands @tool 객체는 .tool_name을 노출하고, 객체 자체가 호출 가능하다
    # (도구 본체 직접 호출) — 검증된 harness/tests/test_aiplc_tools.py 패턴.
    return next(t for t in tools if getattr(t, "tool_name", getattr(t, "__name__", "")) == name)


def _tools(workspace, rules_dir):
    emitted = []
    tools = build_tools(str(workspace), str(rules_dir), emitted.append)
    return {name: _tool_by_name(tools, name)
            for name in ("ask_questions", "report_stage", "submit_document",
                         "file_read", "file_write", "file_append")}, emitted


def test_file_read_routes_aiplc_rules_prefix_to_rules_dir(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; (rules / "aiplc-rules").mkdir(parents=True)
    (rules / "aiplc-rules" / "core-workflow.md").write_text("RULE BODY", encoding="utf-8")
    tools, _ = _tools(ws, rules)
    out = tools["file_read"](path="aiplc-rules/core-workflow.md")
    assert "RULE BODY" in out


def test_file_read_routes_non_rules_path_to_workspace(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; rules.mkdir()
    (ws / "aiplc-docs").mkdir()
    (ws / "aiplc-docs" / "audit.md").write_text("WS BODY", encoding="utf-8")
    tools, _ = _tools(ws, rules)
    out = tools["file_read"](path="aiplc-docs/audit.md")
    assert "WS BODY" in out


def test_file_write_confined_to_workspace_emits_file_changed(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tools, emitted = _tools(ws, tmp_path / "rules")
    tools["file_write"](path="aiplc-docs/x.md", content="hi")
    assert (ws / "aiplc-docs" / "x.md").read_text(encoding="utf-8") == "hi"
    assert any(e.kind == "file_changed" and e.path == "aiplc-docs/x.md" for e in emitted)


def test_file_read_rejects_escape_from_rules_dir(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rules = tmp_path / "rules"; rules.mkdir()
    tools, _ = _tools(ws, rules)
    with pytest.raises(ValueError):
        tools["file_read"](path="aiplc-rules/../../etc/passwd")


def test_report_stage_rejects_invalid_status(tmp_path):
    tools, _ = _tools(tmp_path / "ws", tmp_path / "rules")
    out = tools["report_stage"](stage="Envision", status="bogus")
    assert "invalid status" in out


def test_schema_hint_mentions_parse_ok_and_multi_select():
    assert "parse_ok" in QUESTIONS_SCHEMA_HINT
    assert "multi_select" in QUESTIONS_SCHEMA_HINT
```

> 주: strands `@tool`은 원함수를 `original_function`으로, 이름을 `tool_name`으로 노출한다(SDK v1.48). 도구 본체를 직접 호출해 부작용을 검증한다 — 기존 harness/tests/test_aiplc_tools.py와 동일 방식.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.agent'`.

- [ ] **Step 3: agent 패키지 + tools.py 작성**

`backend/pathfinder/agent/__init__.py`: 빈 파일.

`backend/pathfinder/agent/tools.py` (구 aiplc_tools.py 이식 + 룰 라우팅):
```python
# backend/pathfinder/agent/tools.py — 에이전트의 UI 접점(구 harness/aiplc_tools.py).
# 코드가 UI 계약을 강제하고, 룰(markdown)이 내용을 채운다.
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from strands import tool
from pathfinder.models import AgentEvent

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


def _confine(root: str, rel: str) -> Path:
    """rel을 root에 붙여 해석하고 탈출을 거부한다(escape → ValueError)."""
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base) or rel.startswith("/"):
        raise ValueError(f"path escapes root: {rel}")
    return p


def build_tools(workspace: str, rules_dir: str,
                emit: Callable[[AgentEvent], None]) -> list:
    """워크스페이스 + 룰 디렉토리 + 이벤트 싱크에 바인딩된 6개 도구.

    file_read는 'aiplc-rules/' 프리픽스면 rules_dir(읽기 전용)로, 그 외는
    workspace로 라우팅한다 — 구조상 VM 이미지에 구워졌던 /workspace/aiplc-rules를
    대체한다. file_write/file_append는 항상 workspace만 대상으로 한다(룰은 데이터,
    산출물 아님 — 쓰기 금지)."""

    @tool(context=True)
    def ask_questions(questions_file: dict, tool_context: Any) -> str:
        """사용자에게 객관식 질문 세트를 제시하고 답변을 기다린다. 질문은
        반드시 이 도구로만 전달한다(파일로만 남기지 말 것).

        Args:
            questions_file: 질문 파일 페이로드(dict) — name/preamble/questions.
        """
        answers = tool_context.interrupt(
            "ask_questions", reason={"questions_payload": questions_file})
        return f"사용자 답변: {json.dumps(answers, ensure_ascii=False)}"

    @tool
    def report_stage(stage: str, status: str, summary: str = "") -> str:
        """Discovery 스테이지 전이를 선언한다.

        Args:
            stage: 스테이지 이름 (예: "Envision").
            status: "pending" | "in_progress" | "completed".
            summary: 한 줄 요약.
        """
        if status not in ("pending", "in_progress", "completed"):
            return f"invalid status '{status}' — use pending|in_progress|completed"
        emit(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage, "status": status, "summary": summary}, ensure_ascii=False)))
        return f"stage recorded: {stage} ({status})"

    @tool
    def submit_document(path: str, version: str, summary: str = "") -> str:
        """리뷰 대상 문서가 준비/갱신되었음을 선언한다.

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
        """워크스페이스 파일 또는 룰(aiplc-rules/ 프리픽스)을 읽는다.

        Args:
            path: 상대 경로. 'aiplc-rules/'로 시작하면 읽기 전용 룰 디렉토리에서,
                  그 외에는 프로젝트 워크스페이스에서 읽는다.
        """
        if path.startswith("aiplc-rules/"):
            return _confine(rules_dir, path).read_text(encoding="utf-8")
        return _confine(workspace, path).read_text(encoding="utf-8")

    @tool
    def file_write(path: str, content: str) -> str:
        """워크스페이스 파일 전체를 덮어쓴다 — content가 파일의 유일한 내용이 된다.
        기존 내용에 덧붙이려면(특히 audit.md) 반드시 file_append를 사용할 것.

        Args:
            path: 워크스페이스 상대 경로.
            content: 파일 전체 내용.
        """
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        emit(AgentEvent(kind="file_changed", path=path))
        return f"written: {path}"

    @tool
    def file_append(path: str, content: str) -> str:
        """워크스페이스 파일 끝에 content를 덧붙인다 — 기존 내용은 보존된다.
        audit.md 엔트리 추가 등 누적 기록에 사용. 파일이 없으면 새로 만든다.

        Args:
            path: 워크스페이스 상대 경로.
            content: 덧붙일 내용.
        """
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        emit(AgentEvent(kind="file_changed", path=path))
        return f"appended: {path}"

    return [ask_questions, report_stage, submit_document, file_read, file_write, file_append]
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(backend): port aiplc agent tools into pathfinder.agent.tools with rules-dir routing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Strands 드라이버를 pathfinder/agent/driver.py로 이전

`harness/strands_driver.py`를 `backend/pathfinder/agent/driver.py`로 옮긴다. 변경점: import를 `events`→`pathfinder.models`, `aiplc_tools`→`pathfinder.agent.tools`로, `_system_prompt`/기본 팩토리가 `rules_dir`를 받아 룰을 읽고 `build_tools`에 넘기도록. 세션 폴백 경로는 VM 고정 경로 대신 워크스페이스 하위로.

**Files:**
- Create: `backend/pathfinder/agent/driver.py`
- Create: `backend/tests/test_agent_driver.py` (harness/tests/test_strands_driver.py 이식)

**Interfaces:**
- Consumes: `pathfinder.models.AgentEvent`, `pathfinder.agent.tools.build_tools`/`QUESTIONS_SCHEMA_HINT`.
- Produces:
  - `pathfinder.agent.driver.StrandsDriver(workspace: str, rules_dir: str, agent_factory: Callable[[dict, Callable], Any] | None = None)` — **`rules_dir` 인자 신설**.
  - 메서드: `run(text, session) -> AsyncIterator[AgentEvent]`, `run_answers(interrupt_id, answers, session) -> AsyncIterator[AgentEvent]`, `async pending(session) -> str | None`.
  - `session` dict 형태: `{"session_id","bucket","region","prefix"}` (기존과 동일).

- [ ] **Step 1: 실패 테스트 작성 (핵심 계약 이식)**

`backend/tests/test_agent_driver.py`:
```python
import pytest
from pathfinder.agent.driver import StrandsDriver, _questions_event_from_interrupts


class FakeResult:
    def __init__(self, stop_reason="end_turn", interrupts=None):
        self.stop_reason = stop_reason
        self.interrupts = interrupts


class FakeInterrupt:
    def __init__(self, id="i-1", reason=None):
        self.id = id
        self.name = "ask_questions"
        self.reason = reason or {"questions_payload": {"name": "q", "questions": []}}


class FakeInterruptState:
    def __init__(self, activated=False, interrupts=None):
        self.activated = activated
        self.interrupts = interrupts or {}


class FakeAgent:
    def __init__(self, script, interrupt_state=None):
        self._script = script
        self.calls = []
        self._interrupt_state = interrupt_state

    async def stream_async(self, prompt):
        self.calls.append(prompt)
        for ev in self._script:
            yield ev


def make_driver(script, interrupt_state=None):
    def factory(session, emit):
        return FakeAgent(script, interrupt_state)
    return StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules",
                         agent_factory=factory)


SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-1", "prefix": "sessions"}


async def _collect(aiter):
    return [e async for e in aiter]


async def test_text_deltas_become_message_events_and_done():
    drv = make_driver([{"data": "안녕"}, {"data": "하세요"},
                       {"result": FakeResult("end_turn")}])
    evs = await _collect(drv.run("hi", SESSION))
    assert [e.kind for e in evs] == ["message", "message", "done"]
    assert evs[0].text == "안녕"


async def test_interrupt_result_yields_questions_then_done():
    itr = FakeInterrupt()
    drv = make_driver([{"result": FakeResult("interrupt", interrupts=[itr])}])
    evs = await _collect(drv.run("go", SESSION))
    assert [e.kind for e in evs] == ["questions", "done"]


async def test_run_answers_resumes_with_interrupt_response():
    captured = {}
    def factory(session, emit):
        agent = FakeAgent([{"result": FakeResult("end_turn")}])
        orig = agent.stream_async
        async def spy(prompt):
            captured["prompt"] = prompt
            async for ev in orig(prompt):
                yield ev
        agent.stream_async = spy
        return agent
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    await _collect(drv.run_answers("i-7", {"1": "A"}, SESSION))
    assert captured["prompt"] == [{"interruptResponse": {"interruptId": "i-7", "response": {"1": "A"}}}]


async def test_stream_error_yields_error_event():
    class Boom(FakeAgent):
        async def stream_async(self, prompt):
            raise RuntimeError("kaboom")
            yield  # unreachable
    def factory(session, emit):
        return Boom([])
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    evs = await _collect(drv.run("x", SESSION))
    assert evs[-1].kind == "error"
    assert "agent turn failed" in evs[-1].text


async def test_agent_construction_failure_yields_generic_error():
    def factory(session, emit):
        raise RuntimeError("bedrock init failed")
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    evs = await _collect(drv.run("x", SESSION))
    assert [e.kind for e in evs] == ["error"]
    assert "agent turn failed" in evs[0].text


async def test_pending_returns_none_on_construction_failure():
    def factory(session, emit):
        raise RuntimeError("boom")
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    assert await drv.pending(SESSION) is None


async def test_free_text_while_interrupt_pending_reminds_without_calling_model():
    state = FakeInterruptState(activated=True, interrupts={"i-1": FakeInterrupt()})
    drv = make_driver([{"data": "MODEL WAS CALLED"}], interrupt_state=state)
    evs = await _collect(drv.run("아무 말", SESSION))
    kinds = [e.kind for e in evs]
    assert "message" in kinds and kinds[-1] == "done"
    assert all(e.text != "MODEL WAS CALLED" for e in evs)  # 모델 호출 안 함


async def test_status_events_deduped_on_repeated_current_tool_use():
    drv = make_driver([
        {"current_tool_use": {"name": "file_write"}},
        {"current_tool_use": {"name": "file_write"}},
        {"current_tool_use": {"name": "file_read"}},
        {"result": FakeResult("end_turn")},
    ])
    evs = await _collect(drv.run("go", SESSION))
    status = [e.text for e in evs if e.kind == "status"]
    assert status == ["file_write", "file_read"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.agent.driver'`.

- [ ] **Step 3: driver.py 작성 (strands_driver.py 이식)**

`backend/pathfinder/agent/driver.py` — 원본 `harness/strands_driver.py`를 옮기되 아래만 변경:

1. import 교체:
```python
from pathfinder.models import AgentEvent
from pathfinder.agent.tools import build_tools, QUESTIONS_SCHEMA_HINT
```

2. `_system_prompt`가 `rules_dir`를 받도록 (VM의 `/workspace/aiplc-rules` → 주입된 rules_dir):
```python
_RULES_SUBDIR = "aws-aiplc-rules"
_COMMON_SUBDIR = "aws-aiplc-rule-details/common"

def _system_prompt(rules_dir: str) -> str:
    """core-workflow + common 룰 원문(룰은 데이터), 그 뒤 통합 규약 addendum.
    스테이지 상세 룰은 인라인하지 않는다 — core 워크플로가 file_read로 온디맨드
    로드하도록 지시한다."""
    rd = Path(rules_dir)
    parts = [(rd / _RULES_SUBDIR / "core-workflow.md").read_text(encoding="utf-8")]
    common = rd / _COMMON_SUBDIR
    if common.is_dir():
        for f in sorted(common.glob("*.md")):
            parts.append(f"\n\n---\n# RULE DETAIL: common/{f.name}\n" + f.read_text(encoding="utf-8"))
    parts.append(_CONTACT_ADDENDUM)
    return "".join(parts)
```
> 원본은 `_RULES_DIR = "aiplc-rules/aws-aiplc-rules"`를 workspace 기준으로 읽었다. 이제 rules_dir 안에 `aws-aiplc-rules/`, `aws-aiplc-rule-details/`가 직접 있으므로(파일 트리 확인: `files/aiplc-rules/aws-aiplc-rules`, `.../aws-aiplc-rule-details/common`) 서브디렉토리만 붙인다.

3. `_CONTACT_ADDENDUM`은 원본 그대로 유지(`{QUESTIONS_SCHEMA_HINT}` 삽입 포함).

4. 기본 에이전트 팩토리가 rules_dir로 시스템 프롬프트를 만들고 build_tools에 workspace+rules_dir 전달:
```python
def _default_agent_factory(workspace: str, rules_dir: str):
    def factory(session: dict, emit: Callable[[AgentEvent], None]):
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(
            model_id=os.environ["ANTHROPIC_MODEL"],
            max_tokens=64000,
            additional_request_fields={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        )
        return Agent(
            model=model,
            system_prompt=_system_prompt(rules_dir),
            tools=build_tools(workspace, rules_dir, emit),
            session_manager=_session_manager(session, workspace),
            callback_handler=None,
        )
    return factory
```

5. `_session_manager`의 FileSessionManager 폴백 경로를 워크스페이스 하위로(스펙 §4):
```python
def _session_manager(session: dict, workspace: str):
    if session.get("bucket"):
        from strands.session import S3SessionManager
        return S3SessionManager(
            session_id=session["session_id"], bucket=session["bucket"],
            prefix=session.get("prefix", "sessions"),
            region_name=session.get("region") or None)
    from strands.session import FileSessionManager
    return FileSessionManager(session_id=session["session_id"],
                              storage_dir=str(Path(workspace) / ".sessions"))
```

6. `StrandsDriver.__init__` 시그니처에 rules_dir 추가:
```python
class StrandsDriver:
    def __init__(self, workspace: str, rules_dir: str,
                 agent_factory: Callable[[dict, Callable], Any] | None = None):
        self._workspace = workspace
        self._rules_dir = rules_dir
        self._factory = agent_factory or _default_agent_factory(workspace, rules_dir)
        self._agents: dict[str, Any] = {}
        self._queues: dict[str, collections.deque] = {}
```

`_agent_for`, `_stream`, `run`, `run_answers`, `pending`, `_questions_event_from_interrupts`는 원본 그대로(로직 무변경 — Global Constraints의 문자열/계약 유지). 로거 이름은 `logging.getLogger("pathfinder.agent")`로.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(backend): port StrandsDriver into pathfinder.agent.driver with injected rules_dir

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: AgentRunner — 턴 오케스트레이션 (MicroVMSandbox 승계)

`MicroVMSandbox`의 턴 로직(restore→실행→sync, interrupt 소유, 직렬화, redaction, fail-closed)을 VM 부팅 없는 `AgentRunner`로 승계한다. 파일 계약 ops는 S3 직접. 워크스페이스는 로컬 디렉토리. `stop()`은 로컬 디렉토리 정리.

**Files:**
- Create: `backend/pathfinder/runner.py`
- Create: `backend/tests/test_runner.py` (test_microvm_sandbox.py + test_microvm_persistence.py의 존속 시나리오 이식)

**Interfaces:**
- Consumes: `pathfinder.agent.driver.StrandsDriver`, `pathfinder.s3store.S3StoreLike`, `pathfinder.pathsafe.reject_unsafe`, `pathfinder.globmatch.matches_glob`, `pathfinder.models.AgentEvent`, `pathfinder.parsers.redaction.redact_credentials`.
- Produces: `pathfinder.runner.AgentRunner`:
  - `__init__(self, project_id: str, driver, s3: S3StoreLike, local_root: Path, session: dict)`
  - `input_holder: str | None`, `set_input_holder(holder)`
  - `async read_file/write_file(rel_path[, content])`, `async list_files(glob) -> list[str]`
  - `send_message(text) -> AsyncIterator[AgentEvent]`, `send_answers(answers: dict[str,str]) -> AsyncIterator[AgentEvent]`
  - `async pending() -> str | None`, `async stop() -> None`

- [ ] **Step 1: 실패 테스트 작성 — 파일 계약·턴·sync·interrupt·직렬화·정리**

`backend/tests/test_runner.py`:
```python
import json
from pathlib import Path
import pytest
from pathfinder.runner import AgentRunner
from pathfinder.models import AgentEvent
from fakes.in_memory_s3 import FakeS3Store

Q_PAYLOAD = json.dumps({"interrupt_id": "i-7", "questions": {"name": "q", "questions": []}})
SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-1", "prefix": "sessions"}


class FakeDriver:
    """workspace(local_root) 파일을 실제로 만지는 최소 드라이버.
    run()은 files_written을 로컬 워크스페이스에 쓴 뒤 scripted 이벤트를 낸다."""
    def __init__(self, events=None, files_written=None, answers_events=None,
                 pending_payload=None, workspace=None):
        self._events = events or [AgentEvent(kind="message", text="ok"), AgentEvent(kind="done")]
        self._files = files_written or {}
        self._answers_events = answers_events
        self._pending = pending_payload
        self._workspace = workspace
        self.answer_calls = []

    async def _emit(self, evs):
        for k, v in self._files.items():
            (Path(self._workspace) / k).parent.mkdir(parents=True, exist_ok=True)
            (Path(self._workspace) / k).write_text(v, encoding="utf-8")
        for e in evs:
            yield e

    def run(self, text, session):
        return self._emit(self._events)

    def run_answers(self, interrupt_id, answers, session):
        self.answer_calls.append((interrupt_id, answers))
        return self._emit(self._answers_events or [AgentEvent(kind="done")])

    async def pending(self, session):
        return self._pending


def _runner(tmp_path, driver=None, s3=None):
    root = tmp_path / "ws"
    driver = driver or FakeDriver(workspace=root)
    if driver._workspace is None:
        driver._workspace = root
    return AgentRunner(project_id="p1", driver=driver, s3=s3 or FakeS3Store(),
                       local_root=root, session=SESSION)


async def _collect(aiter):
    return [e async for e in aiter]


async def test_file_ops_go_to_s3(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/x.md", "hi")
    assert await r.read_file("aiplc-docs/x.md") == "hi"
    assert r._s3.blobs["aiplc-docs/x.md"] == "hi"


async def test_path_safety_rejected(tmp_path):
    r = _runner(tmp_path)
    with pytest.raises(ValueError):
        await r.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await r.list_files("../*")


async def test_list_files_double_star_glob(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/top-questions.md", "t")
    await r.write_file("aiplc-docs/sub/nested-questions.md", "n")
    await r.write_file("aiplc-docs/audit.md", "a")
    found = sorted(await r.list_files("aiplc-docs/**/*-questions.md"))
    assert found == ["aiplc-docs/sub/nested-questions.md", "aiplc-docs/top-questions.md"]


async def test_send_message_relays_and_terminates(tmp_path):
    r = _runner(tmp_path)
    evs = await _collect(r.send_message("go"))
    assert evs[-1].kind == "done"


async def test_turn_syncs_written_files_to_s3(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(files_written={"aiplc-docs/aiplc-state.md": "stage: Discovery",
                                  "prototype/app.py": "print('hi')",
                                  "node_modules/pkg.js": "DROP"}, workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("start"))
    assert r._s3.blobs["aiplc-docs/aiplc-state.md"] == "stage: Discovery"
    assert r._s3.blobs["prototype/app.py"] == "print('hi')"
    assert "node_modules/pkg.js" not in r._s3.blobs  # sync 글롭 밖


async def test_audit_md_redacted_at_rest(tmp_path):
    root = tmp_path / "ws"
    raw = "Setup.\nkey sk-abc123def456ghi789 used.\nEnd."
    d = FakeDriver(files_written={"aiplc-docs/audit.md": raw}, workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("go"))
    synced = r._s3.blobs["aiplc-docs/audit.md"]
    assert "sk-abc123def456ghi789" not in synced
    assert "[CREDENTIAL REDACTED]" in synced


async def test_restore_pushes_s3_into_local_before_turn(tmp_path):
    root = tmp_path / "ws"
    s3 = FakeS3Store()
    s3.blobs["uploads/의견.md"] = "# 의견"
    d = FakeDriver(workspace=root)
    r = _runner(tmp_path, driver=d, s3=s3)
    await _collect(r.send_message("읽어줘"))
    assert (root / "uploads" / "의견.md").read_text(encoding="utf-8") == "# 의견"


async def test_sync_completes_before_done_yield(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(files_written={"aiplc-docs/aiplc-state.md": "stage: mid"}, workspace=root)
    r = _runner(tmp_path, driver=d)
    saw_done = False
    async for e in r.send_message("go"):
        if e.kind == "done":
            saw_done = True
            assert await r.read_file("aiplc-docs/aiplc-state.md") == "stage: mid"
    assert saw_done


async def test_concurrent_turn_busy_signal(tmp_path):
    r = _runner(tmp_path)
    r._turn_active = True
    evs = await _collect(r.send_message("second"))
    assert len(evs) == 1 and evs[0].kind == "error"
    assert "in progress" in evs[0].text


async def test_questions_event_arms_interrupt_and_answers_resume(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(events=[AgentEvent(kind="questions", payload=Q_PAYLOAD),
                          AgentEvent(kind="done")], workspace=root)
    r = _runner(tmp_path, driver=d)
    await _collect(r.send_message("시작"))
    await _collect(r.send_answers({"1": "A"}))
    assert d.answer_calls == [("i-7", {"1": "A"})]


async def test_send_answers_without_pending_errors(tmp_path):
    r = _runner(tmp_path)
    evs = await _collect(r.send_answers({"1": "A"}))
    assert evs[0].kind == "error" and "no pending questions" in evs[0].text


async def test_malformed_questions_payload_does_not_arm(tmp_path):
    root = tmp_path / "ws"
    d = FakeDriver(events=[AgentEvent(kind="questions", payload="not-json{"),
                          AgentEvent(kind="done")], workspace=root)
    r = _runner(tmp_path, driver=d)
    evs = await _collect(r.send_message("시작"))
    assert evs[-1].kind == "done"
    assert r._pending_interrupt_id is None
    follow = await _collect(r.send_answers({"1": "A"}))
    assert follow[0].kind == "error"


async def test_pending_delegates_to_driver_and_arms(tmp_path):
    r = _runner(tmp_path, driver=FakeDriver(pending_payload=Q_PAYLOAD, workspace=tmp_path/"ws"))
    assert await r.pending() == Q_PAYLOAD
    assert r._pending_interrupt_id == "i-7"


async def test_pending_degrades_to_none_on_driver_error(tmp_path):
    class Raising(FakeDriver):
        async def pending(self, session):
            raise RuntimeError("dead")
    r = _runner(tmp_path, driver=Raising(workspace=tmp_path/"ws"))
    assert await r.pending() is None


async def test_stop_removes_local_root(tmp_path):
    r = _runner(tmp_path)
    await r.write_file("aiplc-docs/x.md", "hi")  # S3만 씀
    (r._local_root).mkdir(parents=True, exist_ok=True)
    (r._local_root / "scratch.txt").write_text("x", encoding="utf-8")
    await r.stop()
    assert not r._local_root.exists()


async def test_input_holder_settable(tmp_path):
    r = _runner(tmp_path)
    assert r.input_holder is None
    r.set_input_holder("facilitator-1")
    assert r.input_holder == "facilitator-1"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.runner'`.

- [ ] **Step 3: runner.py 작성**

`backend/pathfinder/runner.py`:
```python
# backend/pathfinder/runner.py — 턴 오케스트레이션(구 MicroVMSandbox 승계, VM 없음).
from __future__ import annotations
import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import AsyncIterator

from pathfinder.models import AgentEvent
from pathfinder.globmatch import matches_glob
from pathfinder.pathsafe import reject_unsafe
from pathfinder.s3store import S3StoreLike
from pathfinder.parsers.redaction import redact_credentials

_log = logging.getLogger(__name__)


def _interrupt_id_from(payload: str | None) -> str | None:
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


class AgentRunner:
    """프로젝트당 턴 실행기. 파일 계약 ops는 durable S3 직접(부팅 없음). 턴은
    S3 → 로컬 워크스페이스 restore, in-process 에이전트 실행, done/error 시
    로컬 → S3 sync. VM/부팅 상태기계는 없다 — 로컬 디렉토리는 휘발이며 매 턴
    시작 시 S3에서 재구성된다(S3 = source of truth)."""

    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*", "uploads/**/*")
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")

    def __init__(self, project_id, driver, s3: S3StoreLike, local_root: Path, session: dict):
        self.project_id = project_id
        self._driver = driver
        self._s3 = s3
        self._local_root = Path(local_root)
        self._session = session
        self._turn_active = False
        self._pending_interrupt_id: str | None = None
        self.input_holder: str | None = None

    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder

    # ---- file-as-contract ops: durable S3 직접 ----

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        return await self._s3.get(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        await self._s3.put(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if matches_glob(k, glob))

    # ---- workspace <-> S3 ----

    def _local_path(self, key: str) -> Path:
        reject_unsafe(key)
        return self._local_root / key

    async def _restore_workspace_from_s3(self) -> None:
        """durable 워크스페이스(S3 = source of truth)를 로컬 FS로 복사한다.
        S3가 무조건 이긴다; 푸시는 멱등."""
        for prefix in self._RESTORE_PREFIXES:
            for key in await self._s3.list(prefix):
                p = self._local_path(key)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(await self._s3.get(key), encoding="utf-8")

    async def _sync_workspace_to_s3(self) -> None:
        """턴 출력(방법론 산출물 + 프로토타입 소스 서브트리)을 로컬에서 durable
        S3로 끌어올린다. audit.md는 저장 시 redaction(direct S3 reader 노출 차단)."""
        for glob in self._SYNC_GLOBS:
            for path in self._local_root.rglob("*"):
                if not path.is_file():
                    continue
                key = path.relative_to(self._local_root).as_posix()
                if not matches_glob(key, glob):
                    continue
                reject_unsafe(key)  # fail-closed: 안전하지 않은 키는 sync 전체 중단
                content = path.read_text(encoding="utf-8", errors="replace")
                if key == "aiplc-docs/audit.md":
                    content = redact_credentials(content)
                await self._s3.put(key, content)

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            async for event in self._driver.run(text, self._session):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                yield event
        finally:
            self._turn_active = False

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._turn_active = True
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
            async for event in self._driver.run_answers(interrupt_id, answers, self._session):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                yield event
        finally:
            self._turn_active = False

    async def pending(self) -> str | None:
        try:
            payload = await self._driver.pending(self._session)
        except Exception:
            _log.exception("pending probe failed")
            return None
        got = _interrupt_id_from(payload)
        if got:
            self._pending_interrupt_id = got
        return payload

    async def stop(self) -> None:
        """로컬 워크스페이스 정리. S3(durable)는 건드리지 않는다 — 삭제는
        projects.py의 delete_project_data가 담당."""
        await asyncio.to_thread(shutil.rmtree, self._local_root, ignore_errors=True)


def _glob_prefix(glob: str) -> str:
    """글롭의 선행 정적(와일드카드 없는) 디렉토리 부분 = S3 list prefix.
    'aiplc-docs/**/*-q.md' -> 'aiplc-docs/', 'aiplc-docs/audit.md' -> 그 자체."""
    from pathlib import PurePosixPath
    parts = PurePosixPath(glob).parts
    static: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    prefix = "/".join(static)
    if not static:
        return ""
    if len(static) == len(parts):
        return prefix
    return prefix + "/"
```

> `_sync_workspace_to_s3`는 원본이 harness `list_files`(글롭)로 하던 것을 로컬 `rglob`로 대체한다 — 같은 `matches_glob` 판정을 재사용하므로 `_SYNC_GLOBS` 결과가 동일하다. `read_text(errors="replace")`는 원본 harness `get_file`의 lossy-decode 정책(바이너리 프로토타입 자산 대비)을 승계한다.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runner.py -q`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(backend): AgentRunner — in-process turn orchestration (succeeds MicroVMSandbox)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Workspace가 AgentRunner를 소유하도록 전환 + Sandbox ABC 제거

`Workspace.__init__(sandbox)` → `Workspace.__init__(runner)`로 바꾸고 내부 참조(`self.sandbox` → `self.runner`)를 갱신. `Sandbox` ABC(`base.py`)와 나머지 sandbox/ 파일(local/microvm/harness/microvm_control*)을 삭제한다. `input_holder`는 AgentRunner가 이미 가지므로 Workspace는 위임만.

**Files:**
- Modify: `backend/pathfinder/workspace.py` (Workspace: sandbox→runner)
- Delete: `backend/pathfinder/sandbox/` 전체 (base.py, local.py, microvm.py, microvm_control.py, microvm_control_aws.py, harness.py, `__init__.py`)
- Modify: `backend/tests/test_workspace.py` (LocalSandbox → FakeRunner)

**Interfaces:**
- Consumes: `pathfinder.runner.AgentRunner` (Task 5).
- Produces: `pathfinder.workspace.Workspace(runner)` with `self.runner`; `Workspace` 메서드 시그니처(get_questions/put_answers/get_state/get_audit/get_document/list_question_files/list_artifacts) 무변경. `ProjectRegistry` 무변경.

- [ ] **Step 1: workspace.py 테스트를 FakeRunner 기반으로 재작성**

`backend/tests/test_workspace.py` 상단 및 `_seeded` 교체:
```python
from pathlib import Path
from pathfinder.workspace import Workspace, ProjectRegistry
from fakes.in_memory_s3 import FakeS3Store

FIX = Path(__file__).parent / "fixtures"


class FakeRunner:
    """Workspace가 의존하는 파일 계약 ops만 가진 최소 러너 (S3 backed)."""
    def __init__(self, s3=None):
        self._s3 = s3 or FakeS3Store()
        self.input_holder = None

    async def read_file(self, rel):
        return await self._s3.get(rel)

    async def write_file(self, rel, content):
        await self._s3.put(rel, content)

    async def list_files(self, glob):
        from pathfinder.globmatch import matches_glob
        keys = await self._s3.list("")
        return sorted(k for k in keys if matches_glob(k, glob))


async def _seeded():
    r = FakeRunner()
    await r.write_file("aiplc-docs/strategy-questions.md",
                       (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    await r.write_file("aiplc-docs/aiplc-state.md",
                       (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    return Workspace(r)
```
그리고 각 테스트의 `tmp_path` 인자·`await _seeded(tmp_path)`를 `await _seeded()`로, registry 테스트의 `LocalSandbox(root=...)`를 `FakeRunner()`로 바꾼다. (registry는 attach된 객체를 저장만 하므로 어떤 객체든 통과.)

- [ ] **Step 2: 재작성 테스트가 (아직 workspace.py 미변경 상태에서) 실패하는지 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace.py -q`
Expected: FAIL — `Workspace`가 아직 `sandbox` 속성을 쓰므로 `self.sandbox` 참조가 FakeRunner에 없어 AttributeError(또는 인자명 불일치). 이 실패가 다음 스텝을 유도한다.

- [ ] **Step 3: workspace.py 전환**

`backend/pathfinder/workspace.py`에서 `Workspace` 클래스만 수정 (import는 Sandbox 제거):
```python
# 상단: from pathfinder.sandbox.base import Sandbox  ← 삭제
class Workspace:
    def __init__(self, runner):
        self.runner = runner

    async def get_questions(self, name: str) -> QuestionFile:
        md = await self.runner.read_file(name)
        return parse_question_file(name.split("/")[-1], md)

    async def put_answers(self, name: str, answers: dict[int, str]) -> QuestionFile:
        md = await self.runner.read_file(name)
        new_md = serialize_answers(md, answers)
        await self.runner.write_file(name, new_md)
        return parse_question_file(name.split("/")[-1], new_md)

    async def get_state(self) -> ProjectState:
        try:
            md = await self.runner.read_file(_STATE_PATH)
        except FileNotFoundError:
            return ProjectState(stages=[])
        return parse_state_file(md)

    async def get_audit(self) -> list[AuditEntry]:
        try:
            md = await self.runner.read_file(_AUDIT_PATH)
        except FileNotFoundError:
            return []
        return parse_audit_file(md)

    async def get_document(self) -> str:
        try:
            return await self.runner.read_file(_DOC_PATH)
        except FileNotFoundError:
            return ""

    async def list_question_files(self) -> list[str]:
        return await self.runner.list_files("aiplc-docs/**/*-questions.md")

    async def list_artifacts(self) -> list[str]:
        return await self.runner.list_files("aiplc-docs/**/*")
```
`ProjectRegistry`는 무변경(내부 변수명 `_workspaces`, `attach(project_id, sandbox)` 파라미터명은 그대로 두거나 `runner`로 바꿔도 무방 — 저장만 함. 최소 변경 위해 유지).

- [ ] **Step 4: sandbox/ 패키지 삭제**

```bash
cd backend
git rm -r pathfinder/sandbox/
```
> 이 시점에 routes/*.py, app.py가 아직 sandbox를 import하므로 import 에러가 난다 — Task 7에서 즉시 해소한다. 두 Task는 연속 실행 전제(중간 커밋에서 전체 스위트가 깨지는 유일한 지점). 서브에이전트 실행 시 Task 6·7을 하나의 리뷰 단위로 묶어도 된다.

- [ ] **Step 5: workspace 테스트만 실행**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace.py -q`
Expected: PASS. (app.py를 import하지 않는 테스트라 sandbox 삭제 영향 없음.)

- [ ] **Step 6: Commit (Task 7과 함께 검증되는 중간 커밋)**

```bash
git add -A && git commit -m "refactor(backend): Workspace owns AgentRunner; delete Sandbox ABC and sandbox/ package

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: app.py 재배선 (make_workspace) + 라우트/deps 갱신

`make_sandbox`를 `make_workspace`로 바꿔 `AgentRunner`+`StrandsDriver`를 조립한다. VM 컨트롤러/HarnessClient/토큰 민팅/BootSpec/local 분기 배선을 전부 제거. 라우트의 `ws.sandbox.*` → `ws.runner.*`, `sandbox.stop()` → `runner.stop()`. deps.ensure_workspace의 attach 호출과 delete 레이스 처리를 러너 기준으로 갱신.

**Files:**
- Modify: `backend/pathfinder/app.py` (대폭 재작성)
- Modify: `backend/pathfinder/routes/deps.py`, `projects.py`, `turns.py`, `uploads.py`, `artifacts.py`
- Modify/Rewrite: `backend/tests/test_make_sandbox.py` → `test_make_workspace.py`, `test_input_holder.py`, `test_routes_turns.py`, `test_routes_uploads.py`, `test_routes_artifacts.py`, `test_routes_projects_delete.py`, `test_deps_ensure_workspace.py`
- Delete: `backend/tests/test_app_harness_factory.py`, `test_harness_client.py`, `test_harness_headers.py`, `test_local_sandbox.py`, `test_sandbox_base.py`, `test_sandbox_contract.py`, `sandbox_contract.py`, `test_microvm_*.py`, `test_golden_path_replay.py`, `fakes/harness_app.py`, `fakes/in_memory_harness.py`

**Interfaces:**
- Consumes: `pathfinder.runner.AgentRunner`, `pathfinder.agent.driver.StrandsDriver`, `pathfinder.workspace.Workspace`/`ProjectRegistry`, `pathfinder.s3store.S3Store`.
- Produces:
  - `pathfinder.app.make_workspace(project_id: str) -> Workspace` — attach까지 하지 않고 Workspace 객체만 반환. **주의**: 기존 `make_sandbox`는 sandbox만 반환하고 라우트가 `registry.attach`했다. 새 `make_workspace`도 동일하게 **Workspace만 반환**하고 attach는 호출부가 한다. registry는 `attach(pid, workspace)`로 Workspace를 직접 저장하도록 조정.
  - `pathfinder.app.registry`, `durable_projects_enabled()`, `s3_store_factory`, `session_s3_factory`, `projects_root_s3_factory` — 유지.

- [ ] **Step 1: app.py 재작성**

`backend/pathfinder/app.py`를 아래로 교체(핵심부):
```python
from __future__ import annotations
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import boto3
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from pathfinder.workspace import ProjectRegistry, Workspace
from pathfinder.runner import AgentRunner
from pathfinder.agent.driver import StrandsDriver
from pathfinder.s3store import S3Store, S3StoreLike
from pathfinder.project_store import restore_projects

_log = logging.getLogger(__name__)

registry = ProjectRegistry()


def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)


def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)


def projects_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="projects/", client=client)


def durable_projects_enabled() -> bool:
    return bool(os.environ.get("PATHFINDER_S3_BUCKET"))


def _rules_dir() -> str:
    default = str(Path(__file__).resolve().parent.parent.parent / "files" / "aiplc-rules")
    return os.environ.get("PATHFINDER_RULES_DIR", default)


def _workspaces_dir() -> Path:
    root = os.environ.get("PATHFINDER_WORKSPACES_DIR")
    return Path(root) if root else Path(tempfile.gettempdir()) / "pathfinder-workspaces"


# Monkeypatchable in tests: StrandsDriver를 fake agent_factory로 갈아끼운다.
def driver_factory(project_id: str, local_root: Path) -> StrandsDriver:
    return StrandsDriver(workspace=str(local_root), rules_dir=_rules_dir())


async def make_workspace(project_id: str) -> Workspace:
    s3 = s3_store_factory(project_id)
    local_root = _workspaces_dir() / project_id
    session = {
        "session_id": project_id,
        "bucket": os.environ.get("PATHFINDER_S3_BUCKET", ""),
        "region": os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2"),
        "prefix": "sessions",
    }
    driver = driver_factory(project_id, local_root)
    runner = AgentRunner(project_id=project_id, driver=driver, s3=s3,
                         local_root=local_root, session=session)
    return Workspace(runner)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if durable_projects_enabled():
        try:
            for pid, name in await restore_projects(projects_root_s3_factory()):
                registry.register(pid, name)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
    yield


app = FastAPI(title="Pathfinder", lifespan=_lifespan)

_cors_origins = [
    o.strip()
    for o in os.environ.get("PATHFINDER_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"],
    allow_headers=["*"], allow_credentials=False,
)

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)
from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)
from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router)
from pathfinder.routes import discovery  # noqa: E402
app.include_router(discovery.router)
from pathfinder.routes import history  # noqa: E402
app.include_router(history.router)
from pathfinder.routes import uploads  # noqa: E402
app.include_router(uploads.router)
```

- [ ] **Step 2: ProjectRegistry.attach가 Workspace를 직접 받도록 조정**

`backend/pathfinder/workspace.py`의 `ProjectRegistry.attach`는 지금 `sandbox`를 받아 내부에서 `Workspace(sandbox)`를 만든다. make_workspace가 이미 Workspace를 반환하므로 이중 래핑을 피하려 시그니처를 바꾼다:
```python
    def attach(self, project_id: str, workspace: Workspace) -> Workspace:
        if project_id not in self._names:
            raise KeyError(project_id)
        self._workspaces[project_id] = workspace
        return workspace
```
(`Workspace` 타입은 같은 모듈에 있으므로 import 불필요.)

- [ ] **Step 3: deps.py 갱신**

`backend/pathfinder/routes/deps.py`의 boot 블록:
```python
        try:
            workspace = await app_module.make_workspace(pid)
        except Exception:
            _log.exception("lazy workspace init failed for %s", pid)
            raise HTTPException(status_code=503, detail="project workspace unavailable")
        try:
            return app_module.registry.attach(pid, workspace)
        except KeyError:
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("failed to stop runner for deleted-during-boot project %s", pid)
            raise HTTPException(status_code=404, detail="unknown project")
```
docstring/주석의 "VM 부팅"은 "워크스페이스 초기화(로컬 디렉토리)"로 문구만 조정.

- [ ] **Step 4: projects.py 갱신**

`create_project`: `make_sandbox` → `make_workspace`, `sandbox` → `workspace`, `registry.attach(body.project_id, workspace)`, 실패 정리 `workspace.runner.stop()`.
`delete_project`: `already_stopped.sandbox.stop()` → `already_stopped.runner.stop()`, `removed.sandbox.stop()` → `removed.runner.stop()`. (registry.get/remove는 Workspace를 반환하므로 `.runner.stop()`.)

- [ ] **Step 5: turns.py / uploads.py / artifacts.py 갱신**

- turns.py: `ws.sandbox.send_message` → `ws.runner.send_message`, `ws.sandbox.send_answers` → `ws.runner.send_answers`, `ws.sandbox.pending()` → `ws.runner.pending()`.
- uploads.py: `ws.sandbox.list_files` → `ws.runner.list_files`, `ws.sandbox.write_file` → `ws.runner.write_file`.
- artifacts.py: `ws.sandbox.read_file` → `ws.runner.read_file`.

grep으로 잔여 확인:
```bash
grep -rn "\.sandbox\b\|make_sandbox\|import.*sandbox" pathfinder/ | grep -v __pycache__
```
Expected: 빈 결과.

- [ ] **Step 6: 죽은 테스트/fakes 삭제 + make_workspace·input_holder 테스트 재작성**

삭제:
```bash
cd backend
git rm tests/test_app_harness_factory.py tests/test_harness_client.py tests/test_harness_headers.py \
       tests/test_local_sandbox.py tests/test_sandbox_base.py tests/test_sandbox_contract.py \
       tests/sandbox_contract.py tests/test_golden_path_replay.py \
       tests/test_microvm_control.py tests/test_microvm_control_aws.py \
       tests/test_microvm_persistence.py tests/test_microvm_recovery.py tests/test_microvm_sandbox.py \
       tests/fakes/harness_app.py tests/fakes/in_memory_harness.py
git rm tests/test_make_sandbox.py tests/test_input_holder.py
```

`backend/tests/test_make_workspace.py` 신규:
```python
import inspect
import pathfinder.app as app_module
from pathfinder.workspace import Workspace
from pathfinder.runner import AgentRunner


async def test_make_workspace_builds_runner_backed_workspace(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    ws = await app_module.make_workspace("proj-x")
    assert isinstance(ws, Workspace)
    assert isinstance(ws.runner, AgentRunner)
    assert ws.runner.project_id == "proj-x"


def test_make_workspace_signature():
    sig = inspect.signature(app_module.make_workspace)
    assert list(sig.parameters) == ["project_id"]


async def test_runner_input_holder_settable(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    ws = await app_module.make_workspace("proj-ih")
    assert ws.runner.input_holder is None
    ws.runner.set_input_holder("facilitator-9")
    assert ws.runner.input_holder == "facilitator-9"
```

- [ ] **Step 7: 라우트 테스트를 FakeRunner 주입으로 갱신**

`test_routes_turns.py`, `test_routes_uploads.py`는 현재 `make_sandbox`를 monkeypatch하고 `LocalSandbox`를 쓴다. 이를 `make_workspace`를 monkeypatch하고 Workspace(FakeRunner)를 반환하도록 바꾼다. 예 (test_routes_turns.py의 `_install_scripted`):
```python
from pathfinder.workspace import Workspace
from pathfinder.models import AgentEvent
from fakes.in_memory_s3 import FakeS3Store


class ScriptRunner:
    """send_message/send_answers/pending만 필요한 라우트 테스트용 러너."""
    def __init__(self, script_events):
        self._events = script_events
        self.input_holder = None
        self._pending = None
    async def send_message(self, text):
        for e in self._events(text):
            yield e
    async def send_answers(self, answers):
        yield AgentEvent(kind="done")
    async def pending(self):
        return self._pending
    async def stop(self):
        pass


def _install_scripted(monkeypatch, pid, script):
    async def make(project_id):
        return Workspace(ScriptRunner(script))
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
```
> `create_project`가 `make_workspace`+`attach`를 호출하므로 monkeypatch만으로 전 라우트가 FakeRunner를 쓴다. `script`는 기존 시그니처 `(text, sb)`에서 `(text)`로 단순화 — 각 테스트의 `def script(text, sb)`를 `def script(text)`로, sb를 안 쓰던 것 확인. sb를 쓰던 테스트(파일 시드)는 `ScriptRunner`에 FakeS3Store를 붙여 read/write_file 지원 추가.

uploads/artifacts 라우트 테스트도 동일 패턴으로 `make_workspace` monkeypatch + FakeRunner(S3 backed)로 전환.

- [ ] **Step 8: deps/projects delete 테스트 갱신**

`test_deps_ensure_workspace.py`: `make_sandbox` → `make_workspace`, 반환을 `Workspace(FakeRunner)`로. `_FakeSandbox`를 `Workspace`로 감싸고 `.runner.stop()`이 호출되는지 검증하도록 `test_delete_during_boot_races_...`의 FakeSandbox를 `class _FakeRunner: async def stop(self)...` + `Workspace(_FakeRunner())`로 조정.
`test_routes_projects_delete.py`: `sandbox.stop` 검증을 `runner.stop`으로.

- [ ] **Step 9: 전체 백엔드 스위트 실행**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (green). 실패 시 잔여 `.sandbox`/`make_sandbox`/삭제 모듈 import를 grep으로 추적해 수정.

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "refactor(backend): rewire app to make_workspace + AgentRunner; drop VM/local wiring

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: harness/ 삭제 + strands 의존성 백엔드로 이동

harness의 에이전트 3파일은 Task 3/4에서 이식 완료. harness/ 디렉토리 전체를 삭제하고, strands 런타임 의존성을 backend/pyproject.toml로 옮긴다.

**Files:**
- Delete: `harness/` 전체
- Modify: `backend/pyproject.toml` (dependencies에 `strands-agents>=1.48,<2` 추가; boto3 주석에서 lambda-microvms 사유 제거)

**Interfaces:**
- Produces: 없음(정리 Task). 백엔드가 `strands`를 런타임 의존으로 선언.

- [ ] **Step 1: strands 의존성 추가**

`backend/pyproject.toml`의 dependencies 리스트에 `"strands-agents>=1.48,<2"` 추가. boto3 항목 위 주석의 "lambda-microvms 2025-09-09 service model" 사유 문단을 삭제하고 한 줄로 축약:
```python
# boto3 floor 1.43.35 — Bedrock Converse + S3만 사용(과거 lambda-microvms 요구는 제거됨).
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.43.35", "uvicorn>=0.30", "python-dotenv>=1.0", "openpyxl>=3.1", "pypdf>=4.0", "python-multipart>=0.0.9", "strands-agents>=1.48,<2"]
```

- [ ] **Step 2: strands 설치 확인**

Run: `cd backend && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -c "import strands; from strands.models import BedrockModel; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: harness/ 삭제**

```bash
cd /home/ec2-user/project/pathfinder-sp
git rm -r harness/
```

- [ ] **Step 4: 잔여 참조 확인**

```bash
grep -rn "harness" backend/pathfinder/ backend/tests/ | grep -v __pycache__
```
Expected: 빈 결과(또는 무해한 주석만). 있으면 제거.

- [ ] **Step 5: 전체 백엔드 스위트 재실행 (설치 후 회귀 확인)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "chore: delete harness/; move strands-agents dep into backend

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 인프라 CDK 축소 (S3 버킷 + 백엔드 실행 롤만)

MicroVM 이미지·빌드 롤·하네스 asset·로그그룹·package-harness.sh를 제거하고, S3 버킷과 백엔드 실행 롤(Bedrock invoke + S3 projects/*·sessions/*)만 남긴다.

**Files:**
- Modify: `infra/lib/pathfinder-drill-stack.ts` (대폭 축소)
- Delete: `infra/package-harness.sh`
- Modify: `infra/README.md` (VM 절 제거)

**Interfaces:**
- Produces: CfnOutputs `ArtifactsBucketName`, `BackendRoleArn`(신규, 구 ExecutionRoleArn 대체), `Region`. `ImageArn`/`ExecutionRoleArn` 출력 제거.

- [ ] **Step 1: 스택 축소 재작성**

`infra/lib/pathfinder-drill-stack.ts`:
```typescript
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';

const REGION = 'ap-northeast-1';
const MODEL = 'global.anthropic.claude-opus-4-8';
const MODEL_FAMILY = 'anthropic.claude-opus-4-8';

export class PathfinderDrillStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // Artifacts bucket — 프로젝트 산출물(projects/*)과 strands 세션(sessions/*).
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // 백엔드 프로세스가 assume하는 실행 롤: Bedrock invoke + S3(projects/* & sessions/*).
    // 백엔드가 EC2/컨테이너 인스턴스 프로파일로 이 롤을 맡거나, 롤 정책을 그대로
    // 인스턴스 프로파일에 부여한다(호스트 자격증명 모델, spec §2).
    const backendRole = new iam.Role(this, 'BackendRole', {
      assumedBy: new iam.AccountPrincipal(account),
      description: 'Pathfinder backend: Bedrock invoke + artifacts/session S3 access.',
    });
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/${MODEL_FAMILY}*`,
      ],
    }));
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
      resources: [`${bucket.bucketArn}/projects/*`, `${bucket.bucketArn}/sessions/*`],
    }));
    backendRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [bucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['projects/*', 'sessions/*'] } },
    }));

    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BackendRoleArn', { value: backendRole.roleArn });
    new cdk.CfnOutput(this, 'Region', { value: REGION });
  }
}
```
> `AccountPrincipal`은 데모/드릴 편의(계정 내 주체가 assume). 프로덕션은 백엔드 호스트의 인스턴스 프로파일 신뢰로 좁힌다 — README에 명기.

- [ ] **Step 2: package-harness.sh 삭제 + 참조 제거**

```bash
cd /home/ec2-user/project/pathfinder-sp
git rm infra/package-harness.sh
rm -rf infra/build   # 스테이징 산출물(gitignore일 수 있음)
```
`infra/package.json`에 package-harness 관련 script가 있으면 제거.

- [ ] **Step 3: CDK 합성 검증**

Run: `cd infra && npm install && npx cdk synth`
Expected: 합성 성공, 템플릿에 MicrovmImage/BuildRole 리소스 없음. 확인:
```bash
npx cdk synth 2>/dev/null | grep -c "Microvm\|CfnMicrovmImage"
```
Expected: `0`.

- [ ] **Step 4: infra/README.md에서 VM 절 제거**

MicroVM 이미지 빌드·package-harness·하네스 관련 문단 삭제, 남은 리소스(버킷+롤)만 기술. env 출력 매핑을 `BackendRoleArn`/`ArtifactsBucketName`/`Region`으로 갱신.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore(infra): shrink CDK to S3 bucket + backend role; drop MicroVM image/build

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: env·문서 정리 (README, .env.example)

`PATHFINDER_SANDBOX`/`PATHFINDER_VM_*` 삭제, 신규 env 문서화, README를 단일 실행 방법으로 재작성.

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md`

**Interfaces:**
- Produces: 없음(문서). 신규 env: `PATHFINDER_RULES_DIR`, `PATHFINDER_WORKSPACES_DIR`.

- [ ] **Step 1: .env.example 재작성**

`backend/.env.example`:
```bash
# backend/.env — pathfinder.app이 기동 시 자동 로드한다 (실 환경변수가 우선).
# 값은 `cd infra && npx cdk deploy` 출력(CfnOutputs)에서 가져온다.
AWS_REGION=ap-northeast-1
AWS_DEFAULT_REGION=ap-northeast-1

# 영속 스토리지(프로젝트 산출물 projects/* + strands 세션 sessions/*)
PATHFINDER_S3_REGION=ap-northeast-1
PATHFINDER_S3_BUCKET=pathfinderdrillstack-artifacts...

# Bedrock 추론 프로파일 (에이전트가 백엔드에서 직접 호출)
ANTHROPIC_MODEL=global.anthropic.claude-opus-4-8

# aiplc 룰 디렉토리 (미설정 시 <repo>/files/aiplc-rules). 읽기 전용.
# PATHFINDER_RULES_DIR=/abs/path/to/files/aiplc-rules
# 프로젝트별 로컬 워크스페이스 루트 (미설정 시 시스템 tmp 하위)
# PATHFINDER_WORKSPACES_DIR=/var/pathfinder/workspaces

PATHFINDER_CORS_ORIGINS=http://localhost:3000
```

- [ ] **Step 2: README 재작성 — 단일 실행 방법**

`README.md`에서:
- 상단 다이어그램: `harness/` 줄 삭제, `backend/`를 "FastAPI — 파서 · 인프로세스 Strands 에이전트 · SSE 턴 릴레이 · S3 영속화", `infra/`를 "CDK — S3 버킷 + 백엔드 실행 롤"로.
- "샌드박스는 두 가지다" 표(local/microvm) 삭제.
- "실행 방법 A/B" 두 절을 하나로 통합: 백엔드(env 로드 후 uvicorn) + 프론트. AWS 자격증명(호스트 롤/프로필)과 `PATHFINDER_S3_BUCKET`/`ANTHROPIC_MODEL` 필요.
- 환경 변수 표에서 `PATHFINDER_SANDBOX`, `PATHFINDER_VM_*` 행 삭제, `PATHFINDER_RULES_DIR`/`PATHFINDER_WORKSPACES_DIR` 행 추가.
- 테스트 절: "하네스 유닛" 항목 삭제. e2e는 실 Bedrock 필요로 명기.
- 참고 절의 리전/영속화 문구는 유지하되 "MicroVM" 언급을 "백엔드 인프로세스 에이전트"로 조정.

- [ ] **Step 3: 잔여 microvm 문구 스캔**

```bash
grep -rin "microvm\|PATHFINDER_SANDBOX\|PATHFINDER_VM_\|local 모드\|LocalSandbox\|harness" README.md backend/.env.example
```
Expected: 의미 있는 잔여 없음(히스토리성 문구 제외).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: single-run README + env cleanup (drop SANDBOX/VM vars, add RULES/WORKSPACES dirs)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: 프론트엔드 e2e를 실 Bedrock 기준으로 조정

local 데모 시나리오에 결합된 e2e 단언(고정 데모 질문 텍스트, echo 메시지)을 구조 단언으로 바꾼다. 실 Bedrock 응답은 비결정적이므로 "AI가 무엇을 말하는지"가 아니라 "SSE 턴이 흐르고 카드가 렌더되는지"를 검증한다.

**Files:**
- Modify: `frontend/e2e/workspace.spec.ts`
- (참고) `frontend/e2e/projects.spec.ts`는 백엔드 무관(프로젝트 생성/목록)이라 무변경.

**Interfaces:**
- Produces: 없음(테스트). e2e는 자격증명 있는 환경에서만 실행(INTEGRATION).

- [ ] **Step 1: workspace.spec.ts 재작성 (구조 단언)**

고정 데모 질문("주요 사용자는 누구인가요?" 등)·echo 메시지·"Path A" 데모 스크립트 의존 단언을 제거하고, 실 에이전트 턴의 관측 가능한 구조만 단언:
```typescript
import { test, expect } from "@playwright/test";

// INTEGRATION: 실 백엔드(인프로세스 Strands 에이전트 + Bedrock)를 상대로
// /workspace 3-pane 화면을 구동한다. 응답 텍스트는 비결정적이므로 구조만 단언한다:
// 턴 시작 → AI 말풍선이 스트리밍으로 채워짐 → 질문 카드가 뜨면 답변, 아니면 done.
test("웰컴 카드에서 턴을 시작하면 AI 응답이 스트리밍된다", async ({ page }) => {
  const pid = `e2e-workspace-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByRole("link", { name: new RegExp(pid) }).first()).toBeVisible();

  await page.goto(`/projects/${pid}/workspace`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();
  await expect(page.getByText("어떻게 시작할까요?")).toBeVisible();

  const rightPanel = page.getByLabel("컨텍스트 패널");
  await expect(rightPanel).toBeVisible();

  // 웰컴 카드의 Path A 버튼으로 첫 턴 시작.
  await page.getByRole("button", { name: /Path A/ }).click();
  await expect(page.getByText("어떻게 시작할까요?")).toHaveCount(0);

  const timeline = page.getByLabel("대화 타임라인");
  // 사용자 말풍선(입력 텍스트)이 뜬다.
  await expect(timeline.getByText(/Path A/).first()).toBeVisible();
  // 실 에이전트의 AI 말풍선이 스트리밍으로 나타난다(내용 무관, .prose 컨테이너 존재).
  await expect(timeline.locator(".prose").first()).toBeVisible({ timeout: 120_000 });

  // 턴 실패 배너가 뜨지 않았다.
  await expect(page.getByText(/연결이 끊어졌습니다/)).toHaveCount(0);
});

test("retired /questions and /canvas tabs redirect to /workspace", async ({ page }) => {
  const pid = `e2e-redirect-${Date.now()}`;
  const create = await page.request.post("/api/projects", { data: { project_id: pid } });
  expect(create.ok()).toBe(true);
  await page.goto(`/projects/${pid}/questions`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));
  await page.goto(`/projects/${pid}/canvas`);
  await expect(page).toHaveURL(new RegExp(`/projects/${pid}/workspace$`));
});
```
> 질문 폼 상호작용(라디오 선택→제출)은 실 에이전트가 ask_questions를 호출한다는 보장이 없어 결정적으로 단언 불가하므로 제거한다. 질문 카드 렌더/제출의 결정적 검증은 이미 컴포넌트 테스트(QuestionCard.test.tsx, QuestionForm.test.tsx)가 커버한다 — e2e 주석에 그 위임을 명기.

- [ ] **Step 2: 타임아웃/실행 조건 주석 보강**

파일 상단에 실행 전제를 명기: 백엔드가 `PATHFINDER_S3_BUCKET`+`ANTHROPIC_MODEL`+호스트 자격증명으로 떠 있어야 하며, Bedrock 왕복 때문에 `timeout: 120_000`을 둔다. CI 기본 스위트에서 제외되고 자격증명 있는 환경에서만 도는 INTEGRATION 케이스임을 명시.

- [ ] **Step 3: 프론트 유닛 테스트 회귀 확인 (e2e 변경이 유닛에 영향 없음 확인)**

Run: `cd frontend && npm test`
Expected: PASS (기존과 동일 — e2e는 vitest 스위트에 포함되지 않음).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test(frontend): e2e asserts real-agent turn structure, not scripted demo content

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §1 아키텍처(에이전트 이전·Sandbox 해체·삭제) → Task 1–8. ✓
- §2 턴 플로우(restore→실행→sync, S3SessionManager, 재시작 복구, 동시성) → Task 5(runner), Task 4(session manager). ✓
- §3 테스트(fake agent_factory 유닛, e2e 실 Bedrock) → Task 4/5/7(유닛), Task 11(e2e). ✓
- §4 인프라·env → Task 9(CDK), Task 10(env/README), Task 8(strands dep). ✓
- §5 에러·동시성 계약 → Task 5(runner가 전 계약 승계), Task 7(삭제 플로우 runner.stop). ✓
- 룰 소스(VM 이미지 → PATHFINDER_RULES_DIR) → Task 3(tools 라우팅), Task 4(system prompt), Task 7(_rules_dir 배선). ✓
- FileSessionManager 폴백 경로 조정 → Task 4 Step 3-5. ✓

**Placeholder scan:** "add error handling"/"TBD"류 없음. 모든 코드 스텝에 실제 코드 포함. 삭제 목록은 정확한 파일 경로 명시.

**Type consistency:**
- `build_tools(workspace, rules_dir, emit)` — Task 3에서 정의, Task 4에서 동일 시그니처로 호출. ✓
- `StrandsDriver(workspace, rules_dir, agent_factory=None)` — Task 4 정의, Task 7 `driver_factory`에서 동일 호출. ✓
- `AgentRunner(project_id, driver, s3, local_root, session)` — Task 5 정의, Task 7 `make_workspace`에서 동일 호출. ✓
- `Workspace(runner)` + `.runner` — Task 6 정의, Task 7 라우트/deps에서 `ws.runner.*`. ✓
- `registry.attach(pid, workspace)` — Task 7 Step 2에서 Workspace를 받도록 조정, deps/projects 호출부 일치. ✓
- `make_workspace(project_id) -> Workspace` — Task 7 정의, deps/projects/테스트 일치. ✓

**주의 사항(실행자):** Task 6과 Task 7은 연속이다 — Task 6 종료 시점(sandbox/ 삭제 직후, app.py 미수정)에는 app.py를 import하는 테스트가 깨진다. Task 6의 격리 검증은 test_workspace.py만(app 비의존), 전체 그린은 Task 7 Step 9에서 달성. 서브에이전트로 실행할 경우 6·7을 한 리뷰 단위로 묶을 것.
