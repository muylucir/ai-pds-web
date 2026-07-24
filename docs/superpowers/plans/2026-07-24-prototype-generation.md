# 프로토타입 생성 (MicroVM Agent SDK 빌드 + EC2 호스팅) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discovery가 산출한 `PROTOTYPE-{slug}.md` 스펙으로부터, Tokyo MicroVM 안의 Claude Agent SDK가 프로토타입을 대화형으로 빌드하고, 결과 번들을 S3에 영속화한 뒤 Pathfinder EC2에서 라이브 프리뷰로 호스팅한다.

**Architecture:** 백엔드(서울 EC2)가 Tokyo `lambda-microvms` VM을 세션 단위로 부팅하고, VM 안의 harness 서버(8080/9000)가 `ClaudeSDKClient`(Bedrock 인증)로 빌드 턴을 실행해 AgentEvent SSE로 중계한다. 세션 종료 시 `/workspace/prototype/`을 S3 번들로 pull하고, `ProtoHost`가 그 번들을 EC2 로컬 서브프로세스(포트 4001+)로 기동해 `/api/proto/{pid}/{slug}/*` 경로 프록시로 노출한다. 스펙: `docs/superpowers/specs/2026-07-24-prototype-generation-design.md`.

**Tech Stack:** Python 3.11 (FastAPI/Starlette, httpx, sse-starlette), `claude-agent-sdk`(정확 버전 핀, CLI 바이너리 번들), boto3 `lambda-microvms`, Next.js 14 (App Router, Tailwind), AWS CDK 2.261 (`CfnMicrovmImage` L1), pytest + Vitest/MSW.

## Global Constraints

- **모델 ID:** `global.anthropic.claude-opus-4-8` (infra `backend-permissions.ts`의 `MODEL` 상수 재사용).
- **VM 리전 Tokyo 고정:** `ap-northeast-1` (lambda-microvms 제공 리전). 백엔드/S3는 기존 서울(`ap-northeast-2`).
- **claude-agent-sdk 버전 핀:** `harness/requirements.txt`에 `claude-agent-sdk==0.2.126` (구현 시점 최신으로 갱신 가능하되 반드시 `==` 핀).
- **harness는 non-root:** Dockerfile의 `useradd -m -u 10001 harness` + `USER harness` 유지 — root는 `bypassPermissions` 거부(git 6d21e1f 학습 사항).
- **AgentEvent 계약 무변경:** `pathfinder/models.py`의 `AgentEvent(kind, text, path, payload)` 8종 kind 그대로 재사용. 프론트 `lib/api/types.ts`도 무변경.
- **VM 실행 롤에 S3 권한 없음:** 파일은 백엔드가 HTTP로 중개 (스펙 §5).
- **stderr/에러 상세는 서버 로그만:** SSE에는 sanitize된 `error` 이벤트만.
- **유휴 타임아웃:** 빌드 세션 30분 (진행 중 턴·질문 응답은 타이머 리셋).
- **호스팅 포트:** 4001부터 순차 스캔. systemd 등록 없음(백엔드 재시작 시 수동 재기동).
- **커밋 메시지 말미:** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## File Structure

```
harness/                                  # 부활 + SDK 드라이버 (Task 1-3)
  serve.py            # 8080 app + 9000 hooks 이중 스레드 (510fc66^에서 부활·축소)
  app.py              # 턴 HTTP API + /interrupt (510fc66^에서 부활·확장)
  sdk_driver.py       # 신규 — ClaudeSDKClient 래핑
  hooks.py            # /ready·/health (부활, 진단 교체)
  events.py           # AgentEvent (backend models.py와 동일 셰이프, VM 안 단독 사용)
  globmatch.py        # 510fc66^에서 부활 (files API glob)
  pathsafe.py         # 경로 이스케이프 가드 (backend/pathfinder/pathsafe.py 복사)
  requirements.txt    # starlette, sse-starlette, httpx, uvicorn, pydantic, claude-agent-sdk==
  Dockerfile          # al2023 + python3.11 + nodejs + non-root
  tests/              # fake SDK client 기반 단위 테스트
backend/pathfinder/proto/                 # 신규 패키지 (Task 4-6)
  __init__.py
  vm.py               # LambdaMicroVMController 부활 (suspend/resume 제외)
  harness_client.py   # HarnessClient 부활 + interrupt 추가
  session.py          # PrototypeSession
  host.py             # ProtoHost
backend/pathfinder/routes/prototypes.py   # REST + SSE + 리버스 프록시 (Task 7)
frontend/app/projects/[projectId]/prototypes/page.tsx  # 프로토타입 탭 (Task 9)
frontend/components/prototypes/           # 카드·빌드 채팅 패널 (Task 8-9)
frontend/lib/api/prototypes.ts            # API 클라이언트 (Task 8)
infra/lib/pathfinder-drill-stack.ts       # MicroVM 이미지·롤 부활 (Task 10)
infra/package-harness.sh                  # 부활 (Task 10)
```

**Task 분할:** 1(harness 골격 부활) · 2(sdk_driver 턴+번역) · 3(sdk_driver 질문·중단 + Dockerfile) · 4(VM 컨트롤러·HarnessClient 부활) · 5(PrototypeSession) · 6(ProtoHost) · 7(백엔드 라우트+프록시) · 8(프론트 API 클라이언트+카드) · 9(프론트 빌드 패널+탭) · 10(인프라 CDK) · 11(수동 e2e 체크리스트 문서).

Task 1→2→3은 순차. Task 4→5는 순차. Task 6·8은 독립. Task 7은 5·6 이후. Task 9는 7·8 이후. Task 10·11은 마지막.

---

### Task 1: harness 골격 부활 (serve/app/hooks/events/globmatch)

git `510fc66^`의 harness 서버 골격을 부활시키되 Strands 관련을 제거하고 `/interrupt` 라우트를 추가한다. 드라이버는 아직 없으므로 이 태스크는 **프로토콜 계약(FakeDriver) 기준으로 테스트**한다.

**Files:**
- Create: `harness/events.py`, `harness/globmatch.py`, `harness/pathsafe.py`, `harness/app.py`, `harness/hooks.py`, `harness/serve.py`, `harness/requirements.txt`
- Create: `harness/tests/conftest.py`, `harness/tests/fake_driver.py`, `harness/tests/test_app.py`, `harness/tests/test_hooks.py`

**Interfaces:**
- Produces: `build_app(driver, workspace: str) -> Starlette` — 라우트: `POST /message` `{text}` → SSE, `POST /answers` `{interrupt_id, answers}` → 204 (드라이버에 전달; SDK 모델에서는 열린 /message 스트림이 이어지므로 answers 자체는 스트림이 아님 — Task 3 참조), `POST /interrupt` → 202, `POST /pending` → `{"pending": str|null}`, `GET /files?glob=`, `GET/PUT /files/{path:path}`, `GET /health`
- Produces: `AgentEvent` (pydantic, kind: `message|questions|stage|document|file_changed|status|done|error`, text/path/payload) — backend `models.py:59-66`과 동일 셰이프
- Produces: driver 프로토콜 — `run(text) -> AsyncIterator[AgentEvent]`, `submit_answers(interrupt_id: str, answers: dict[str, str]) -> bool`, `interrupt() -> None`, `pending() -> str | None`
- Consumes: git `510fc66^:harness/app.py` (파일 라우트·`_resolve` 방어 로직), `510fc66^:harness/serve.py` (이중 스레드 주석 포함), `510fc66^:harness/hooks.py`, `510fc66^:harness/globmatch.py` — `git show '510fc66^':harness/app.py` 등으로 열람

- [ ] **Step 1: 부활 소스 추출**

```bash
mkdir -p harness/tests
git show '510fc66^':harness/globmatch.py > harness/globmatch.py
git show '510fc66^':harness/events.py > harness/events.py
cp backend/pathfinder/pathsafe.py harness/pathsafe.py
```

`harness/events.py`를 backend `models.py`의 현재 kind 목록과 대조해 8종(`message|questions|stage|document|file_changed|status|done|error`)이 되도록 수정. `harness/pathsafe.py`의 import가 패키지 상대(`pathfinder.`)라면 로컬 import로 수정.

- [ ] **Step 2: 실패하는 테스트 작성 — app 라우트 계약**

`harness/tests/fake_driver.py`:

```python
from events import AgentEvent

class FakeDriver:
    """Scripted driver: run() yields a fixed event list; records calls."""
    def __init__(self, events=None, pending_payload=None):
        self._events = events or [AgentEvent(kind="message", text="hi"),
                                  AgentEvent(kind="done")]
        self._pending = pending_payload
        self.interrupts = 0
        self.answers_calls: list[tuple[str, dict]] = []

    async def run(self, text):
        for ev in self._events:
            yield ev

    async def submit_answers(self, interrupt_id, answers):
        self.answers_calls.append((interrupt_id, answers))
        return True

    async def interrupt(self):
        self.interrupts += 1

    async def pending(self):
        return self._pending
```

`harness/tests/test_app.py` — httpx.ASGITransport로 각 라우트 검증:

```python
import json, pytest, httpx
from app import build_app
from tests.fake_driver import FakeDriver

@pytest.fixture
def client(tmp_path):
    driver = FakeDriver()
    app = build_app(driver, str(tmp_path))
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), driver, tmp_path

@pytest.mark.asyncio
async def test_message_streams_events(client):
    c, driver, _ = client
    async with c.stream("POST", "/message", json={"text": "build it"}) as r:
        assert r.status_code == 200
        body = "".join([chunk async for chunk in r.aiter_text()])
    assert '"kind":"message"' in body and '"kind":"done"' in body

@pytest.mark.asyncio
async def test_interrupt_returns_202_and_calls_driver(client):
    c, driver, _ = client
    r = await c.post("/interrupt")
    assert r.status_code == 202
    assert driver.interrupts == 1

@pytest.mark.asyncio
async def test_answers_forwards_to_driver(client):
    c, driver, _ = client
    r = await c.post("/answers", json={"interrupt_id": "i1", "answers": {"Q?": "A"}})
    assert r.status_code == 204
    assert driver.answers_calls == [("i1", {"Q?": "A"})]

@pytest.mark.asyncio
async def test_answers_missing_key_400(client):
    c, _, _ = client
    r = await c.post("/answers", json={"answers": {}})
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_file_roundtrip_and_escape_rejected(client):
    c, _, ws = client
    r = await c.put("/files/prototype/a.txt", content=b"hello")
    assert r.status_code == 204
    r = await c.get("/files/prototype/a.txt")
    assert r.text == "hello"
    r = await c.get("/files/../etc/passwd")
    assert r.status_code == 400
```

`harness/tests/conftest.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd harness && python3.11 -m venv .venv && .venv/bin/pip install starlette sse-starlette httpx uvicorn 'pydantic>=2.6' pytest pytest-asyncio && .venv/bin/pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: app.py 부활 + /interrupt 추가**

`git show '510fc66^':harness/app.py`를 기반으로 `harness/app.py` 작성. 유지: `_resolve` defense-in-depth 주석·로직, 파일 GET의 lossy-decode 주석, PUT의 mkdir. 변경:

```python
# harness/app.py  (port 8080 inside the MicroVM)
from __future__ import annotations
import json
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from globmatch import matches_glob


def build_app(driver, workspace: str) -> Starlette:
    ws = Path(workspace).resolve()

    async def message(request):
        body = await request.json()
        text = body["text"]
        gen_src = driver.run(text)

        async def gen():
            async for ev in gen_src:
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    async def answers(request):
        body = await request.json()
        try:
            interrupt_id = body["interrupt_id"]
            answers_map = body["answers"]
        except KeyError as exc:
            return PlainTextResponse(f"missing key: {exc.args[0]}", status_code=400)
        ok = await driver.submit_answers(interrupt_id, answers_map)
        if not ok:
            return PlainTextResponse("no pending question", status_code=409)
        return Response(status_code=204)

    async def interrupt(request):
        await driver.interrupt()
        return Response(status_code=202)

    async def pending(request):
        return JSONResponse({"pending": await driver.pending()})
    ...
```

(파일 라우트 4개·`_resolve`·health는 `510fc66^` 원본 그대로. answers가 EventSourceResponse가 아닌 204인 이유: SDK 모델에서는 답변이 **열려 있는 /message 스트림을 이어가게** 하므로 — 이벤트는 그 스트림으로 계속 흐른다.)

라우트 테이블:

```python
    return Starlette(routes=[
        Route("/message", message, methods=["POST"]),
        Route("/answers", answers, methods=["POST"]),
        Route("/interrupt", interrupt, methods=["POST"]),
        Route("/pending", pending, methods=["POST"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/files/{path:path}", get_file, methods=["GET"]),
        Route("/files/{path:path}", put_file, methods=["PUT"]),
        Route("/health", health, methods=["GET"]),
    ])
```

- [ ] **Step 5: hooks.py·serve.py 부활**

`git show '510fc66^':harness/hooks.py` 기반 `harness/hooks.py`: `strands_diagnostic` 삭제, `claude_cli_diagnostic`을 SDK 진단으로 교체(로그만, 게이트 아님 — 원본의 503-루프 학습 주석 유지):

```python
def sdk_diagnostic() -> str:
    """Diagnostic only, never a build gate (the first image build 503-looped
    /ready on a CLI gate; we only log). Confirms the claude-agent-sdk import
    and its bundled CLI binary run on this image/arch."""
    try:
        import claude_agent_sdk
        ver = getattr(claude_agent_sdk, "__version__", "?")
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        return f"claude_agent_sdk import failed {type(exc).__name__}: {exc}"
    import shutil, subprocess
    exe = shutil.which("claude")
    note = f"sdk {ver}; PATH claude={exe or 'absent (bundled binary is used)'}"
    return note
```

`default_rules_present`는 `/workspace/aiplc-rules/aws-aiplc-rule-details/discovery/prototype-building.md` 존재 확인으로 변경(이 이미지가 굽는 룰은 프로토타입 빌드 룰).

`git show '510fc66^':harness/serve.py` 기반 `harness/serve.py`: 이중 스레드 구조·주석 유지, `make_driver`는 분기 없이 `SdkDriver` 단일 (Task 2에서 생기므로 이 시점에는 import 실패 — serve.py 테스트는 make_driver를 제외한 build 함수만):

```python
def make_driver(workspace: str = WORKSPACE):
    from sdk_driver import SdkDriver
    return SdkDriver(workspace=workspace)
```

`harness/tests/test_hooks.py`:

```python
from hooks import build_hooks_app, sdk_diagnostic

def test_sdk_diagnostic_reports_import_failure_without_crashing(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "claude_agent_sdk":
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    out = sdk_diagnostic()
    assert "import failed" in out
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd harness && .venv/bin/pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 7: requirements.txt 작성 + 커밋**

`harness/requirements.txt`:

```
starlette>=0.37
sse-starlette>=2.0
httpx>=0.27
uvicorn>=0.30
pydantic>=2.6
claude-agent-sdk==0.2.126
```

```bash
git add harness/
git commit -m "feat(harness): revive server skeleton (app/serve/hooks) with /interrupt; SDK diagnostic

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: sdk_driver — 턴 실행과 이벤트 번역

`ClaudeSDKClient`를 래핑하는 `SdkDriver`. 이 태스크는 기본 턴(메시지 → 타입 객체 → AgentEvent)과 PostToolUse 파일 추적까지. 질문·중단은 Task 3.

**Files:**
- Create: `harness/sdk_driver.py`
- Create: `harness/tests/fake_sdk.py`, `harness/tests/test_sdk_driver.py`

**Interfaces:**
- Produces: `SdkDriver(workspace: str, client_factory: Callable[[], Any] | None = None)` — factory는 테스트 주입점 (현 backend `StrandsDriver(agent_factory=...)` 패턴, driver.py:120-125)
- Produces: Task 1의 driver 프로토콜 구현 — `run/submit_answers/interrupt/pending`
- Consumes: `claude_agent_sdk.ClaudeSDKClient`, `ClaudeAgentOptions`, 메시지 타입 `AssistantMessage`(content: `TextBlock|ToolUseBlock`), `ResultMessage`, 훅 `HookMatcher`
- SDK 옵션 (공식 문서 확인 사항): `permission_mode="bypassPermissions"`, `cwd=workspace`, `env={"CLAUDE_CODE_USE_BEDROCK": "1"}` (+ `ANTHROPIC_MODEL`은 VM 이미지 env로 이미 존재 — 전달 불필요), `can_use_tool` 콜백(Task 3), `hooks={"PostToolUse": [...]}`. **주의**: `can_use_tool`은 streaming mode 필요 — `ClaudeSDKClient.connect()` 후 `client.query()` 사용(프롬프트 없는 connect는 스트림을 열어 둠 — 공식 문서)

- [ ] **Step 1: fake SDK client 작성**

`harness/tests/fake_sdk.py` — 실제 SDK 타입을 import하지 않고 셰이프만 흉내(단위 테스트가 SDK 설치 없이 돌게):

```python
"""Shape-compatible stand-ins for claude_agent_sdk message types + a scripted
client. sdk_driver matches on class NAME (type(msg).__name__), not isinstance,
precisely so these fakes work without importing the real SDK."""
from dataclasses import dataclass, field

@dataclass
class TextBlock:
    text: str

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict

@dataclass
class AssistantMessage:
    content: list

@dataclass
class ResultMessage:
    subtype: str = "success"
    result: str | None = None

class FakeSdkClient:
    """Scripted ClaudeSDKClient: yields `script` from receive_response()."""
    def __init__(self, script=None):
        self.script = script or []
        self.queries: list[str] = []
        self.interrupt_calls = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for msg in self.script:
            yield msg

    async def interrupt(self):
        self.interrupt_calls += 1
```

- [ ] **Step 2: 실패하는 테스트 작성 — 번역·상태 dedupe·에러**

`harness/tests/test_sdk_driver.py`:

```python
import pytest
from sdk_driver import SdkDriver
from tests.fake_sdk import (FakeSdkClient, AssistantMessage, TextBlock,
                            ToolUseBlock, ResultMessage)

async def collect(driver, text="go"):
    return [ev async for ev in driver.run(text)]

@pytest.mark.asyncio
async def test_text_and_result_translate(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[TextBlock(text="working on it")]),
        ResultMessage(subtype="success"),
    ])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    events = await collect(d)
    kinds = [(e.kind, e.text) for e in events]
    assert ("message", "working on it") in kinds
    assert events[-1].kind == "done"
    assert client.queries == ["go"]

@pytest.mark.asyncio
async def test_tool_use_status_deduped(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[ToolUseBlock(id="1", name="Bash", input={}),
                                  ToolUseBlock(id="2", name="Bash", input={})]),
        AssistantMessage(content=[ToolUseBlock(id="3", name="Write",
                                               input={"file_path": "x"})]),
        ResultMessage(),
    ])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    events = await collect(d)
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["Bash", "Write"]

@pytest.mark.asyncio
async def test_client_error_yields_sanitized_error(tmp_path):
    class Boom(FakeSdkClient):
        async def receive_response(self):
            raise RuntimeError("AWS_SECRET=xyz leaked")
            yield  # pragma: no cover
    d = SdkDriver(str(tmp_path), client_factory=lambda: Boom())
    events = await collect(d)
    assert events[-1].kind == "error"
    assert "xyz" not in (events[-1].text or "")

@pytest.mark.asyncio
async def test_second_turn_reuses_connected_client(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    await collect(d, "one")
    await collect(d, "two")
    assert client.queries == ["one", "two"]

@pytest.mark.asyncio
async def test_turn_already_in_progress(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    d._turn_active = True
    events = await collect(d)
    assert events[0].kind == "error"
    assert "in progress" in events[0].text
```

파일 추적 훅 테스트 — 훅 콜백을 직접 호출(훅 등록 자체는 실 SDK 통합에서만 검증 가능):

```python
@pytest.mark.asyncio
async def test_post_tool_hook_emits_file_changed(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    out = await d._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    assert out == {}
    assert [e.kind for e in d.drain_queue()] == ["file_changed"]
    assert d._queue == []

@pytest.mark.asyncio
async def test_post_tool_hook_rejects_escape(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    await d._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
        "toolu_1", None)
    evs = d.drain_queue()
    assert evs[0].kind == "status" and "outside workspace" in evs[0].text
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd harness && .venv/bin/pytest tests/test_sdk_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk_driver'`

- [ ] **Step 4: sdk_driver.py 구현 (턴·번역·훅 큐)**

```python
# harness/sdk_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import collections
import logging
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Callable

from events import AgentEvent

_log = logging.getLogger("harness.sdk_driver")

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; reject escapes.
    (Ported verbatim from the old claude_driver._rel — see its docstring for
    why any `..` in the relativized parts is an escape, not merely relative.)"""
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


def _default_client_factory(workspace: str, driver: "SdkDriver") -> Callable[[], Any]:
    def make():
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        from claude_agent_sdk.types import HookMatcher
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=workspace,
            env={"CLAUDE_CODE_USE_BEDROCK": "1"},
            can_use_tool=driver._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[driver._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


class SdkDriver:
    """One build session = one connected ClaudeSDKClient (multi-turn context
    lives in the client; no --continue flag to manage). Hook/tool callbacks
    run on the SDK's tasks while run() drains on the caller's loop — both on
    the SAME event loop, so a plain deque handoff is safe."""

    def __init__(self, workspace: str,
                 client_factory: Callable[[], Any] | None = None):
        self._workspace = workspace
        self._factory = client_factory or _default_client_factory(workspace, self)
        self._client: Any = None
        self._queue: collections.deque[AgentEvent] = collections.deque()
        self._turn_active = False
        self._interrupted = False
        # Task 3 fills these in (question wait state):
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None

    def drain_queue(self) -> list[AgentEvent]:
        out = []
        while self._queue:
            out.append(self._queue.popleft())
        return out

    async def _ensure_client(self):
        if self._client is None:
            self._client = self._factory()
            await self._client.connect()
        return self._client

    async def _on_post_tool_use(self, input_data, tool_use_id, context) -> dict:
        name = input_data.get("tool_name", "")
        if name in _FILE_TOOLS:
            fp = (input_data.get("tool_input") or {}).get("file_path", "")
            rel = _rel(fp, self._workspace)
            if rel is None:
                self._queue.append(AgentEvent(
                    kind="status", text="file outside workspace ignored"))
            else:
                self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return {}

    async def _on_can_use_tool(self, tool_name, input_data, context):
        # Task 3 replaces this with the AskUserQuestion interception; until
        # then allow everything (bypassPermissions already auto-approves
        # normal tools; this only sees AskUserQuestion-class calls).
        from claude_agent_sdk.types import PermissionResultAllow
        return PermissionResultAllow(updated_input=input_data)

    def _translate(self, msg) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        if tname == "AssistantMessage":
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    if block.name != self._last_status:
                        self._last_status = block.name
                        events.append(AgentEvent(kind="status", text=block.name))
        elif tname == "ResultMessage":
            events.append(AgentEvent(kind="done"))
        return events

    async def run(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        self._interrupted = False
        self._last_status: str | None = None
        try:
            client = await self._ensure_client()
            await client.query(text)
            async for msg in client.receive_response():
                for ev in self.drain_queue():
                    yield ev
                for ev in self._translate(msg):
                    if ev.kind == "done" and self._interrupted:
                        yield AgentEvent(kind="status", text="interrupted")
                    yield ev
        except Exception:
            _log.exception("sdk turn failed")
            for ev in self.drain_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            self._turn_active = False
        for ev in self.drain_queue():
            yield ev

    async def interrupt(self) -> None:
        if self._client is None or not self._turn_active:
            return  # idempotent no-op
        self._interrupted = True
        await self._client.interrupt()

    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool:
        return False  # Task 3

    async def pending(self) -> str | None:
        return self._pending_payload
```

주의: `ResultMessage`는 interrupt 후 `subtype="error_during_execution"`으로 남은 버퍼에 나타날 수 있음(SDK 문서) — `receive_response()` 루프가 자연 종료할 때까지 드레인하는 위 구조가 그대로 그 요구를 충족한다. done 번역은 subtype과 무관(턴 종료 표시).

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd harness && .venv/bin/pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add harness/sdk_driver.py harness/tests/
git commit -m "feat(harness): SdkDriver — ClaudeSDKClient turns, typed-message translation, PostToolUse file tracking

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: sdk_driver 질문 가로채기·중단 왕복 + Dockerfile

`AskUserQuestion`을 `can_use_tool`에서 가로채 questions 이벤트로 방출하고, `/answers`가 pending future를 resolve해 같은 턴이 이어지게 한다. Dockerfile 완성.

**Files:**
- Modify: `harness/sdk_driver.py`
- Create: `harness/Dockerfile`
- Test: `harness/tests/test_sdk_driver_questions.py`

**Interfaces:**
- Produces: questions AgentEvent — `payload=json.dumps({"interrupt_id": "<uuid>", "questions": {"stage": "prototype", "questions": [{"number": 1, "question": ..., "header": ..., "options": [...], "multi_select": bool, "answer": null}, ...]}})` — 프론트 `QuestionsPayload{interrupt_id, questions: QuestionFile}` 계약(types.ts:80-83) 충족
- Produces: `submit_answers(interrupt_id, answers)` — answers는 `{"<question number>": "<label>"}`(기존 계약). SDK 응답 셰이프로 번역: `PermissionResultAllow(updated_input={"questions": <원본>, "answers": {"<question text>": "<label>"}})` (공식 문서의 응답 계약)
- Consumes: SDK `can_use_tool` 콜백 — AskUserQuestion input은 `{"questions": [{"question", "header", "options": [{"label", "description"}], "multiSelect"}]}`

- [ ] **Step 1: 실패하는 테스트 작성**

`harness/tests/test_sdk_driver_questions.py`:

```python
import asyncio, json, pytest
from sdk_driver import SdkDriver
from tests.fake_sdk import FakeSdkClient, ResultMessage

ASK_INPUT = {"questions": [
    {"question": "Which DB?", "header": "DB",
     "options": [{"label": "Postgres", "description": "relational"},
                 {"label": "DynamoDB", "description": "NoSQL"}],
     "multiSelect": False},
]}

class QuestionScriptClient(FakeSdkClient):
    """Simulates the SDK: receive_response first triggers can_use_tool
    (captured from the driver), waits for its resolution, then finishes."""
    def __init__(self, driver_ref):
        super().__init__()
        self.driver_ref = driver_ref
        self.answer_result = None

    async def receive_response(self):
        result = await self.driver_ref()._on_can_use_tool(
            "AskUserQuestion", ASK_INPUT, None)
        self.answer_result = result
        yield ResultMessage()

@pytest.mark.asyncio
async def test_question_roundtrip(tmp_path):
    holder = {}
    client = QuestionScriptClient(lambda: holder["d"])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    holder["d"] = d

    async def consume():
        return [ev async for ev in d.run("build")]
    turn = asyncio.create_task(consume())

    # wait until the question event is queued and pending() reflects it
    for _ in range(100):
        await asyncio.sleep(0.01)
        if d._pending_payload is not None:
            break
    payload = json.loads(d._pending_payload)
    iid = payload["interrupt_id"]
    q = payload["questions"]["questions"][0]
    assert q["question"] == "Which DB?"
    assert [o["label"] for o in q["options"]] == ["Postgres", "DynamoDB"]

    ok = await d.submit_answers(iid, {"1": "Postgres"})
    assert ok
    events = await turn
    kinds = [e.kind for e in events]
    assert "questions" in kinds and kinds[-1] == "done"
    ui = client.answer_result.updated_input
    assert ui["answers"] == {"Which DB?": "Postgres"}
    assert d._pending_payload is None

@pytest.mark.asyncio
async def test_answers_wrong_interrupt_id_rejected(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    assert not await d.submit_answers("nope", {"1": "x"})
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd harness && .venv/bin/pytest tests/test_sdk_driver_questions.py -v`
Expected: FAIL — question roundtrip이 pending 없이 즉시 종료

- [ ] **Step 3: 가로채기 구현**

`sdk_driver.py`의 `_on_can_use_tool` 교체 + `submit_answers`/`pending` 구현:

```python
    async def _on_can_use_tool(self, tool_name, input_data, context):
        from claude_agent_sdk.types import PermissionResultAllow
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)
        import json as _json, uuid
        iid = uuid.uuid4().hex
        questions = []
        for i, q in enumerate(input_data.get("questions", []), start=1):
            questions.append({
                "number": i,
                "question": q.get("question", ""),
                "header": q.get("header", ""),
                "options": q.get("options", []),
                "multi_select": bool(q.get("multiSelect")),
                "answer": None,
            })
        payload = _json.dumps({"interrupt_id": iid,
                               "questions": {"stage": "prototype",
                                             "questions": questions}},
                              ensure_ascii=False)
        self._pending_payload = payload
        self._pending_iid = iid
        self._pending_input = input_data
        loop = asyncio.get_running_loop()
        self._pending_question = loop.create_future()
        self._queue.append(AgentEvent(kind="questions", payload=payload))
        answers = await self._pending_question  # stays open until /answers
        # Map "number -> label" (our contract) to "question text -> label"
        # (SDK contract).
        by_number = {str(q["number"]): q["question"] for q in questions}
        sdk_answers = {by_number[k]: v for k, v in answers.items()
                       if k in by_number}
        self._pending_payload = None
        self._pending_question = None
        return PermissionResultAllow(updated_input={
            "questions": input_data.get("questions", []),
            "answers": sdk_answers,
        })

    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool:
        if (self._pending_question is None
                or getattr(self, "_pending_iid", None) != interrupt_id
                or self._pending_question.done()):
            return False
        self._pending_question.set_result(answers)
        return True
```

`run()`의 메시지 루프는 훅 큐를 message 사이마다 드레인하지만, **질문 대기 중에는 receive_response가 아무것도 yield하지 않아** 큐의 questions 이벤트가 스트림에 나가지 못한다. run()의 루프를 큐 폴링과 receive를 경합시키는 구조로 보강:

```python
            # inside run(): replace `async for msg in client.receive_response():`
            agen = client.receive_response().__aiter__()
            next_msg = asyncio.ensure_future(agen.__anext__())
            while True:
                done, _ = await asyncio.wait({next_msg}, timeout=0.05)
                for ev in self.drain_queue():
                    yield ev
                if not done:
                    continue
                try:
                    msg = next_msg.result()
                except StopAsyncIteration:
                    break
                for ev in self._translate(msg):
                    if ev.kind == "done" and self._interrupted:
                        yield AgentEvent(kind="status", text="interrupted")
                    yield ev
                next_msg = asyncio.ensure_future(agen.__anext__())
```

- [ ] **Step 4: 테스트 통과 확인 (전체 회귀 포함)**

Run: `cd harness && .venv/bin/pytest tests/ -v`
Expected: PASS — Task 2의 기존 테스트 포함 전체

- [ ] **Step 5: Dockerfile 작성**

`harness/Dockerfile` (git `6d21e1f:harness/Dockerfile` 기반, npm 글로벌 설치 제거):

```dockerfile
# harness/Dockerfile — layered over the managed al2023 base (supplied at build
# time via MicrovmImage BaseImageArn; this Dockerfile only adds our app layer).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
# shadow-utils provides useradd (absent from the minimal al2023 base).
# nodejs/npm: NOT for Claude Code (the agent-sdk wheel bundles its own native
# binary) — they are the runtime the agent uses to build/run the prototype.
RUN dnf install -y python3.11 python3.11-pip nodejs npm shadow-utils git && dnf clean all
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
# Prototype-building rules baked into the workspace.
COPY aiplc-rules /workspace/aiplc-rules
# Run as a NON-ROOT user. The SDK spawns the same claude binary, which
# hard-refuses bypassPermissions when euid==0 (learned at 6d21e1f: version
# checks pass as root but every turn exits 1).
RUN useradd -m -u 10001 harness \
    && chown -R harness:harness /workspace /app
USER harness
EXPOSE 8080 9000
CMD ["python3.11", "-m", "serve"]
```

- [ ] **Step 6: 커밋**

```bash
git add harness/
git commit -m "feat(harness): AskUserQuestion interception + answer roundtrip; interrupt drain; Dockerfile

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 백엔드 — VM 컨트롤러·HarnessClient 부활

git `ef63be4^`의 sandbox 파일에서 필요분만 `pathfinder/proto/`로 부활. suspend/resume 소멸, interrupt 중계 추가.

**Files:**
- Create: `backend/pathfinder/proto/__init__.py`, `backend/pathfinder/proto/vm.py`, `backend/pathfinder/proto/harness_client.py`
- Create: `backend/tests/test_proto_vm.py`, `backend/tests/test_proto_harness_client.py`

**Interfaces:**
- Produces: `BootSpec(region="ap-northeast-1", image_id, exec_role_arn, max_idle_seconds=...)`, `VMHandle(vm_id, base_url, status)`, `LambdaMicroVMController(region="ap-northeast-1", client=None)` — `boot(project_id, spec) -> VMHandle`, `stop(handle)`, `status(handle) -> VMStatus`; `mint_harness_token(vm_id, region, client=None) -> dict[str, str]` (`{"X-aws-proxy-auth": ...}`)
- Produces: `HarnessClient(base_url, headers)` — `send_message(text) -> AsyncIterator[AgentEvent]`, `send_answers(interrupt_id, answers) -> bool`, `interrupt() -> None`, `pending() -> str|None`, `read_file/write_file/list_files`, `heartbeat() -> bool`
- Consumes: `git show 'ef63be4^':backend/pathfinder/sandbox/microvm_control.py`(BootSpec/VMHandle/Fake), `...microvm_control_aws.py`(boto3 `lambda-microvms`: `run_microvm/get_microvm/terminate_microvm/create_microvm_auth_token`), `...harness.py`(HarnessClient SSE 소비)

- [ ] **Step 1: 부활 소스 추출·축소**

```bash
mkdir -p backend/pathfinder/proto && touch backend/pathfinder/proto/__init__.py
git show 'ef63be4^':backend/pathfinder/sandbox/microvm_control.py > /tmp/mc.py
git show 'ef63be4^':backend/pathfinder/sandbox/microvm_control_aws.py > /tmp/mca.py
git show 'ef63be4^':backend/pathfinder/sandbox/harness.py > /tmp/h.py
```

`/tmp/mc.py` + `/tmp/mca.py` → `backend/pathfinder/proto/vm.py`로 병합: `BootSpec`(suspended_duration_seconds·auto_resume 필드 삭제), `VMHandle`, `_map_status`, `LambdaMicroVMController`(boot/stop/status만 — resume/suspend 메서드 삭제), `FakeMicroVMController`(resume/suspend 삭제), `mint_harness_token` 그대로. import 경로를 `pathfinder.models`로 조정.

`/tmp/h.py` → `backend/pathfinder/proto/harness_client.py`: `send_answers`를 SSE 소비가 아닌 `POST /answers` → bool(204→True, 409→False)로 변경, `interrupt()` 추가:

```python
    async def send_answers(self, interrupt_id: str, answers: dict[str, str]) -> bool:
        r = await self._http.post(f"{self._base}/answers",
                                  json={"interrupt_id": interrupt_id, "answers": answers},
                                  headers=self._headers)
        if r.status_code == 409:
            return False
        r.raise_for_status()
        return True

    async def interrupt(self) -> None:
        r = await self._http.post(f"{self._base}/interrupt", headers=self._headers)
        r.raise_for_status()
```

- [ ] **Step 2: 실패하는 테스트 작성**

`backend/tests/test_proto_vm.py` — `ef63be4^:backend/tests/test_microvm_control_aws.py`의 fake boto3 client 패턴을 이식(부활 파일에서 boot/stop/status/mint 시나리오만 유지, resume/suspend 시나리오 폐기):

```python
# 핵심 시나리오 (원본 테스트 부활·축소):
# - boot: run_microvm 응답의 microvmId/endpoint → poll get_microvm RUNNING → VMHandle
# - boot 폴링 타임아웃 → TimeoutError
# - stop: terminate_microvm 호출 확인
# - mint_harness_token: create_microvm_auth_token 응답 map에서 X-aws-proxy-auth 추출
```

`backend/tests/test_proto_harness_client.py` — httpx.MockTransport로 `/answers` 204/409 분기, `/interrupt` POST, `/message` SSE 소비(기존 `ef63be4^:backend/tests/test_harness_client.py` 패턴 이식).

- [ ] **Step 3: 실패 확인 → 구현 마무리 → 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_proto_vm.py tests/test_proto_harness_client.py -v` (venv 없으면 `python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'`)
Expected: FAIL → 구현/조정 → PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/pathfinder/proto/ backend/tests/test_proto_vm.py backend/tests/test_proto_harness_client.py
git commit -m "feat(proto): revive LambdaMicroVMController (boot/stop/status) + HarnessClient with interrupt

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: PrototypeSession — 빌드 세션 오케스트레이션

VM 부팅 → 파일 push → 턴 중계 → 유휴 타이머 → 종료 시 S3 sync + stop.

**Files:**
- Create: `backend/pathfinder/proto/session.py`
- Create: `backend/tests/test_proto_session.py`

**Interfaces:**
- Produces:

```python
class PrototypeSession:
    def __init__(self, project_id: str, slug: str, s3: S3StoreLike,
                 controller: MicroVMController, spec: BootSpec,
                 harness_factory: Callable[[str, dict], HarnessClientLike],
                 rules_dir: Path, idle_seconds: int = 1800,
                 token_minter: Callable[[str], dict] | None = None): ...
    status: Literal["starting", "ready", "building", "waiting_input", "failed", "closed"]
    async def start(self) -> None            # boot→push(PROTOTYPE-*.md, prototype-building.md, 재빌드시 bundle 복원)→첫 턴은 라우트가 발화
    async def send_message(self, text) -> AsyncIterator[AgentEvent]   # 턴 중계 + 타이머 리셋
    async def send_answers(self, interrupt_id, answers) -> bool
    async def interrupt(self) -> None
    async def close(self) -> None            # pull prototype/** → S3 bundle/ 업로드(node_modules·.next 제외) → VM stop
    def first_prompt(self) -> str            # 스펙 §4의 첫 턴 자동 발화 텍스트
```

- Consumes: Task 4의 컨트롤러·클라이언트, `S3StoreLike`(s3store.py:8-12), 스펙 §4의 파일 경로 계약 — S3 키 `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`(프로젝트 프리픽스 하위), 번들 키 `prototypes/{slug}/bundle/**`
- `first_prompt()`에 스펙 §4의 5개 지시 포함: PROTOTYPE 읽고 빌드 / 질문은 사용자에게 / 완성물 `/workspace/prototype/` + README / basePath·상대 경로(경로 프록시 하위 동작) / LLM은 Bedrock+기본 자격증명 체인(키 하드코딩 금지, 리전·모델 env 수용)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_proto_session.py` — `FakeMicroVMController`(Task 4에 부활) + fake harness(딕셔너리 파일스토어 + 스크립트된 이벤트) + `tests/fakes/in_memory_s3.py`(기존) 사용:

```python
# 시나리오:
# - start(): S3에 PROTOTYPE-{slug}.md 있으면 boot → push 확인(파일 계약:
#   PROTOTYPE md + prototype-building.md rule) → status "ready"
# - start(): S3에 스펙 없으면 FileNotFoundError (라우트가 404로 변환)
# - start(): S3 bundle/ 프리픽스에 파일 있으면 재빌드 — bundle 파일도 push
# - send_message: 이벤트 중계 + 종료 후에도 status "ready", waiting_input은
#   questions 이벤트 통과 시 설정
# - close(): fake harness의 prototype/** 파일이 S3 prototypes/{slug}/bundle/에
#   업로드, node_modules/·.next/ 경로는 제외, VM stop 호출 확인
# - 유휴 타이머: idle_seconds=0.05로 축소해 자동 close 확인
# - close() 멱등(이중 호출 안전)
```

- [ ] **Step 2: 실패 확인 → 구현 → 통과**

구현 핵심: 유휴 타이머는 `asyncio.get_running_loop().call_later` 재장전(턴 시작·answers 제출 시 리셋). close의 pull은 `harness.list_files("prototype/**")` → 각 `read_file` → `s3.put(f"prototypes/{slug}/bundle/{rel}", content)`. 제외 패턴은 경로 세그먼트 검사 `{"node_modules", ".next", ".git"}`.

Run: `cd backend && .venv/bin/pytest tests/test_proto_session.py -v`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add backend/pathfinder/proto/session.py backend/tests/test_proto_session.py
git commit -m "feat(proto): PrototypeSession — boot/push/turn-relay/idle-timer/S3-bundle-sync

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: ProtoHost — EC2 로컬 호스팅

S3 번들 → 로컬 디렉토리 → `npm install`/빌드/기동 서브프로세스 관리.

**Files:**
- Create: `backend/pathfinder/proto/host.py`
- Create: `backend/tests/test_proto_host.py`, `backend/tests/fixtures/proto_npm_stub/` (더미 npm 프로젝트)

**Interfaces:**
- Produces:

```python
class ProtoHost:
    def __init__(self, s3: S3StoreLike, root: Path,
                 port_range: range = range(4001, 4051)): ...
    async def start(self, pid: str, slug: str) -> HostInfo   # download→install→build?→start
    async def stop(self, pid: str, slug: str) -> None
    def status(self, pid: str, slug: str) -> HostInfo | None # {"state", "port", "log_tail"}
    def log_tail(self, pid: str, slug: str, lines: int = 100) -> str

@dataclass
class HostInfo:
    state: Literal["installing", "building", "running", "failed", "stopped"]
    port: int | None
    log_tail: str
```

- Consumes: S3 번들 프리픽스 `prototypes/{slug}/bundle/`(Task 5가 업로드한 것; s3 인자는 프로젝트 프리픽스 스토어)
- 기동 규약: `package.json`의 `scripts.build` 있으면 `npm run build` 후, `scripts.start` 있으면 `npm run start`, 없으면 `npm run dev`. env는 `PORT=<scanned>`, `AWS_REGION`, `ANTHROPIC_MODEL`만(스펙 §4). 포트 스캔: range를 순회하며 `socket.bind` 성공하는 첫 포트

- [ ] **Step 1: 더미 npm fixture 작성**

`backend/tests/fixtures/proto_npm_stub/package.json`:

```json
{"name": "stub", "scripts": {"start": "node server.js"}}
```

`backend/tests/fixtures/proto_npm_stub/server.js`:

```javascript
const http = require("http");
http.createServer((req, res) => res.end("stub ok"))
    .listen(process.env.PORT, "127.0.0.1");
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# 시나리오 (npm install은 devDependencies 없는 stub이라 수 초 내 완료):
# - start(): fixture를 in-memory S3 bundle/ 프리픽스에 넣고 start →
#   폴링으로 state "running" 도달 → 실 HTTP GET localhost:{port} == "stub ok"
# - 포트 점유 시 다음 포트로 스캔 (테스트가 4001을 미리 bind)
# - stop(): 프로세스 종료 + state "stopped"
# - 빌드 실패(깨진 package.json): state "failed" + log_tail에 npm 에러
# - status() 미기동 slug → None
```

- [ ] **Step 3: 실패 확인 → 구현 → 통과**

구현 핵심: `asyncio.create_subprocess_exec("npm", ...)` + stdout/err를 로그 파일(`<dir>/.proto-host.log`)로 리다이렉트, `log_tail`은 파일 tail. install/build는 `await proc.wait()` 후 rc 검사, start는 백그라운드 유지 + 포트 리슨 폴링(상한 60초).

Run: `cd backend && .venv/bin/pytest tests/test_proto_host.py -v`
Expected: PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/pathfinder/proto/host.py backend/tests/test_proto_host.py backend/tests/fixtures/proto_npm_stub/
git commit -m "feat(proto): ProtoHost — S3 bundle download, npm lifecycle, port scan, log tail

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 백엔드 라우트 — REST + SSE + 리버스 프록시

**Files:**
- Create: `backend/pathfinder/routes/prototypes.py`
- Modify: `backend/pathfinder/app.py` (라우터 include + 세션/호스트 레지스트리 + 기동 시 고아 VM 정리 + env)
- Test: `backend/tests/test_routes_prototypes.py`

**Interfaces:**
- Produces (모두 기존 `/projects` 프리픽스 규약):
  - `GET  /projects/{pid}/prototypes` → `[{"slug", "spec_path", "state": "none|building|built|running|failed", "port"}]` (스펙 md 목록은 S3 `aiplc-docs/discovery/prototypes/` list + 세션/호스트/번들 존재로 상태 합성)
  - `POST /projects/{pid}/prototypes/{slug}/session` → 202 (VM 부팅 시작; 실패 시 502+사유). 부팅 완료 후 첫 턴은 `session.first_prompt()`로 자동 발화되어 아래 events 스트림으로 흐름
  - `GET  /projects/{pid}/prototypes/{slug}/events?text=` → SSE (turns.py:33-39 패턴 + `_redacted` 재사용)
  - `GET  /projects/{pid}/prototypes/{slug}/answers/stream?interrupt_id=&answers=` → 204/409 (SSE 아님 — 이벤트는 열린 events 스트림으로)
  - `POST /projects/{pid}/prototypes/{slug}/interrupt` → 202
  - `DELETE /projects/{pid}/prototypes/{slug}/session` → close(S3 sync + VM stop)
  - `POST /projects/{pid}/prototypes/{slug}/host` / `DELETE .../host` / `GET .../host` (start/stop/status+log_tail)
  - `ALL  /proto/{pid}/{slug}/{path:path}` → `http://127.0.0.1:{port}/{path}` httpx 스트리밍 프록시 (미기동 502 + 안내 텍스트)
- Consumes: Task 5·6의 클래스, `ensure_workspace`(deps.py:13), `redact_credentials`(turns.py:15-25 `_redacted`), app.py의 `s3_store_factory`
- app.py 추가 env: `PATHFINDER_VM_REGION`(기본 `ap-northeast-1`), `PATHFINDER_VM_IMAGE_ID`, `PATHFINDER_VM_ROLE_ARN`, `PATHFINDER_PROTO_ROOT`(기본 `~/pathfinder-protos`). lifespan에서 기동 시 lambda-microvms `list_microvms` 태그 필터 고아 stop(실패해도 기동은 계속 — 로그만)
- 테스트: 기존 fakes 패턴 — `app.py`의 factory monkeypatch로 FakeController/fake harness/in-memory S3 주입. 프록시는 로컬 임시 HTTP 서버 대상 스트리밍 검증

- [ ] **Step 1: 실패하는 테스트 → Step 2: 구현 → Step 3: 통과 → Step 4: 커밋**

라우트별 시나리오: 목록 상태 합성 4상태, 세션 시작 202/404(스펙 없음)/502(부팅 실패), events 스트림 중계+redaction, answers 204/409, interrupt 202, host start/stop/status, 프록시 200 스트리밍/502. 프록시 구현:

```python
@router.api_route("/proto/{pid}/{slug}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(pid: str, slug: str, path: str, request: Request):
    info = host_registry.status(pid, slug)
    if info is None or info.state != "running":
        return PlainTextResponse("prototype not running — start hosting first",
                                 status_code=502)
    url = f"http://127.0.0.1:{info.port}/{path}"
    client = httpx.AsyncClient()
    req = client.build_request(request.method, url,
                               params=request.query_params,
                               content=request.stream(),
                               headers={k: v for k, v in request.headers.items()
                                        if k.lower() not in ("host", "x-origin-verify")})
    upstream = await client.send(req, stream=True)
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             headers=dict(upstream.headers),
                             background=BackgroundTask(upstream.aclose))
```

```bash
git add backend/pathfinder/routes/prototypes.py backend/pathfinder/app.py backend/tests/test_routes_prototypes.py
git commit -m "feat(routes): prototypes REST+SSE+streaming reverse proxy; orphan VM cleanup on startup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 프론트 — API 클라이언트 + 상태 카드

**Files:**
- Create: `frontend/lib/api/prototypes.ts`, `frontend/components/prototypes/PrototypeCard.tsx`
- Test: 각 `.test.ts(x)` (기존 Vitest+MSW 패턴 — `lib/api/client.test.ts` 참조)

**Interfaces:**
- Produces: `listPrototypes(pid): Promise<PrototypeInfo[]>`, `startSession(pid, slug)`, `closeSession(pid, slug)`, `interruptSession(pid, slug)`, `streamPrototypeEvents(pid, slug, text, handlers): () => void`(sse.ts openStream 재사용), `submitPrototypeAnswers(pid, slug, interruptId, answers): Promise<boolean>`, `startHost/stopHost/getHost`
- Produces: `PrototypeCard({info, onBuild, onOpenPreview, onStartHost, onStopHost, busy})` — 상태 전이 `none→building→built→running/failed` 별 액션 버튼·프리뷰 링크(`/api/proto/{pid}/{slug}/`)·로그 보기
- Consumes: Task 7 API 계약, `API_BASE_URL`(client.ts:13), `AgentEvent`(types.ts)

- [ ] Step 1 실패 테스트(MSW 핸들러로 각 함수 + 카드 상태별 렌더/버튼) → Step 2 구현 → Step 3 `cd frontend && npm test -- --run` PASS → Step 4 커밋

```bash
git add frontend/lib/api/prototypes.ts frontend/components/prototypes/
git commit -m "feat(frontend): prototypes API client + status card

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: 프론트 — 빌드 채팅 패널 + 프로토타입 탭

**Files:**
- Create: `frontend/components/prototypes/BuildPanel.tsx`, `frontend/app/projects/[projectId]/prototypes/page.tsx`
- Modify: 프로젝트 탭 네비게이션(기존 canvas/dashboard/workspace 링크가 있는 컴포넌트에 "프로토타입" 추가 — `AppHeader.tsx` 또는 프로젝트 레이아웃에서 기존 탭 렌더 위치 확인 후 동일 패턴)
- Test: 각 `.test.tsx`

**Interfaces:**
- Produces: `BuildPanel({projectId, slug})` — 채팅 타임라인(기존 `useTurnStream` 변형: `streamPrototypeEvents` 사용), 중단 버튼(턴 활성 중 노출 → `interruptSession`), questions 이벤트 수신 시 `QuestionForm`(components/questions/QuestionForm.tsx — `file: QuestionFile, onSubmit(answers), submitting` props 그대로) 렌더 → `submitPrototypeAnswers`, `file_changed` 누적 목록, "완료" 버튼 → `closeSession`
- Consumes: Task 8 클라이언트, `QuestionsPayload`(types.ts:80-83) 파싱은 `useWorkspaceStream.ts:128-131`의 `safeParse` 패턴
- 페이지: `listArtifacts` 또는 `listPrototypes`로 카드 그리드 + 카드 클릭 시 BuildPanel 열림

- [ ] Step 1 실패 테스트(BuildPanel: 이벤트 렌더·questions→QuestionForm·중단 버튼 노출 조건·answers 제출, page: 카드 목록) → Step 2 구현 → Step 3 `npm test -- --run` + `npm run build` PASS → Step 4 커밋

```bash
git add frontend/
git commit -m "feat(frontend): prototype tab — build chat panel with interrupt + question wizard reuse

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 인프라 — MicroVM 이미지·롤 부활 (CDK)

**Files:**
- Modify: `infra/lib/pathfinder-drill-stack.ts`
- Create: `infra/package-harness.sh` (git `5f2dbc6^:infra/package-harness.sh` 부활 — aiplc-rules 전체 대신 필요 룰만 복사하도록 수정 가능하나 원본 유지가 단순)
- Modify: `infra/lib/backend-permissions.ts` (백엔드 롤에 lambda-microvms 권한 statement 추가)
- Test: `infra/test/drill-stack.assert.ts` 신규 + `infra/package.json` test 스크립트에 추가

**Interfaces:**
- Consumes: `git show '5f2dbc6^':infra/lib/pathfinder-drill-stack.ts` — `CfnMicrovmImage` 정의(hooks 9000, ready/validate ENABLED, ARM_64, 2048MiB, BaseImageArn `al2023-1` major `'1'`), BuildRole(ServicePrincipal `lambda.amazonaws.com` + SourceAccount/SourceArn 조건), 로그 그룹
- 변경점 (원본 대비):
  - VM은 **Tokyo 고정**: 이미지·롤·로그 그룹 관련 리소스의 리전 상수 `ap-northeast-1` (스택 자체는 서울 배포 — 크로스 리전 이슈가 있으므로 **MicroVM 리소스는 별도 스택 `PathfinderVmStack`으로 분리해 Tokyo에 배포**, bin/app.ts에서 `env: {region: 'ap-northeast-1'}` 명시)
  - ExecutionRole: S3 statement **전부 삭제**(스펙 §5 — 파일은 백엔드 중개), Bedrock invoke statement만 (`backend-permissions.ts`의 MODEL/MODEL_FAMILY ARN 셰이프 재사용)
  - 이미지 env: `CLAUDE_CODE_USE_BEDROCK=1`, `ANTHROPIC_MODEL=global.anthropic.claude-opus-4-8` (PATHFINDER_DRIVER 삭제)
  - 백엔드 롤(+ 호스팅 인스턴스 롤): `lambda-microvms:RunMicrovm/GetMicrovm/TerminateMicrovm/ListMicrovms/CreateMicrovmAuthToken` — Tokyo 리소스 ARN 스코프
- Produces: `PathfinderVmStack` outputs `ImageArn`, `ExecutionRoleArn` → 백엔드 env `PATHFINDER_VM_IMAGE_ID`/`PATHFINDER_VM_ROLE_ARN`에 수동 주입(README 기록)

- [ ] Step 1 assertion 테스트 작성(이미지 존재·hooks 설정·ExecRole에 S3 statement **부재**·Bedrock ARN 셰이프·백엔드 롤 microvms 액션) → Step 2 실패 확인(`cd infra && npm test`) → Step 3 구현 → Step 4 통과 + `npx cdk synth` 성공 → Step 5 커밋

```bash
git add infra/
git commit -m "feat(infra): PathfinderVmStack (Tokyo) — MicroVM image + roles revived, exec role Bedrock-only

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: 수동 e2e 체크리스트 + README

**Files:**
- Create: `docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md`
- Modify: `backend/.env.example`(신규 env 4종), `infra/README.md`(VmStack 배포 절차·이미지 빌드·package-harness.sh), 리포 `README.md`(프로토타입 탭 소개 한 단락)

**체크리스트 항목(문서에 그대로):** VmStack 배포 → 이미지 빌드 성공(/ready 로그) → sdk_diagnostic 로그 확인(번들 바이너리 arch OK) → 프로토타입 탭에서 세션 시작 → 첫 턴 스트림 → AskUserQuestion 위저드 왕복 → 중단 버튼 → 완료 → S3 bundle 확인 → 호스팅 start → 프리뷰 URL 접속 → 프록시 하위 동작(basePath) → 로그 tail → 호스팅 stop → 백엔드 재시작 후 고아 VM 정리 로그.

- [ ] Step 1 문서 작성 → Step 2 커밋

```bash
git add docs/ backend/.env.example infra/README.md README.md
git commit -m "docs: prototype generation e2e checklist + env/deploy docs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 결과

- **스펙 커버리지**: §2 아키텍처(T4·5·7), §3 백엔드(T4-7)/하네스(T1-3)/프론트(T8-9)/인프라(T10), §4 데이터 흐름 전부(세션 시작 T5·7, 대화 턴 T2, 질문 T3·9, 중단 T2·3·7·9, 종료 T5, 호스팅 T6·7, 재빌드 T5), §5 보안(T10 롤 축소·T7 프록시 헤더 스트립·T2 sanitize), §6 에러 표(T5 타이머·T7 502·T6 로그 tail), §7 테스트(각 태스크 TDD), §8 스코프 제외 준수.
- **모호성 해소 기록**: (1) harness `/answers`는 스트림이 아닌 204 — SDK 모델에서 이벤트는 열린 /message 스트림으로 흐름(스펙 §4 "열린 채 대기"의 구현 귀결). (2) MicroVM CDK 리소스는 서울 drill 스택이 아닌 **별도 Tokyo 스택** — 크로스 리전 L1 배포 불가 제약의 귀결(스펙 §3 "drill 스택 부활"을 리전 정합하게 조정). (3) 첫 턴 자동 발화는 세션 시작 API가 아니라 첫 events 연결에서 `first_prompt()` 발화 — SSE로 즉시 스트림을 받기 위함.
- **타입 일관성**: driver 프로토콜(run/submit_answers/interrupt/pending)이 T1 fake → T2·3 구현 → T4 HarnessClient → T7 라우트까지 동일 시그니처. `QuestionsPayload{interrupt_id, questions: QuestionFile}` T3 생산 ↔ T9 소비 일치.
