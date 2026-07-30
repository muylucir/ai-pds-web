# 프로토타입 빌드 세션 수명 재정의 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로토타입 빌드 세션이 에이전트의 명시적 완료 선언 시 스스로 닫히게 하고, 개선 작업은 요약만 실은 새 세션으로 분기한다.

**Architecture:** 에이전트가 새 MCP 도구 `build_complete`를 호출하면 빌더가 큐에 `build_complete` 이벤트를 넣는다(`_on_post_tool_use`가 `file_changed`를 넣는 것과 동형). `PrototypeSession.send_message`가 릴레이하며 그것을 관찰해 `status = "complete"`로 바꾸고 `handoff.json`을 쓴 뒤, 유휴 타이머가 짧은 유예로 세션을 닫는다. 다음 시작은 handoff를 발견하면 전액 resume 대신 새 session_id + 요약 주입으로 분기한다.

**Tech Stack:** Python 3.11 / FastAPI / claude-agent-sdk 0.2.126 / pytest(asyncio) — 백엔드. Next.js 15 App Router / TypeScript / Vitest — 프론트엔드.

**Spec:** `docs/superpowers/specs/2026-07-30-proto-build-session-lifecycle-design.md`

## Global Constraints

- **테스트 실행**: 백엔드는 `backend/` 에서 `.venv/bin/python -m pytest`. 프론트는 `frontend/` 에서 `npx vitest run`.
- **Python 3.11 전용** — 3.9로는 백엔드가 돌지 않는다.
- **모든 주석·프롬프트·UI 문구는 한국어.** 코드 주석은 기존 파일의 밀도와 어조를 따른다(이 코드베이스는 "왜"를 길게 적는다).
- **Bedrock 호출에 샘플링 파라미터를 넣지 않는다** (`temperature`/`top_p`/`top_k`/`budget_tokens`). 이 작업에서 Bedrock 호출 코드를 새로 쓰지는 않지만 규칙은 유효하다.
- **`AgentEvent.kind`는 프론트-백엔드 계약이다.** 한쪽만 바꾸면 안 된다 (Task 2에서 양쪽을 함께 바꾼다).
- **커밋 메시지는 한국어**, 기존 형식을 따른다: `feat(proto):` / `fix(proto):` / `test(proto):`.
- **단일 키 삭제는 `delete_prefix(정확한 키)`** 를 쓴다 — `S3StoreLike`에 단일 키 `delete`가 없고, 이것이 확립된 관례다(`agent/pending_store.py:69`, `survey/store.py:334,368`).

---

## Task 1: `build_complete` MCP 도구

**Files:**
- Create: `backend/pathfinder/proto/tools.py`
- Test: `backend/tests/test_proto_tools.py`

**Interfaces:**
- Consumes: `pathfinder.models.AgentEvent`
- Produces:
  - `PROTO_MCP_SERVER_NAME: str = "pathfinder_proto"`
  - `BUILD_COMPLETE_TOOL: str = "mcp__pathfinder_proto__build_complete"`
  - `build_proto_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list` — `@tool` dataclass 리스트. Task 3이 `create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME, tools=build_proto_tools(...))`로 감싼다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_proto_tools.py` 생성:

```python
# backend/tests/test_proto_tools.py — 프로토타입 빌더의 커스텀 MCP 도구.
from __future__ import annotations

import json

from pathfinder.models import AgentEvent
from pathfinder.proto.tools import (BUILD_COMPLETE_TOOL, PROTO_MCP_SERVER_NAME,
                                    build_proto_tools)


def _handler(workspace, emit):
    """build_proto_tools가 돌려주는 @tool dataclass에서 핸들러를 꺼낸다.
    claude_agent_sdk의 @tool은 SdkMcpTool(name=..., handler=...)를 만든다."""
    tools = build_proto_tools(str(workspace), emit)
    by_name = {t.name: t.handler for t in tools}
    return by_name["build_complete"]


def _prototype_dir(tmp_path):
    d = tmp_path / "prototype"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_tool_name_constant_matches_the_sdk_spelling():
    """allowed_tools의 항목은 mcp__<서버 키>__<도구 이름>이어야 한다 —
    다른 표기는 조용히 승인 대기로 남는다(claude_driver.py:419-422)."""
    assert BUILD_COMPLETE_TOOL == f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"


async def test_complete_emits_a_build_complete_event(tmp_path):
    _prototype_dir(tmp_path) .joinpath("index.html").write_text("<h1>hi</h1>")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"})

    assert len(seen) == 1
    ev = seen[0]
    assert ev.kind == "build_complete"
    payload = json.loads(ev.payload)
    assert payload == {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"}


async def test_remaining_is_optional(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "완성"})

    assert json.loads(seen[0].payload)["remaining"] == ""


async def test_completion_is_refused_when_prototype_dir_is_missing(tmp_path):
    """도구가 거짓을 선언할 수 없게 막는다 — submit_document와 같은 규율
    (agent/tools.py). 반환 문자열은 에이전트가 읽고 스스로 고칠 수 있어야 한다."""
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)   # prototype/ 없음

    result = await handler({"summary": "다 했다"})

    assert seen == []                            # 이벤트가 나가지 않는다
    text = result["content"][0]["text"]
    assert "prototype/" in text


async def test_completion_is_refused_when_prototype_dir_is_empty(tmp_path):
    _prototype_dir(tmp_path)                     # 만들지만 비어 있다
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    result = await handler({"summary": "다 했다"})

    assert seen == []
    assert "prototype/" in result["content"][0]["text"]


async def test_a_successful_completion_returns_text_for_the_agent(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    handler = _handler(tmp_path, lambda ev: None)

    result = await handler({"summary": "완성"})

    assert result["content"][0]["type"] == "text"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.proto.tools'`

- [ ] **Step 3: 최소 구현을 쓴다**

`backend/pathfinder/proto/tools.py` 생성:

```python
# backend/pathfinder/proto/tools.py — 프로토타입 빌더의 커스텀 MCP 도구.
#
# 하나뿐이다: build_complete. 파일 조작과 질문은 SDK 내장 도구가 담당한다
# (Write/Edit/AskUserQuestion). 이것만 자작하는 이유는 Discovery의
# report_stage와 같다 — "빌드가 끝났다"는 사실은 모델의 명시적 선언이 있어야
# 신뢰할 수 있다. 산출물 존재나 done 이벤트에서 역추론하면 빌드 중간 턴을
# 완료로 오판한다(done은 "이 턴이 끝났다"는 뜻일 뿐이다).
#
# 이 선언이 세션의 수명을 끝낸다: proto/session.py가 이 이벤트를 관찰해
# status를 "complete"로 바꾸고 handoff.json을 쓴 뒤 유휴 타이머로 세션을
# 닫는다. 그래서 도구가 거짓을 선언할 수 없어야 하고, 아래 산출물 검증이
# 그것을 막는다.
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from pathfinder.models import AgentEvent

_log = logging.getLogger("pathfinder.proto")

#: Discovery의 "pathfinder"와 구분되는 값 — 두 드라이버는 서로 다른 도구
#: 집합을 노출한다. 같은 이름을 쓰면 어느 쪽 도구가 붙었는지 로그에서
#: 구분되지 않는다.
PROTO_MCP_SERVER_NAME = "pathfinder_proto"

#: allowed_tools에 넣을 정규 이름. SDK가 --mcp-config를 직렬화할 때 이
#: 형태로 이름을 만들므로, 다른 표기는 조용히 승인 대기로 남는다
#: (agent/claude_driver.py:419-422의 같은 지적).
BUILD_COMPLETE_TOOL = f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"

# 명시적 JSON Schema를 쓴다. @tool의 dict 숏컷({"key": type})은 모든 키를
# required로 만들어(create_sdk_mcp_server._build_schema) remaining을 생략할
# 수 없게 된다 — agent/tools.py:32-41이 같은 이유로 같은 선택을 했다.
_BUILD_COMPLETE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "remaining": {"type": "string"},
    },
    "required": ["summary"],
}


def _text_result(text: str) -> dict[str, Any]:
    """@tool 핸들러의 반환 계약 — create_sdk_mcp_server.call_tool이 이 형태를
    CallToolResult로 변환한다."""
    return {"content": [{"type": "text", "text": text}]}


def _has_output(workspace: str) -> bool:
    """prototype/ 아래에 무엇이든 있는가.

    _local_build_exists(routes/prototypes.py:155-170)와 같은 기준을 쓴다:
    직속 자식 하나라도 있으면 참, 재귀 스캔은 하지 않는다(node_modules가
    생긴 뒤에도 싸게 유지된다). 두 곳이 다른 기준을 쓰면 도구는 완료를
    받아들이는데 목록은 built로 보이지 않는(또는 그 반대) 상태가 된다.
    """
    proto_dir = Path(workspace) / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None]) -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 SdkMcpTool 리스트.

    Discovery의 build_tools와 같은 계약이다 — 이 리스트 자체는
    ClaudeAgentOptions에 바로 넣을 수 없고, 호출부(proto/builder.py)가
    create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME, tools=...)로 감싼다.
    """

    @tool("build_complete",
          "프로토타입 빌드가 완료되었음을 선언한다. **prototype/ 아래에 실제 "
          "산출물을 만든 뒤** 호출해야 한다 — 비어 있으면 선언이 거부된다. "
          "이 선언 뒤 빌드 세션이 종료되므로, 아직 작업이 남았으면 호출하지 마라.",
          _BUILD_COMPLETE_SCHEMA)
    async def build_complete(args: dict[str, Any]) -> dict[str, Any]:
        summary = args["summary"]
        remaining = args.get("remaining", "")

        # 이 이벤트가 세션을 끝낸다. 산출물 없이 선언되면 사용자는 "빌드
        # 완료" 카드를 보는데 호스팅할 것이 없다 — submit_document가 파일
        # 존재를 확인하는 것과 같은 이유로 여기서 막는다. 반환 문자열은
        # 에이전트가 읽고 스스로 고칠 수 있도록 무엇을 해야 하는지 알려준다.
        if not _has_output(workspace):
            _log.warning("build_complete refused: prototype/ is empty (%s)",
                         workspace)
            return _text_result(
                "거부됨 — 작업 디렉토리의 `prototype/` 아래에 산출물이 없다. "
                "완성물을 `prototype/`에 쓴 뒤 다시 선언해라.")

        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
        return _text_result("빌드 완료가 기록되었다. 세션을 종료한다.")

    return [build_complete]
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_tools.py -v`
Expected: **아직 PASS하지 않는다.** `test_tool_name_constant_matches_the_sdk_spelling`만
통과하고, 이벤트를 만드는 나머지 5개는 pydantic Literal 검증에서 실패한다:

```
ValidationError: 1 validation error for AgentEvent
kind
  Input should be 'message', ..., 'done' or 'error' [type=literal_error, input_value='build_complete']
```

`kind="build_complete"`가 아직 `models.py`에 없기 때문이다 — 이것이 정확히
다음 스텝이 고치는 것이다. 이 실패 메시지를 확인한 뒤 Step 5로 넘어간다.

- [ ] **Step 5: `AgentEvent.kind` Literal에 추가한다**

`backend/pathfinder/models.py:60-61` 수정:

```python
class AgentEvent(BaseModel):
    kind: Literal["message", "questions", "stage", "document",
                  "file_changed", "status", "done", "error",
                  # 프로토타입 빌드의 명시적 완료 선언(proto/tools.py). 이
                  # 이벤트가 세션의 수명을 끝낸다 — proto/session.py가
                  # 관찰해 status를 "complete"로 바꾼다.
                  "build_complete"]
```

- [ ] **Step 6: 테스트를 다시 돌린다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_tools.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: 회귀가 없는지 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 모두 PASS

- [ ] **Step 8: 커밋**

```bash
git add backend/pathfinder/proto/tools.py backend/pathfinder/models.py backend/tests/test_proto_tools.py
git commit -m "feat(proto): 빌드 완료를 선언하는 build_complete 도구

에이전트가 명시적으로 선언해야 완료로 인정한다 — done 이벤트는 '이 턴이
끝났다'는 뜻일 뿐이어서 빌드 중간 턴과 구분되지 않는다. prototype/이 비어
있으면 거부해 도구가 거짓을 선언하지 못하게 막는다."
```

---

## Task 2: 프론트엔드 타입 확장

**Files:**
- Modify: `frontend/lib/api/types.ts:57-65` (`AgentEventKind`), 그리고 `QuestionsPayload` 인근에 `BuildCompletePayload` 추가
- Test: `frontend/lib/api/types.test.ts` (신규 — 타입 전용 테스트)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `AgentEventKind`에 `"build_complete"` 추가
  - `export interface BuildCompletePayload { summary: string; remaining: string }`
    — Task 8의 `usePrototypeStream`, Task 9의 `BuildPanel`이 쓴다.

`remaining`은 **옵셔널이 아니다.** 백엔드가 항상 채워 보낸다(Task 1의 `args.get("remaining", "")`), 그래서 프론트는 빈 문자열만 다루면 되고 `undefined` 분기가 필요 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/api/types.test.ts` 생성:

```typescript
// frontend/lib/api/types.test.ts — 이벤트 계약이 백엔드와 어긋나지 않게
// 붙잡는 타입 테스트. AgentEvent.kind는 백엔드 models.py의 Literal과
// 한 쌍이므로, 한쪽만 바뀌면 런타임에 조용히 무시되는 이벤트가 생긴다.
import { describe, it, expect } from "vitest";
import type { AgentEventKind, BuildCompletePayload } from "./types";

describe("AgentEventKind", () => {
  it("build_complete를 포함한다", () => {
    const kind: AgentEventKind = "build_complete";
    expect(kind).toBe("build_complete");
  });
});

describe("BuildCompletePayload", () => {
  it("summary와 remaining을 가진다", () => {
    const payload: BuildCompletePayload = { summary: "만들었다", remaining: "" };
    expect(payload.summary).toBe("만들었다");
    expect(payload.remaining).toBe("");
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/api/types.test.ts`
Expected: FAIL — `"build_complete"` is not assignable to `AgentEventKind`, `BuildCompletePayload` 없음

- [ ] **Step 3: 타입을 추가한다**

`frontend/lib/api/types.ts` 수정 — `AgentEventKind`에 항목 추가:

```typescript
export type AgentEventKind =
  | "message"
  | "questions"
  | "stage"
  | "document"
  | "file_changed"
  | "status"
  | "done"
  // 프로토타입 빌드의 완료 선언. 백엔드 models.py의 Literal과 한 쌍이다.
  | "build_complete"
  | "error";
```

`QuestionsPayload` 정의 뒤에 추가:

```typescript
// build_complete 이벤트의 payload. remaining은 옵셔널이 아니다 — 백엔드가
// 항상 채워 보내므로(proto/tools.py의 args.get("remaining", "")) 프론트는
// 빈 문자열만 다루면 되고 undefined 분기가 필요 없다.
export interface BuildCompletePayload {
  summary: string;
  remaining: string;
}
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/api/types.test.ts`
Expected: PASS (2 tests)

- [ ] **Step 5: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 6: 커밋**

```bash
git add frontend/lib/api/types.ts frontend/lib/api/types.test.ts
git commit -m "feat(proto): build_complete 이벤트 타입

AgentEvent.kind는 백엔드 models.py의 Literal과 한 쌍이라 한쪽만 바꾸면
이벤트가 조용히 무시된다. 타입 테스트로 그 쌍을 붙잡는다."
```

---

## Task 3: 빌더에 MCP 도구를 배선한다

**Files:**
- Modify: `backend/pathfinder/proto/builder.py:153-207` (`_default_client_factory`)
- Test: `backend/tests/test_proto_builder.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1의 `PROTO_MCP_SERVER_NAME`, `BUILD_COMPLETE_TOOL`, `build_proto_tools`
- Produces: 빌더가 `build_complete` 이벤트를 큐에 넣을 수 있다. Task 4의 `PrototypeSession`이 그 이벤트를 관찰한다.

**중요:** 도구의 `emit`은 `self._queue.append`다 — `_on_post_tool_use`가 `file_changed`를 넣는 것과 같은 경로(`builder.py:462-472`). 그래야 `_relay_queue`의 소유권 규율(배달 후 pop)을 그대로 받는다. `workspace`는 이미 `self._workspace`에 있으므로 생성자 인자를 추가하지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_proto_builder.py` 끝에 추가:

```python
# ---- build_complete MCP 도구 배선 ----

def test_mcp_server_and_allowed_tools_are_wired(tmp_path, monkeypatch):
    """_default_client_factory가 MCP 서버와 allowed_tools를 실제로 넘기는지.

    client_factory를 주입하는 다른 테스트들은 이 경로를 전혀 타지 않으므로,
    배선이 빠져도 그 테스트들은 전부 통과한다 — 그래서 옵션을 직접 붙잡는다.
    """
    from pathfinder.proto.builder import _default_client_factory
    from pathfinder.proto.tools import BUILD_COMPLETE_TOOL, PROTO_MCP_SERVER_NAME

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)

    b = PrototypeBuilder(
        workspace=str(tmp_path), config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555", resume=False)
    _default_client_factory(b)()

    options = captured["options"]
    assert PROTO_MCP_SERVER_NAME in options.mcp_servers
    assert BUILD_COMPLETE_TOOL in options.allowed_tools
    # skills="all"이 살아 있어야 한다 — SDK가 allowed_tools를 복사한 뒤
    # "Skill"을 덧붙이므로(subprocess_cli.py:434-452) 공존한다. shadcn-design
    # 스킬이 이 값에 달려 있다.
    assert options.skills == "all"


async def test_the_tool_queues_a_build_complete_event(tmp_path):
    """도구의 emit이 빌더 큐로 가는지 — _on_post_tool_use와 같은 경로여야
    _relay_queue의 소유권 규율(배달 후 pop)을 받는다."""
    from pathfinder.proto.builder import _proto_tools_for

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "index.html").write_text("x")

    b = _builder(tmp_path, FakeSdkClient(script=[]))
    handler = {t.name: t.handler for t in _proto_tools_for(b)}["build_complete"]

    await handler({"summary": "만들었다"})

    assert [e.kind for e in b._queue] == ["build_complete"]


async def test_a_queued_completion_is_relayed_before_the_terminal_done(tmp_path):
    """build_complete가 done보다 먼저 나가는지 — 진짜 run()으로 확인한다.

    proto/session.py의 done 가드와 유예 타이머가 이 순서에 의존한다. 세션
    테스트는 FakeBuilder가 스크립트 순서대로 내보내므로 이 규율을 검증하지
    못한다 -- run()이 terminal 이벤트를 held하고 큐를 먼저 비우기 때문에
    성립하는 것이고(builder.py의 call site 4), 그 규율을 되돌리면 여기가
    먼저 실패해야 한다.

    sse.ts가 done에서 EventSource를 닫으므로, 순서가 뒤집히면 완료 이벤트가
    클라이언트에 닿지 않고 완료 카드가 영원히 뜨지 않는다.
    """
    from pathfinder.proto.builder import _proto_tools_for

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "index.html").write_text("x")

    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    # 턴이 시작되기 전에 도구가 호출된 것처럼 큐에 넣는다 — 실제로는
    # ResultMessage 직전에 호출된다.
    handler = {t.name: t.handler for t in _proto_tools_for(b)}["build_complete"]
    await handler({"summary": "만들었다"})

    events = await collect(b)

    kinds = [e.kind for e in events]
    assert "build_complete" in kinds
    assert kinds.index("build_complete") < kinds.index("done")
    assert kinds[-1] == "done"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_builder.py -k "mcp_server or build_complete or relayed_before" -v`
Expected: FAIL — `ImportError: cannot import name '_proto_tools_for'`, 그리고 `options.mcp_servers`가 없거나 비어 있다

- [ ] **Step 3: 배선을 추가한다**

`backend/pathfinder/proto/builder.py` — `_default_client_factory` 위에 헬퍼를 추가:

```python
def _proto_tools_for(builder: "PrototypeBuilder") -> list:
    """빌더의 큐에 바인딩된 커스텀 도구.

    emit이 `self._queue.append`인 것이 핵심이다 — `_on_post_tool_use`가
    `file_changed`를 넣는 것과 같은 경로라, `_relay_queue`의 소유권 규율
    (배달 후 pop, 중간에 소비자가 떠나도 이벤트가 큐에 남는다)을 그대로
    받는다. 별도 경로를 만들면 그 보장을 잃는다.
    """
    from pathfinder.proto.tools import build_proto_tools
    return build_proto_tools(builder._workspace, builder._queue.append)
```

`_default_client_factory`의 `make()` 안에서 import와 옵션을 수정:

```python
    def make():
        from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient,
                                      create_sdk_mcp_server)
        from claude_agent_sdk.types import HookMatcher

        from pathfinder.proto.tools import (BUILD_COMPLETE_TOOL,
                                            PROTO_MCP_SERVER_NAME)
```

그리고 `ClaudeAgentOptions(...)` 안에서 `skills="all",` 바로 뒤에 추가:

```python
            # 빌드 완료 선언용 커스텀 도구. Discovery(claude_driver.py:423-439)와
            # 같은 형태다. allowed_tools의 항목은 반드시
            # "mcp__<서버 키>__<도구 이름>"이어야 한다 — SDK가 --mcp-config를
            # 직렬화할 때 그 이름을 만들므로, 다른 표기는 조용히 승인 대기로
            # 남는다.
            #
            # skills="all"과 충돌하지 않는다(실측): SDK의
            # _apply_skills_defaults는 allowed_tools를 복사한 뒤 "Skill"을
            # 덧붙이므로(subprocess_cli.py:434-452) shadcn-design 스킬이 그대로
            # 살아 있다.
            mcp_servers={PROTO_MCP_SERVER_NAME: create_sdk_mcp_server(
                name=PROTO_MCP_SERVER_NAME, tools=_proto_tools_for(builder))},
            allowed_tools=[BUILD_COMPLETE_TOOL],
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_builder.py -v`
Expected: PASS (신규 2개 포함, 기존 전부 유지)

- [ ] **Step 5: 프롬프트에 완료 선언 지시를 추가한다**

`backend/pathfinder/proto/session.py`의 `_plan_prompt()` — "빌드 단계에서 지킬 것(승인 후 적용):" 목록 끝에 추가:

```python
            "- 프로토타입이 완성되면 **`build_complete` 도구로 완료를 선언해줘.** "
            "무엇을 만들었는지 요약(summary)과, 남은 작업이나 알려진 한계가 있으면 "
            "remaining에 적어줘. 이 선언 뒤 빌드 세션이 종료되니, 아직 작업이 "
            "남았으면 선언하지 말고 계속 진행해줘.\n"
```

- [ ] **Step 6: 프롬프트 테스트를 추가한다**

`backend/tests/test_proto_session.py`의 프롬프트 테스트 인근에 추가:

```python
async def test_the_plan_prompt_asks_for_an_explicit_completion_declaration(tmp_path):
    """완료 선언은 도구 호출이지만, 그것을 부르라고 말하는 곳은 프롬프트뿐이다."""
    session = await _started(tmp_path)
    prompt = session.first_prompt()
    assert "build_complete" in prompt
```

- [ ] **Step 7: 테스트를 돌린다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py tests/test_proto_builder.py -v`
Expected: 모두 PASS

- [ ] **Step 8: 커밋**

```bash
git add backend/pathfinder/proto/builder.py backend/pathfinder/proto/session.py backend/tests/test_proto_builder.py backend/tests/test_proto_session.py
git commit -m "feat(proto): 빌더에 build_complete 도구 배선

도구의 emit은 빌더 큐다 — _on_post_tool_use와 같은 경로라 _relay_queue의
소유권 규율(배달 후 pop)을 그대로 받는다. skills=all과 공존하는 것을
확인했다: SDK가 allowed_tools를 복사한 뒤 Skill을 덧붙인다."
```

---

## Task 4: 세션이 완료를 관찰하고 스스로 닫힌다

**Files:**
- Modify: `backend/pathfinder/proto/session.py` — `SessionStatus`, `__init__`, `_arm_idle_timer`, `send_message`, `_handoff_key`/`_write_handoff` 추가
- Test: `backend/tests/test_proto_session.py`

**Interfaces:**
- Consumes: Task 1의 `build_complete` 이벤트(payload `{"summary","remaining"}`), Task 3의 배선
- Produces:
  - `SessionStatus`에 `"complete"` 추가
  - `PrototypeSession._completion: dict | None` — 완료 시 `{"summary","remaining"}`
  - `handoff.json`을 `prototypes/{slug}/handoff.json`에 기록 (`{"summary","remaining","completed_at"}`)
  - Task 7의 `_DEAD_STATUSES`, Task 5의 프론트가 이 상태에 의존한다

**이 태스크에 결함 세 개가 몰려 있다. 각각 스텝으로 분리해 테스트가 먼저 잡게 한다:**
1. `done`이 `status`를 `ready`로 되돌린다 → 가드
2. 지연 값을 호출자가 넘기면 `done`이 유예를 30분으로 되돌린다 → 상태에서 파생
3. `_write_handoff` 예외가 `send_message`의 `except`로 새면 완료가 실패가 된다 → 삼킨다

- [ ] **Step 1: 실패하는 테스트를 쓴다 (완료 관찰)**

`backend/tests/test_proto_session.py`에 추가. 파일 상단 상수 옆에 키를 하나 더 둔다:

```python
HANDOFF_KEY = f"prototypes/{SLUG}/handoff.json"


def _complete_event(summary="할 일 앱", remaining="다크 모드"):
    return AgentEvent(kind="build_complete", payload=json.dumps(
        {"summary": summary, "remaining": remaining}, ensure_ascii=False))


# ---- 완료 선언: 상태 전이 + handoff 기록 ----

async def test_build_complete_sets_the_complete_status(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "complete"


async def test_the_done_after_a_completion_does_not_revert_to_ready(tmp_path):
    """build_complete 다음에는 반드시 done이 온다(run()의 terminal held 규율).
    done 분기가 status를 ready로 되돌리면 _DEAD_STATUSES 기구 전체가
    무력해진다 — 호스팅이 다시 409가 되고 개선 세션을 열 수 없다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "complete"      # NOT "ready"


async def test_build_complete_writes_the_handoff(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event("할 일 앱을 만들었다", "다크 모드"),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    saved = json.loads(s3.blobs[HANDOFF_KEY])
    assert saved["summary"] == "할 일 앱을 만들었다"
    assert saved["remaining"] == "다크 모드"
    assert saved["completed_at"]            # ISO 8601 타임스탬프


async def test_the_build_complete_event_still_reaches_the_consumer(tmp_path):
    """관찰이 이벤트를 삼키면 프론트가 완료 카드를 그릴 수 없다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in events] == ["build_complete", "done"]


async def test_a_malformed_completion_payload_is_ignored(tmp_path):
    """_interrupt_id_from과 같은 fail-soft 규율 — 깨진 payload는 예외가 아니라
    무시로 강등되고, 유휴 타이머가 평소대로 정리한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="build_complete", payload="{not json"),
                    AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"        # 완료로 처리되지 않는다
    assert HANDOFF_KEY not in s3.blobs
    assert [e.kind for e in events] == ["build_complete", "done"]


async def test_a_completion_payload_without_a_summary_is_ignored(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="build_complete", payload=json.dumps({})),
                    AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    assert session.status == "ready"
    assert HANDOFF_KEY not in s3.blobs


async def test_a_handoff_write_failure_does_not_fail_the_session(tmp_path):
    """S3 실패가 완성된 빌드를 실패로 보이게 만들면 안 된다.

    _write_handoff의 예외를 삼키지 않으면 send_message의 except Exception이
    잡아 status="failed" + 슬롯 release로 간다(session.py:191-200) — "handoff
    실패에도 완료는 진행한다"는 결정과 정반대다.
    """
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    async def boom(key, content):
        if key == HANDOFF_KEY:
            raise RuntimeError("s3 down")
        return None

    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()
    s3.put = boom   # type: ignore[method-assign]

    builder.script([_complete_event(), AgentEvent(kind="done")])
    events = [ev async for ev in session.send_message("go")]

    assert session.status == "complete"                  # NOT "failed"
    assert sem.snapshot()["active_builds"] == 1          # 슬롯을 풀지 않았다
    assert [e.kind for e in events] == ["build_complete", "done"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k "complete or handoff" -v`
Expected: FAIL — `session.status`가 `"ready"`, `HANDOFF_KEY` 없음

- [ ] **Step 3: 완료 관찰을 구현한다**

`backend/pathfinder/proto/session.py` — import에 `datetime` 추가:

```python
from datetime import datetime, timezone
```

`SessionStatus` 수정:

```python
SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        # 에이전트가 build_complete로 완료를 선언한 상태.
                        # "ready"와 다른 이유: ready는 "또 다른 턴을 받을 수
                        # 있다"이고 complete는 "이 세션은 할 일을 마쳤다"다.
                        # routes/prototypes.py의 _DEAD_STATUSES가 이 구분에
                        # 달려 있다.
                        "complete",
                        "failed", "closed"]
```

`_interrupt_id_from` 옆에 파서를 추가:

```python
#: 완료 선언 뒤 세션이 스스로 닫히기까지의 유예. 0이 아닌 이유: terminal
#: 이벤트가 제너레이터 체인(_relay_queue -> run -> send_message -> gen)을
#: 빠져나갈 여유가 필요하다.
_COMPLETION_GRACE_SECONDS = 5


def _completion_from(payload: str | None) -> dict | None:
    """build_complete payload -> {"summary","remaining"} 또는 None.

    _interrupt_id_from과 같은 fail-soft 규율이다 — 깨진 payload는 예외가
    아니라 None으로 강등된다. 완료 처리가 일어나지 않으면 유휴 타이머가
    평소대로 정리하므로, 잘못 선언된 완료보다 안전한 방향이다.
    """
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary:
        return None
    remaining = data.get("remaining")
    return {"summary": summary,
            "remaining": remaining if isinstance(remaining, str) else ""}
```

`__init__`에 필드 추가 (`_pending_interrupt_id` 옆):

```python
        # 완료 선언의 내용({"summary","remaining"}) 또는 None. 두 가지를
        # 동시에 뜻한다: (1) 이 세션은 할 일을 마쳤다, (2) 유휴 타이머는
        # 짧은 유예를 써야 한다(_arm_idle_timer 참조).
        self._completion: dict | None = None
```

`_handoff_key`를 `_session_key` 옆에 추가:

```python
    def _handoff_key(self) -> str:
        return f"prototypes/{self.slug}/handoff.json"
```

`_arm_idle_timer` 수정 — **지연을 상태에서 파생시킨다**:

```python
    def _arm_idle_timer(self) -> None:
        """유휴 타이머를 재무장한다. 지연 값은 호출자가 아니라 여기서
        결정한다 -- 그것이 이 설계에서 가장 틀리기 쉬운 부분이다.

        호출자가 인자로 넘기는 형태였다면, 완료 선언이 짧은 유예로 무장한
        직후 뒤따르는 done이 기본 30분으로 되돌려 세션이 닫히지 않는다.
        build_complete 다음에는 **반드시** done이 오므로(run()의 terminal
        held 규율) 이것은 가능성이 아니라 확정된 동작이다. 지연을 상태에서
        파생시키면 그 창이 존재하지 않는다.
        """
        delay = (_COMPLETION_GRACE_SECONDS if self._completion is not None
                 else self._idle_seconds)
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(delay, self._on_idle_timeout)
```

`_write_handoff`를 `_arm_idle_timer` 뒤에 추가:

```python
    async def _write_handoff(self, completion: dict) -> None:
        """다음 세션이 읽을 핸드오프. 개선 작업이 전체 트랜스크립트를 지고
        가지 않아도 되게 하는 유일한 근거다(_resolve_session_id의 세 번째
        분기).

        completed_at은 진단용이다 -- 어느 분기를 탔는지 로그에서 읽을 수
        있게 한다.
        """
        await self._s3.put(self._handoff_key(), json.dumps({
            **completion,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))
```

`send_message`의 릴레이 루프 수정:

```python
            async for event in self._builder.run(text):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                        self.status = "waiting_input"
                elif event.kind == "build_complete":
                    completion = _completion_from(event.payload)
                    if completion is not None:
                        self._completion = completion
                        self.status = "complete"
                        # 예외를 반드시 삼킨다. 그러지 않으면 아래의
                        # `except Exception`이 잡아 status="failed" + 슬롯
                        # release로 가는데, 그것은 "handoff 실패에도 완료는
                        # 진행한다"는 결정과 정반대다. S3 실패가 완성된
                        # 빌드를 실패로 보이게 만들면 안 된다.
                        try:
                            await self._write_handoff(completion)
                        except Exception:
                            _log.exception("handoff write failed: %s/%s",
                                           self.project_id, self.slug)
                elif event.kind in ("done", "error"):
                    # 완료를 선언한 세션은 ready로 돌아가지 않는다.
                    # build_complete 다음에는 반드시 done이 오므로, 이 가드가
                    # 없으면 status가 되돌아가 _DEAD_STATUSES 기구 전체가
                    # 무력해진다(호스팅이 다시 409, 개선 세션을 열 수 없다).
                    #
                    # error도 같이 묶는 이유: 완료 선언 뒤 error가 온다면
                    # 그것도 이 세션을 ready로 만들 근거가 아니다. 완료
                    # 전이라면 종전대로 재시도 가능한 상태로 남는다.
                    if self._completion is None:
                        self.status = "ready"
                yield event
```

**주의:** 기존 코드의 `elif event.kind == "done":` / `elif event.kind == "error":` 두 분기를 위의 하나로 합친다. 두 분기 모두 `self.status = "ready"`였으므로 동작은 같다.

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k "complete or handoff" -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 유예 종료 테스트를 추가한다**

```python
# ---- 완료 선언 뒤 세션이 스스로 닫힌다 ----

async def test_a_completed_session_closes_itself(tmp_path, monkeypatch):
    """세션 종료는 백엔드가 소유한다 — 프론트가 DELETE /session을 부르는
    방식과의 차이가 요점이다. 새로고침·탭 닫기에도 슬롯이 회수된다."""
    import pathfinder.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True   # 이 세션의 슬롯
    assert sem.try_acquire() is True   # 다른 팀의 슬롯 -- 살아남아야 한다
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 1   # 다른 팀 슬롯만 남는다


async def test_the_done_after_a_completion_does_not_extend_the_grace(tmp_path, monkeypatch):
    """지연 값이 호출자 인자였다면 done이 기본 30분으로 되돌려 세션이 닫히지
    않는다. build_complete 다음에는 반드시 done이 오므로 이것은 가능성이
    아니라 확정된 동작이다 — 지연을 상태에서 파생시켜 그 창을 없앤다."""
    import pathfinder.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    # 기본 유휴는 사실상 무한 -- 세션이 닫힌다면 그것은 유예 때문이다.
    session = _session(s3, tmp_path, builder, idle_seconds=3600)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.2)

    assert session.status == "closed"


async def test_a_completed_session_releases_its_slot_exactly_once(tmp_path, monkeypatch):
    """사용자의 DELETE /session과 유예 종료가 겹쳐도 release는 한 번이다.
    BuildSemaphore.release()는 0에서 클램프할 뿐 과다 release를 감지하지
    못하므로, 두 번 풀면 다른 세션의 슬롯을 공짜로 내준다."""
    import pathfinder.proto.session as session_module
    monkeypatch.setattr(session_module, "_COMPLETION_GRACE_SECONDS", 0.05)

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is True
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    builder.script([_complete_event(), AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await session.close()          # 사용자가 먼저 닫는다
    await asyncio.sleep(0.2)       # 그 다음 유예가 만료된다

    assert sem.snapshot()["active_builds"] == 1
    assert builder.disconnect_calls == 1
```

- [ ] **Step 6: 테스트를 돌린다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -v`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/pathfinder/proto/session.py backend/tests/test_proto_session.py
git commit -m "feat(proto): 완료 선언을 관찰해 세션이 스스로 닫힌다

세 가지를 함께 막는다:
- done이 status를 ready로 되돌려 complete 상태를 지우는 것
- 지연을 호출자가 넘겨 done이 유예를 30분으로 되돌리는 것
- handoff 쓰기 실패가 send_message의 except로 새어 완료를 실패로 만드는 것

종료를 백엔드가 소유하므로 새로고침·탭 닫기에도 슬롯이 회수된다."
```

---

## Task 5: 유휴 타이머를 "마지막 생존 신호"로 재정의

**Files:**
- Modify: `backend/pathfinder/proto/session.py` — `send_message`의 릴레이 루프
- Test: `backend/tests/test_proto_session.py`

**Interfaces:**
- Consumes: Task 4의 `_arm_idle_timer`(지연을 상태에서 파생)
- Produces: 없음 (동작 변경만)

Task 4가 지연을 상태에서 파생시켰기 때문에 이 태스크가 안전하다. 순서가 반대면 이벤트별 재무장이 완료 유예를 30분으로 되돌린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# ---- 유휴 타이머: "턴 진입 이후"가 아니라 "마지막 생존 신호 이후" ----

async def test_a_long_turn_is_not_killed_while_events_still_flow(tmp_path):
    """종전 타이머는 턴 진입에서만 재무장됐다 — 30분을 넘는 빌드 턴은 진행
    중에 죽었다. 이벤트가 흐르는 동안은 살아 있어야 한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class SlowBuilder(FakeBuilder):
        async def run(self, text: str):
            self.queries.append(text)
            # 유휴 예산(0.1)보다 총 소요가 길지만, 각 간격은 그보다 짧다.
            for _ in range(4):
                await asyncio.sleep(0.06)
                yield AgentEvent(kind="status", text="working")
            yield AgentEvent(kind="done")

    builder = SlowBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.1)
    await session.start()

    events = [ev async for ev in session.send_message("go")]

    assert session.status == "ready"          # 타임아웃으로 닫히지 않았다
    assert [e.kind for e in events][-1] == "done"


async def test_the_idle_budget_restarts_when_a_question_is_relayed(tmp_path):
    """질문 카드를 띄운 채 사용자가 오래 고민하면 세션이 닫히고, 답변 제출이
    409가 됐다. 카드가 뜬 순간부터 예산이 새로 시작해야 한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.15)
    await session.start()

    await asyncio.sleep(0.1)      # 예산의 대부분을 소진한 뒤 질문이 온다
    builder.script([AgentEvent(kind="questions", payload=json.dumps(
        {"interrupt_id": "iid-1", "questions": {"questions": []}}))])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.1)      # 재무장이 없었다면 여기서 이미 닫혔다

    assert session.status == "waiting_input"
    assert builder.disconnect_calls == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k "long_turn or idle_budget" -v`
Expected: FAIL — `status == "closed"`, `disconnect_calls == 1`

- [ ] **Step 3: 이벤트별 재무장을 추가한다**

`send_message`의 릴레이 루프 — `yield event` 바로 앞에 추가:

```python
                # 생존 신호. 타이머의 의미가 "턴 진입 이후"에서 "마지막
                # 이벤트 이후"로 바뀌는 지점이다. 종전에는 30분을 넘는 빌드
                # 턴이 진행 중에 죽고, 질문 카드를 띄운 채 30분이 지나면
                # 답변 제출이 409가 됐다.
                #
                # 완료 유예를 되돌리지 않는다 -- _arm_idle_timer가 지연을
                # self._completion에서 파생시키므로, 완료 후의 done도 짧은
                # 유예를 유지한다.
                #
                # 비용: TimerHandle.cancel() + call_later 한 쌍이 빌드 한 번에
                # 수천 번 일어난다. 둘 다 힙 연산 하나짜리라 실질 비용은
                # 없지만, 이벤트마다 부르는 형태라는 점은 알고 있어야 한다.
                self._arm_idle_timer()
                yield event
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -v`
Expected: 모두 PASS — 특히 `test_a_completed_session_closes_itself`와 `test_the_done_after_a_completion_does_not_extend_the_grace`가 여전히 통과해야 한다(이벤트별 재무장이 유예를 되돌리지 않는다는 증거)

- [ ] **Step 5: 전체 백엔드 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/proto/session.py backend/tests/test_proto_session.py
git commit -m "fix(proto): 유휴 타이머를 마지막 생존 신호 기준으로

종전에는 턴 진입에서만 재무장돼 두 가지가 깨졌다: 30분을 넘는 빌드 턴이
진행 중에 죽고, 질문 카드를 띄운 채 30분이 지나면 답변 제출이 409가 됐다.
이벤트마다 재무장하면 둘 다 해소된다 — 완료 유예는 지연이 상태에서
파생되므로 되돌아가지 않는다."
```

---

## Task 6: 개선 세션 — `_resolve_session_id`의 세 번째 분기

**Files:**
- Modify: `backend/pathfinder/proto/session.py` — `_resolve_session_id`, `start`, `first_prompt`, `_handoff_prompt` 추가
- Test: `backend/tests/test_proto_session.py`

**Interfaces:**
- Consumes: Task 4의 `handoff.json`
- Produces:
  - `PromptKind = Literal["plan", "resume", "handoff"]`
  - `PrototypeSession._prompt_kind: PromptKind` — `self._resumed`를 **대체**한다
  - `_resolve_session_id() -> tuple[str, bool, PromptKind]`
  - `_handoff_prompt() -> str`

`_resumed`는 세션 내부에서만 쓰이므로(`session.py:98,157,267` 외 사용처 없음 — 확인함) 안전한 교체다. 단 `builder_factory(session_id, resume)`의 `resume` 인자는 그대로 유지된다 — SDK의 `--resume` 여부는 프롬프트 종류와 별개다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# ---- 개선 세션: handoff가 있으면 새 session_id + 요약 주입 ----

async def test_a_handoff_starts_a_fresh_session_id(tmp_path):
    """개선 작업이 전체 트랜스크립트를 지고 가지 않게 한다. 전액 resume은
    버튼 색 하나 바꾸는 요청에도 빌드 전체 맥락을 싣는다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    old_id = "99999999-8888-7777-6666-555555555555"
    s3.blobs[SESSION_KEY] = json.dumps({"session_id": old_id})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱", "remaining": "다크 모드"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [False]      # resume이 아니다
    saved = json.loads(s3.blobs[SESSION_KEY])["session_id"]
    assert saved != old_id                            # 새 id로 갈아탔다


async def test_a_handoff_is_deleted_after_it_is_consumed(tmp_path):
    """한 번 쓴 handoff가 남으면 다음 시작도 개선 프롬프트를 받아, 세션 B의
    대화를 이어받지 못한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "할 일 앱", "remaining": ""})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert HANDOFF_KEY not in s3.blobs


async def test_the_handoff_prompt_carries_the_summary(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = json.dumps(
        {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드가 남았다"})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    prompt = session.first_prompt()

    assert "할 일 앱을 만들었다" in prompt
    assert "다크 모드가 남았다" in prompt
    # 처음부터 계획하라는 지시가 아니다.
    assert "이번 턴에서는 계획만 세우고" not in prompt
    # 마음대로 시작하지 말고 물어봐야 한다.
    assert "AskUserQuestion" in prompt


async def test_a_session_that_died_without_declaring_completion_still_resumes(tmp_path):
    """완료 선언 없이 죽은 세션(유휴 타임아웃, 백엔드 재시작)은 여전히 진짜
    resume이 맞다. 두 경로는 다른 사건을 표현한다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    # handoff 없음

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]
    assert "이어서" in session.first_prompt() or "이전" in session.first_prompt()


async def test_a_malformed_handoff_falls_back_to_resume(tmp_path):
    """깨진 handoff가 개선 경로를 막아서는 안 된다 — 전액 resume은 무겁지만
    정확한 degradation이다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "99999999-8888-7777-6666-555555555555"})
    s3.blobs[HANDOFF_KEY] = "{not json"

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [True]


async def test_a_handoff_without_a_saved_session_id_still_plans(tmp_path):
    """handoff만 있고 session.json이 없는 조합(초기화 중 부분 실패 등)은
    fresh로 떨어진다 — 이어갈 세션이 애초에 없다."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[HANDOFF_KEY] = json.dumps({"summary": "뭔가", "remaining": ""})

    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()

    assert session._test_resume_calls == [False]
    assert "이번 턴에서는 계획만 세우고" in session.first_prompt()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k handoff -v`
Expected: FAIL — handoff가 있어도 `_test_resume_calls == [True]`, 요약이 프롬프트에 없음

- [ ] **Step 3: 세 번째 분기를 구현한다**

`SessionStatus` 선언 뒤에 타입 추가:

```python
#: first_prompt()가 고르는 세 가지 개시 프롬프트.
#:   plan    -- 처음부터. 계획만 세우고 빌드하지 않는다.
#:   resume  -- 완료 선언 없이 죽은 세션을 이어받는다(트랜스크립트 전액).
#:   handoff -- 완료된 빌드를 개선한다(새 세션 + 요약만).
PromptKind = Literal["plan", "resume", "handoff"]
```

`__init__`에서 `self._resumed = False`를 **교체**:

```python
        # first_prompt()가 고를 프롬프트 종류. 종전의 `_resumed` 불리언을
        # 대체한다 -- 분기가 셋이 되어 불리언으로 표현할 수 없다.
        self._prompt_kind: PromptKind = "plan"
        # handoff 분기일 때 프롬프트에 실을 내용({"summary","remaining"}).
        self._handoff: dict | None = None
```

`_resolve_session_id` 교체:

```python
    async def _resolve_session_id(self) -> tuple[str, bool, PromptKind]:
        """(session_id, resume, prompt_kind)를 돌려준다.

        세 분기가 있고, 각각 다른 사건을 표현한다:

          저장 없음          -> 새 id, resume 안 함, "plan"
          저장 있음, handoff 없음 -> 저장된 id resume, "resume"
          저장 있음 + handoff    -> 새 id, resume 안 함, "handoff"

        세 번째가 이 설계의 요점이다. 완료된 빌드를 개선할 때 전체
        트랜스크립트를 지고 가면 버튼 색 하나 바꾸는 요청에도 빌드 전체
        맥락이 실린다. 요약만 싣고 새로 시작한다.

        두 번째가 남는 이유: 완료 선언 **없이** 죽은 세션(유휴 타임아웃,
        백엔드 재시작)은 여전히 진짜 resume이 맞다. 그 세션은 할 일을
        마치지 않았고, 이어받을 맥락이 요약으로 대체될 수 없다.

        비-UUID 저장값은 없는 것으로 취급한다 -- SDK가 non-UUID resume을
        거부하므로, 레거시/손편집 값이 세션을 영구히 막지 못하게 한다.
        """
        try:
            saved = json.loads(await self._s3.get(self._session_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = None

        if not (isinstance(saved, dict) and _is_uuid(saved.get("session_id"))):
            new_id = str(uuid.uuid4())
            await self._s3.put(self._session_key(),
                               json.dumps({"session_id": new_id}))
            return new_id, False, "plan"

        handoff = await self._read_handoff()
        if handoff is None:
            return saved["session_id"], True, "resume"

        # 개선 세션: 새 id로 갈아타고 handoff를 소비한다.
        #
        # 순서가 중요하다 -- session.json 쓰기 먼저, handoff 삭제 나중.
        # 그 사이에서 실패하면 handoff가 남아 다음 시작이 다시 이 분기를
        # 타는데, session.json에는 이미 새(빈) id가 있으므로 개선
        # 프롬프트로 새로 시작한다: 같은 결과다. 반대 순서는 handoff를
        # 지운 뒤 id 쓰기가 실패하면 요약을 잃고 옛 세션을 전액 resume한다.
        # 손실 있는 방향을 피한다.
        self._handoff = handoff
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        # 단일 키 삭제에 delete_prefix를 쓴다 -- S3StoreLike에 단일 키
        # delete가 없고, 이것이 확립된 관례다(agent/pending_store.py:69,
        # survey/store.py:334).
        await self._s3.delete_prefix(self._handoff_key())
        return new_id, False, "handoff"

    async def _read_handoff(self) -> dict | None:
        """handoff.json -> {"summary","remaining"} 또는 None.

        _completion_from과 같은 fail-soft 규율이다. 깨진 handoff가 개선
        경로를 막아서는 안 된다 -- None으로 강등되면 두 번째 분기(전액
        resume)로 떨어지고, 그것은 무겁지만 정확한 degradation이다.
        """
        try:
            data = json.loads(await self._s3.get(self._handoff_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        remaining = data.get("remaining")
        return {"summary": summary,
                "remaining": remaining if isinstance(remaining, str) else ""}
```

`start()` 수정:

```python
        self._session_id, resume, self._prompt_kind = await self._resolve_session_id()
```

`first_prompt()` 교체:

```python
    def first_prompt(self) -> str:
        """자동 발화되는 개시 턴. 세 가지 모양이고 `_prompt_kind`가 고른다.

        셋 다 같은 방식으로 끝난다 -- AskUserQuestion, 그리고 대기. 그
        도구만이 permission 콜백을 우리가 가로채는 유일한 도구여서, 질문하는
        것이 턴을 멈추고 선택지를 UI에 올리는 방법이기도 하다
        (builder._on_can_use_tool -> `questions` SSE 이벤트). 그리고 이
        문구가 유일한 브레이크다: 빌더는 bypassPermissions로 돌아 Write/Edit이
        자동 승인되므로, 그냥 시작해 버리는 에이전트를 이 텍스트 밖에서 막을
        방법이 없다.

        plan    -> 계획만 세워라, 아직 빌드하지 마라.
        resume  -> 트랜스크립트와 반쯤 만든 파일이 이미 맥락에 있다. 다시
                   계획하지 말고 무엇을 이어갈지 물어라.
        handoff -> 빌드는 끝났고 맥락은 요약뿐이다. 무엇을 개선할지 물어라.
        """
        if self._prompt_kind == "handoff" and self._handoff is not None:
            return self._handoff_prompt(self._handoff)
        if self._prompt_kind == "resume":
            return self._resume_prompt()
        return self._plan_prompt()
```

`_resume_prompt` 뒤에 추가:

```python
    def _handoff_prompt(self, handoff: dict) -> str:
        """완료된 빌드를 개선하는 새 세션의 개시 턴.

        `_resume_prompt`보다도 짧다. 파일 트리를 넘기지 않는 것이 의도적이다
        -- 에이전트가 자기 파일 도구로 cwd를 읽는 편이 스냅샷보다 정확하고,
        그게 이미 스펙을 읽는 방식이다. 여기서 할 일은 이전 빌드가 무엇을
        남겼는지 알려주고, 마음대로 손대지 않게 막는 것뿐이다.
        """
        remaining = handoff.get("remaining") or "(따로 기록된 것 없음)"
        return (
            f"이 프로토타입은 이미 한 번 빌드가 완료됐다. 이번 세션은 개선 "
            f"작업이다.\n\n"
            f"이전 빌드 요약:\n{handoff['summary']}\n\n"
            f"남은 작업으로 기록된 것:\n{remaining}\n\n"
            "**아직 아무것도 수정하지 마.**\n"
            f"1. 먼저 작업 디렉토리의 `prototype/`을 살펴보고 현재 상태를 파악해줘. "
            f"필요하면 `{self._spec_key()}`도 다시 읽어줘.\n"
            "2. 그다음 **AskUserQuestion으로 이번에 무엇을 개선할지 물어보고 내 "
            "답을 기다려줘.** 위에 기록된 남은 작업을 할지, 다른 것을 할지 내가 "
            "고를 수 있게 선택지를 제시해줘.\n"
            "3. 내가 고른 뒤에 작업을 시작해줘. 개선이 끝나면 다시 "
            "`build_complete`로 완료를 선언해줘.\n"
        )
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -v`
Expected: 모두 PASS — 기존 `test_a_resumed_session_*`, `test_a_fresh_session_still_gets_the_planning_prompt`도 유지

- [ ] **Step 5: `_resumed` 잔재가 없는지 확인한다**

Run: `cd backend && grep -rn "_resumed" pathfinder/ tests/`
Expected: 출력 없음 (테스트 함수 이름의 `resumed`는 무관 — `_resumed` 속성만 확인)

- [ ] **Step 6: 전체 백엔드 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/pathfinder/proto/session.py backend/tests/test_proto_session.py
git commit -m "feat(proto): 완료된 빌드는 요약만 싣고 새 세션으로 개선한다

_resolve_session_id가 3분기가 된다. 완료 선언 없이 죽은 세션은 여전히 진짜
resume이고(그 맥락은 요약으로 대체할 수 없다), 완료된 빌드만 새 id + 요약
주입으로 갈아탄다.

session.json 쓰기 -> handoff 삭제 순서는 뒤집으면 안 된다: 반대로 하면
중간 실패가 요약을 잃는다."
```

---

## Task 7: `complete`를 죽은 상태로 취급한다 (호스팅 409 해소)

**Files:**
- Modify: `backend/pathfinder/routes/prototypes.py:122` (`_DEAD_STATUSES`)
- Test: `backend/tests/test_routes_prototypes.py`

**Interfaces:**
- Consumes: Task 4의 `status == "complete"`
- Produces: 없음 (라우트 동작 변경)

`start_host`를 건드리지 않는다. 상태 하나를 집합에 넣으면 네 곳이 동시에 옳아진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_routes_prototypes.py`에 추가:

```python
# ---- 완료된 세션은 죽은 세션이다 ----

def test_a_completed_session_does_not_block_hosting(proto_env, monkeypatch):
    """이 작업의 동기가 된 결함: 빌드가 끝나도 세션이 살아 있으면 [호스팅
    시작]이 409로 막혔다. 카드는 이미 '빌드 완료 / 호스팅 시작'을 보여준다."""
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200
    assert resp.json()["state"] == "running"


def test_a_completed_session_does_not_block_a_new_start(proto_env, monkeypatch):
    """'개선 이어서 하기'가 필요로 하는 것."""
    old = FakePrototypeSession()
    old.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = old
    app_module.s3_store_factory(PID).blobs[SPEC_KEY] = "# spec"

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    assert resp.status_code == 202


def test_a_completed_session_serves_no_stream(proto_env):
    """답할 future가 없는 세션에 스트림을 열어주면 안 된다."""
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/events?text=hi")

    assert resp.status_code == 404


def test_answers_on_a_completed_session_404(proto_env):
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/answers",
                       json={"answers": {"1": "A"}})

    assert resp.status_code == 404


def test_list_state_built_for_a_completed_session(proto_env, monkeypatch):
    """complete는 _WORKING_STATUSES에 없으므로 카드가 '빌드 중'에 고정되지
    않는다."""
    s3 = app_module.s3_store_factory(PID)
    s3.blobs[SPEC_KEY] = "# spec"
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session
    proto_dir = app_module._proto_root() / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True, exist_ok=True)
    (proto_dir / "index.html").write_text("x")

    resp = client.get(f"/projects/{PID}/prototypes")

    entry = next(p for p in resp.json()["prototypes"] if p["slug"] == SLUG)
    assert entry["state"] == "built"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -k completed -v`
Expected: FAIL — 호스팅이 409, 새 시작이 409, 스트림이 200, answers가 204

- [ ] **Step 3: `_DEAD_STATUSES`에 추가한다**

`backend/pathfinder/routes/prototypes.py:117-122` 수정:

```python
#: A session in one of these terminal states is dead: it must NOT block a new
#: start (409) and must NOT be served as an active stream (404). Keeping
#: "failed" out of this set wedged the prototype permanently — POST said
#: "already active" while GET said "no active session", so the user could
#: neither restart nor stream.
#:
#: "complete" belongs here for the same reason and fixes four routes at once:
#: the agent declared the build finished and stopped touching the build tree,
#: so POST /host must no longer 409 (the card already says 빌드 완료 —
#: "ready" is not in _WORKING_STATUSES), POST /session must be allowed so
#: "개선 이어서 하기" can open a fresh session, and /answers + /interrupt must
#: 404 because the pending-question future they would resolve is gone.
#: The session may still be in `proto_sessions` at that moment — it closes
#: itself a few seconds later via the idle timer (proto/session.py's
#: _COMPLETION_GRACE_SECONDS) — so this set, not the dict, is what makes it
#: harmless. ProtoHost.start() does not wipe the build tree (proto/host.py's
#: "NOT rmtree" note), so hosting inside that grace window is safe.
_DEAD_STATUSES = ("closed", "failed", "complete")
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -v`
Expected: 모두 PASS (신규 5개 포함, 기존 전부 유지)

- [ ] **Step 5: 전체 백엔드 회귀 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/routes/prototypes.py backend/tests/test_routes_prototypes.py
git commit -m "fix(proto): 완료된 세션이 호스팅을 막던 문제

_DEAD_STATUSES에 complete를 넣으면 네 곳이 동시에 옳아진다: POST /host가
409를 내지 않고(카드는 이미 '빌드 완료'를 보여준다), 개선 세션을 열 수
있고, answers/interrupt가 404가 된다. start_host는 건드리지 않았다."
```

---

## Task 8: 프론트 — 완료 이벤트를 상태로 받는다

**Files:**
- Modify: `frontend/lib/usePrototypeStream.ts`
- Test: `frontend/lib/usePrototypeStream.test.tsx`

**Interfaces:**
- Consumes: Task 2의 `BuildCompletePayload`, Task 4의 `build_complete` 이벤트
- Produces: `PrototypeStream`에 추가
  - `buildComplete: BuildCompletePayload | null`
  - `restartForImprovement: () => Promise<void>` — 세션을 새로 열고 개시 턴을 발화한다
  - Task 9의 `BuildPanel`이 둘 다 쓴다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/lib/usePrototypeStream.test.tsx`에 추가. 기존 테스트가 쓰는 모듈 모킹 형태를 그대로 따른다:

```typescript
describe("build_complete", () => {
  it("완료 이벤트를 buildComplete 상태로 받는다", async () => {
    const { result, emit } = renderStream();
    act(() => result.current.startBuild());

    act(() => {
      emit({
        kind: "build_complete",
        text: null,
        path: null,
        payload: JSON.stringify({ summary: "할 일 앱", remaining: "다크 모드" }),
      });
    });

    expect(result.current.buildComplete).toEqual({
      summary: "할 일 앱",
      remaining: "다크 모드",
    });
  });

  it("완료 이벤트가 streaming을 끝내지 않는다 — 뒤따르는 done이 끝낸다", () => {
    const { result, emit } = renderStream();
    act(() => result.current.startBuild());

    act(() => {
      emit({
        kind: "build_complete",
        text: null,
        path: null,
        payload: JSON.stringify({ summary: "완성", remaining: "" }),
      });
    });

    expect(result.current.streaming).toBe(true);
  });

  it("깨진 payload는 무시하고 스트림을 계속한다", () => {
    const { result, emit } = renderStream();
    act(() => result.current.startBuild());

    act(() => {
      emit({ kind: "build_complete", text: null, path: null, payload: "{not json" });
      emit({ kind: "message", text: "계속 진행", path: null, payload: null });
    });

    expect(result.current.buildComplete).toBeNull();
    const ai = result.current.items.find((it) => it.role === "ai");
    expect(ai?.text).toContain("계속 진행");
  });

  it("개선 재시작이 세션을 새로 열고 완료 상태를 비운다", async () => {
    const { result, emit } = renderStream();
    act(() => result.current.startBuild());
    act(() => {
      emit({
        kind: "build_complete",
        text: null,
        path: null,
        payload: JSON.stringify({ summary: "완성", remaining: "" }),
      });
      emit({ kind: "done", text: null, path: null, payload: null });
    });
    expect(result.current.buildComplete).not.toBeNull();

    await act(async () => {
      await result.current.restartForImprovement();
    });

    expect(startSessionMock).toHaveBeenCalledWith("proj-1", "todo-app");
    expect(result.current.buildComplete).toBeNull();
    // 개시 턴이 다시 발화된다 — 서버가 __first__를 _handoff_prompt로 치환한다.
    expect(streamMock).toHaveBeenLastCalledWith(
      "proj-1", "todo-app", "__first__", expect.anything());
  });
});
```

기존 테스트 파일에 `startSession` 모킹이 없으면 `vi.mock("@/lib/api/prototypes", ...)` 팩토리에 `startSession: startSessionMock`을 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run lib/usePrototypeStream.test.tsx`
Expected: FAIL — `result.current.buildComplete`가 `undefined`, `restartForImprovement`가 함수 아님

- [ ] **Step 3: 훅을 수정한다**

`frontend/lib/usePrototypeStream.ts`:

import에 `startSession` 추가:

```typescript
import {
  streamPrototypeEvents,
  submitPrototypeAnswers,
  interruptSession,
  startSession,
} from "@/lib/api/prototypes";
```

타입 import에 추가:

```typescript
import type { QuestionsPayload, BuildCompletePayload } from "@/lib/api/types";
```

`PrototypeStream` 인터페이스에 추가:

```typescript
export interface PrototypeStream {
  items: ChatItem[];
  streaming: boolean;
  pendingQuestions: QuestionsPayload | null;
  /** 에이전트가 빌드 완료를 선언했을 때의 요약. 이 값이 있으면 세션은 이미
   *  닫혔거나 몇 초 안에 닫힌다(백엔드가 유예 타이머로 닫는다). */
  buildComplete: BuildCompletePayload | null;
  changedPaths: string[];
  startBuild: () => void;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => Promise<void>;
  interrupt: () => Promise<void>;
  /** 완료된 빌드를 개선한다: 새 세션을 열고 개시 턴을 발화한다. 서버가
   *  `__first__`를 핸드오프 프롬프트로 치환하므로 새 API가 필요 없다. */
  restartForImprovement: () => Promise<void>;
}
```

상태 추가 (`pendingQuestions` 옆):

```typescript
  const [buildComplete, setBuildComplete] = useState<BuildCompletePayload | null>(null);
```

`applyEvent`의 `questions` 분기 뒤에 추가:

```typescript
      if (ev.kind === "build_complete") {
        // streaming을 건드리지 않는다 — 뒤따르는 `done`이 onDone으로 평소대로
        // 턴을 닫는다. 백엔드는 이 선언 뒤 유예 타이머로 세션을 닫으므로,
        // 이 시점부터 send()는 더 이상 유효하지 않다.
        const parsed = safeParse<BuildCompletePayload>(ev.payload);
        if (parsed) setBuildComplete(parsed);
        return;
      }
```

`interrupt` 뒤에 추가:

```typescript
  const restartForImprovement = useCallback(async () => {
    // 완료 선언으로 세션이 닫혔으므로 새로 열어야 한다. 백엔드의
    // _resolve_session_id가 handoff.json을 발견해 새 session_id + 요약
    // 주입으로 분기하고, `__first__` 센티넬이 그 핸드오프 프롬프트로
    // 치환된다 — 그래서 여기서 프롬프트를 만들지 않는다.
    //
    // startSession의 예외를 잡지 않는 것이 의도다. 429(동시 빌드 상한)면
    // 아래 세 줄이 실행되지 않아 완료 카드가 그대로 남고, 호출자
    // (BuildPanel.handleRestart)가 상한 메시지를 보여준다. 여기서 삼키면
    // 카드가 지워진 채 아무 일도 일어나지 않은 화면이 된다.
    await startSession(projectId, slug);
    setBuildComplete(null);
    setChangedPaths([]);
    startBuild();
  }, [projectId, slug, startBuild]);
```

반환 객체에 추가:

```typescript
  return {
    items,
    streaming,
    pendingQuestions,
    buildComplete,
    changedPaths,
    startBuild,
    send,
    submitAnswers,
    interrupt,
    restartForImprovement,
  };
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd frontend && npx vitest run lib/usePrototypeStream.test.tsx`
Expected: 모두 PASS (신규 4개 포함, 기존 전부 유지)

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/usePrototypeStream.ts frontend/lib/usePrototypeStream.test.tsx
git commit -m "feat(proto): 완료 선언을 훅 상태로 받는다

build_complete는 streaming을 건드리지 않는다 — 뒤따르는 done이 평소대로
턴을 닫는다. 개선 재시작은 startSession + __first__ 재발화만으로 되므로
새 API가 필요 없다: 서버가 handoff.json을 보고 프롬프트를 고른다."
```

---

## Task 9: 프론트 — 완료 카드

**Files:**
- Modify: `frontend/components/prototypes/BuildPanel.tsx`
- Test: `frontend/components/prototypes/BuildPanel.test.tsx`

**Interfaces:**
- Consumes: Task 8의 `buildComplete`, `restartForImprovement`; 기존 `startHost`, `closeSession`
- Produces: 없음 (최종 UI)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/components/prototypes/BuildPanel.test.tsx`에 추가:

```typescript
describe("완료 카드", () => {
  it("완료 선언 후 요약과 남은 작업을 보여준다", () => {
    mockStream({
      buildComplete: { summary: "할 일 앱을 만들었다", remaining: "다크 모드" },
    });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={() => {}} />);

    expect(screen.getByText(/할 일 앱을 만들었다/)).toBeInTheDocument();
    expect(screen.getByText(/다크 모드/)).toBeInTheDocument();
  });

  it("남은 작업이 비어 있으면 그 줄을 그리지 않는다", () => {
    mockStream({ buildComplete: { summary: "완성", remaining: "" } });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={() => {}} />);

    expect(screen.queryByText("남은 작업")).not.toBeInTheDocument();
  });

  it("완료 전에는 카드를 그리지 않는다", () => {
    mockStream({ buildComplete: null });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={() => {}} />);

    expect(screen.queryByRole("button", { name: "호스팅 시작" })).not.toBeInTheDocument();
  });

  it("호스팅 시작이 startHost를 부르고 패널을 닫는다", async () => {
    const onClose = vi.fn();
    mockStream({ buildComplete: { summary: "완성", remaining: "" } });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "호스팅 시작" }));

    expect(startHostMock).toHaveBeenCalledWith("proj-1", "todo-app");
    expect(onClose).toHaveBeenCalled();
  });

  it("호스팅이 실패하면 패널을 닫지 않고 오류를 보여준다", async () => {
    const onClose = vi.fn();
    startHostMock.mockRejectedValueOnce(new Error("npm error"));
    mockStream({ buildComplete: { summary: "완성", remaining: "" } });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "호스팅 시작" }));

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/호스팅을 시작하지 못했습니다/)).toBeInTheDocument();
  });

  it("개선 이어서 하기가 restartForImprovement를 부른다", async () => {
    const restart = vi.fn().mockResolvedValue(undefined);
    mockStream({
      buildComplete: { summary: "완성", remaining: "" },
      restartForImprovement: restart,
    });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={() => {}} />);

    await userEvent.click(screen.getByRole("button", { name: "개선 이어서 하기" }));

    expect(restart).toHaveBeenCalled();
  });

  it("개선 시작이 429면 상한 메시지를 보여주고 카드를 남긴다", async () => {
    // 동시 빌드 상한에 걸린 경우. 카드를 지우면 사용자는 완료 요약과 다른
    // 선택지(호스팅)를 모두 잃는다 — 재시도할 수 있게 남긴다.
    const restart = vi.fn().mockRejectedValueOnce(
      new ApiError(429, "다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요"));
    mockStream({
      buildComplete: { summary: "완성", remaining: "" },
      restartForImprovement: restart,
    });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={() => {}} />);

    await userEvent.click(screen.getByRole("button", { name: "개선 이어서 하기" }));

    expect(screen.getByText(/다른 팀이 프로토타입을 빌드하고 있습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "호스팅 시작" })).toBeInTheDocument();
  });

  it("완료 후 닫기는 세션이 이미 닫혀 404여도 패널을 닫는다", async () => {
    const onClose = vi.fn();
    closeSessionMock.mockRejectedValueOnce(new ApiError(404, "no build session"));
    mockStream({ buildComplete: { summary: "완성", remaining: "" } });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "닫기" }));

    expect(onClose).toHaveBeenCalled();
  });
});
```

기존 테스트가 `usePrototypeStream`을 모킹하는 형태(`mockStream` 헬퍼가 없다면 만든다)에 맞추고, `startHost`/`closeSession`/`ApiError`를 `@/lib/api/prototypes`·`@/lib/api/client` 모킹에 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/prototypes/BuildPanel.test.tsx`
Expected: FAIL — 완료 카드와 버튼들이 없음

- [ ] **Step 3: 완료 카드를 구현한다**

`frontend/components/prototypes/BuildPanel.tsx`:

import 수정:

```typescript
import { closeSession, startHost } from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
```

훅 구조분해에 추가:

```typescript
  const {
    items, streaming, pendingQuestions, buildComplete, changedPaths,
    startBuild, send, submitAnswers, interrupt, restartForImprovement,
  } = usePrototypeStream(projectId, slug);
  const [closing, setClosing] = useState(false);
  const [submittingAnswers, setSubmittingAnswers] = useState(false);
  const [hosting, setHosting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
```

`handleDone` 수정 — 404를 흡수한다:

```typescript
  async function handleDone() {
    setClosing(true);
    try {
      await closeSession(projectId, slug);
      onClose();
    } catch (err) {
      // 404는 정상 경로다: 완료 선언 뒤 백엔드가 유예 타이머로 세션을 먼저
      // 닫는다(proto/session.py의 _COMPLETION_GRACE_SECONDS). 이미 없는
      // 세션을 못 닫았다고 패널을 붙잡아 둘 이유가 없다.
      if (err instanceof ApiError && err.status === 404) {
        onClose();
        return;
      }
      throw err;
    } finally {
      setClosing(false);
    }
  }
```

핸들러 두 개 추가:

```typescript
  async function handleStartHost() {
    setHosting(true);
    setActionError(null);
    try {
      await startHost(projectId, slug);
      onClose();
    } catch (err) {
      // 패널을 닫지 않는다 — 닫으면 사용자는 그리드에서 이유 없이 실패한
      // 카드를 보게 된다. 여기서 오류를 보여주고 재시도할 수 있게 둔다.
      setActionError(
        err instanceof ApiError && err.message
          ? err.message
          : "호스팅을 시작하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setHosting(false);
    }
  }

  async function handleRestart() {
    setRestarting(true);
    setActionError(null);
    try {
      await restartForImprovement();
    } catch (err) {
      // 429(동시 빌드 상한)가 실제로 도달 가능한 경로다. 카드를 지우지
      // 않는다 — 지우면 사용자는 완료 요약과 호스팅 선택지를 모두 잃는다.
      // actionError를 호스팅과 공유한다: 이 카드에 오류 줄은 하나뿐이고, 두
      // 동작이 동시에 실패할 수는 없다(둘 다 서로를 disabled로 막는다).
      setActionError(
        err instanceof ApiError && err.message
          ? err.message
          : "개선 세션을 시작하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setRestarting(false);
    }
  }
```

`aside` 안, `pendingQuestions` 블록 **앞**에 완료 카드를 넣는다 (완료와 질문이 동시에 있을 수 없지만, 완료가 더 최신 사건이므로 위에 둔다):

```tsx
            {buildComplete && (
              <div className="p-4 border-b border-slate-200">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                  <p className="text-sm font-bold text-emerald-800">빌드 완료</p>
                  <p className="mt-2 text-sm text-slate-700 whitespace-pre-wrap">
                    {buildComplete.summary}
                  </p>
                  {buildComplete.remaining && (
                    <>
                      <p className="mt-3 text-xs font-bold text-slate-500">남은 작업</p>
                      <p className="mt-1 text-sm text-slate-600 whitespace-pre-wrap">
                        {buildComplete.remaining}
                      </p>
                    </>
                  )}
                </div>
                {actionError && (
                  <p className="mt-3 text-sm text-rose-600">{actionError}</p>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleStartHost()}
                    disabled={hosting || restarting || closing}
                    className="px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-medium"
                  >
                    호스팅 시작
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleRestart()}
                    disabled={hosting || restarting || closing}
                    className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 text-sm font-medium text-slate-700"
                  >
                    개선 이어서 하기
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDone()}
                    disabled={hosting || restarting || closing}
                    className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 text-sm font-medium text-slate-700"
                  >
                    닫기
                  </button>
                </div>
              </div>
            )}
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd frontend && npx vitest run components/prototypes/BuildPanel.test.tsx`
Expected: 모두 PASS (신규 7개 포함, 기존 전부 유지)

- [ ] **Step 5: 전체 프론트 확인**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx next lint`
Expected: 모두 통과

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/prototypes/BuildPanel.tsx frontend/components/prototypes/BuildPanel.test.tsx
git commit -m "feat(proto): 빌드 완료 카드 + 다음 행동 분기

완료 선언 후 요약·남은 작업을 보여주고 [호스팅 시작] [개선 이어서 하기]
[닫기]로 분기한다. 호스팅 실패는 패널을 닫지 않고 오류를 보여준다 — 닫으면
사용자가 그리드에서 이유 없이 실패한 카드를 본다. 닫기는 404를 정상으로
흡수한다(백엔드가 유예 타이머로 먼저 닫는다)."
```

---

## Task 10: README 현행화 + 전체 검증

**Files:**
- Modify: `README.md:8-15` (프로토타입 탭 설명), `README.md:268` 인근(환경변수 표)

**Interfaces:**
- Consumes: Task 1-9 전부
- Produces: 없음

- [ ] **Step 1: README의 세션 수명 서술을 고친다**

`README.md`의 프로토타입 설명 문단(9-15행) — "빌드 트랜스크립트는 S3로 미러링되므로..." 문장 뒤에 추가:

```markdown
빌드 세션의 수명은 **빌드 1회**다: 에이전트가 완성물을 만들고 `build_complete`
도구로 완료를 선언하면 세션이 스스로 닫혀 서브프로세스와 빌드 슬롯을 즉시
반납한다. 그 시점에 빌드 드로어는 완료 카드로 바뀌어 호스팅 시작·개선 이어서
하기·닫기로 분기한다. "개선"은 전체 트랜스크립트를 다시 싣지 않고 새 세션에
이전 빌드 요약(`handoff.json`)만 주입한다 — 버튼 색 하나 바꾸는 요청이 빌드
전체 맥락을 지고 가지 않게 한다. 완료 선언 없이 세션이 죽으면(유휴 타임아웃,
백엔드 재시작) 종전처럼 트랜스크립트를 `resume`한다.
```

- [ ] **Step 2: 유휴 타이머 서술을 확인한다**

Run: `grep -n "30분\|유휴\|idle" README.md`

유휴 타임아웃을 "턴 진입 기준"으로 설명하는 문장이 있으면 "마지막 이벤트 기준"으로 고친다. 없으면 이 스텝은 변경 없이 넘어간다.

- [ ] **Step 3: 백엔드 전체 테스트**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 모두 PASS

- [ ] **Step 4: 프론트엔드 전체 검증**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx next lint`
Expected: 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add README.md
git commit -m "docs: 프로토타입 빌드 세션 수명 현행화

세션이 완료 선언 시 스스로 닫히고, 개선은 요약만 실은 새 세션으로
분기한다는 사실을 반영한다."
```

---

## 수동 e2e 검증

자동 테스트가 덮지 못하는 것: 실제 에이전트가 `build_complete`를 부르는지. 도구
배선과 프롬프트는 단위 테스트로 확인했지만, 모델이 실제로 그 도구를 호출하는지는
실행해 봐야 안다.

`docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md`에 항목을 추가한다:

- [ ] 빌드를 끝까지 진행해 에이전트가 `build_complete`를 호출하는지 확인
- [ ] 완료 카드가 요약과 함께 뜨는지
- [ ] 카드에서 [호스팅 시작]이 409 없이 성공하는지 (이 작업의 동기)
- [ ] 완료 후 목록의 `active_builds`가 줄어드는지 (유예 후)
- [ ] 완료 직후 브라우저를 새로고침해도 `active_builds`가 회수되는지
      (백엔드가 종료를 소유한다는 것의 증거)
- [ ] [개선 이어서 하기]가 이전 요약을 언급하며 무엇을 개선할지 묻는지
- [ ] 개선 세션이 다시 `build_complete`로 끝나는지
- [ ] 30분을 넘는 빌드가 진행 중에 죽지 않는지 (긴 프로토타입으로 확인)
