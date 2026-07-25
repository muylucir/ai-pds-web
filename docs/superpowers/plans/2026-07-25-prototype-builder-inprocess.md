# 프로토타입 빌더 백엔드 흡수 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로토타입 빌드 에이전트를 도쿄 MicroVM에서 백엔드 프로세스 안으로 옮기고, SDK의 `session_store`+`resume`으로 대화 맥락을 재시작 뒤에도 유지한다.

**Architecture:** `harness/sdk_driver.py`를 `backend/pathfinder/proto/builder.py`로 이식해 `ClaudeSDKClient`를 백엔드 in-process로 구동한다. VM 계층(`vm.py`, `harness_client.py`, `harness/`, `PathfinderVmStack`)은 전부 삭제한다. 빌드 워크스페이스는 `PATHFINDER_PROTO_ROOT/{pid}/{slug}/`에 상주하고 `ProtoHost`가 그 디렉토리를 in-place로 호스팅한다. transcript는 `S3SessionStore` 어댑터로 S3에 미러링되고 `resume=<uuid>`로 재개된다.

**Tech Stack:** Python 3.11 · FastAPI · claude-agent-sdk 0.2.126 · boto3 1.43.50 · pytest/pytest-asyncio/moto · Next.js 15 + Vitest · AWS CDK (TypeScript)

**Spec:** `docs/superpowers/specs/2026-07-25-prototype-builder-inprocess-design.md`

## Global Constraints

- **Python 3.11** — 백엔드 venv는 3.11. `backend/.venv/bin/python`으로 실행한다.
- **신규 의존성**: `claude-agent-sdk==0.2.126` (정확 버전 핀 `==`, `>=` 금지 — SDK 0.2.x churn 때문). `backend/pyproject.toml`의 `dependencies`에 추가.
- **`resume` 값은 반드시 UUID 형식** — SDK가 `_validate_uuid`로 거부한다(`_internal/session_resume.py:151`). `f"{pid}-{slug}"` 같은 문자열 금지.
- **`session_store` + `enable_file_checkpointing` 동시 사용 금지** — SDK가 `ValueError`를 던진다. 체크포인팅은 쓰지 않는다.
- **`continue_conversation` 사용 금지** — 스토어에 `list_sessions()` 구현을 강제한다. 항상 `resume`을 명시한다.
- **`CLAUDE_CONFIG_DIR`를 항상 주입한다** — 미주입 시 번들 바이너리가 호스트 유저의 `~/.claude`(개인 skills/agents/CLAUDE.md)를 읽는다.
- **에이전트 에러는 sanitize** — SSE로 나가는 `error` 이벤트 text는 `"agent turn failed"` 같은 고정 문구. 상세는 서버 로그만(자격증명 노출 차단).
- **테스트는 AWS 없이 통과해야 한다** — fake SDK client / `FakeS3Store` / `moto[s3]`. 실 Bedrock·실 EC2가 필요한 검증은 e2e 체크리스트로만.
- **롤백 경로 없음** — 삭제 대상은 완전히 지운다. git 히스토리가 유일한 복구 수단.
- 백엔드 테스트: `cd backend && .venv/bin/python -m pytest -q`
- 프론트 테스트: `cd frontend && npm test`
- 인프라 합성: `cd infra && npx cdk synth`

## File Structure

**신규 (backend)**
| 파일 | 책임 |
|---|---|
| `backend/pathfinder/proto/builder.py` | `PrototypeBuilder` — `ClaudeSDKClient` 한 개의 수명·턴 스트림·interrupt·AskUserQuestion 가로채기·PostToolUse 경로 가드. 구 `harness/sdk_driver.py`의 이식본 |
| `backend/pathfinder/proto/session_store.py` | `S3SessionStore` — SDK `SessionStore` 프로토콜의 S3 어댑터 (`append`/`load`/`list_subkeys`) |
| `backend/pathfinder/proto/limits.py` | `BuildSemaphore` — 전역 동시 빌드 상한. 획득/반납/조회 |

**수정 (backend)**
| 파일 | 변경 |
|---|---|
| `backend/pathfinder/proto/session.py` | VM 부팅·파일 push 제거. builder 조립 + session UUID 영속 + 세마포어 반납 + `disconnect()` |
| `backend/pathfinder/proto/host.py` | in-place 호스팅(rmtree 제거), 포트 예약, pid 파일 |
| `backend/pathfinder/routes/prototypes.py` | VM ARN 가드 → 세마포어 429, zip 라우트 추가 |
| `backend/pathfinder/s3store.py` | `get_bytes`/`put_bytes` 추가 |
| `backend/pathfinder/routes/uploads.py` | uuid8 키 + `IfNoneMatch` |
| `backend/pathfinder/app.py` | builder/store/세마포어 배선, VM 배선·고아 스윕 삭제 |

**삭제**: `harness/` 전체 · `proto/vm.py` · `proto/harness_client.py` · `infra/lib/pathfinder-vm-stack.ts` · `infra/package-harness.sh` · `backend/tests/test_proto_vm.py` · `backend/tests/test_proto_harness_client.py` · `infra/test/vm-stack.assert.ts`

**수정 (frontend)**: `components/prototypes/PrototypeCard.tsx`(다운로드 버튼) · `lib/api/prototypes.ts`(zip URL 빌더) · `app/projects/[projectId]/prototypes/page.tsx`(핸들러 배선)

**수정 (infra)**: `lib/pathfinder-hosting-stack.ts` · `lib/backend-permissions.ts` · `lib/user-data.ts` · `bin/app.ts` · `test/hosting-stack.assert.ts`

## Task 순서 근거

Task 1–2가 새 의존성과 순수 유닛(세마포어)을 먼저 세운다. Task 3–4가 builder와 session_store를 만든다(둘 다 다른 것에 의존하지 않는다). Task 5가 `PrototypeSession`을 새 부품으로 재배선하고, 여기서 VM 코드가 처음 삭제된다. Task 6–7이 host/routes를 고친다. Task 8–10이 독립적인 부가 항목(bytes S3+zip, 업로드, 프론트)이다. Task 11이 인프라, Task 12가 문서/체크리스트.

---

### Task 1: claude-agent-sdk 의존성 추가 + 번들 바이너리 검증

**Files:**
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_sdk_available.py` (create)

**Interfaces:**
- Consumes: 없음
- Produces: `claude_agent_sdk` 임포트 가능 + `_bundled/claude` 실행 가능. Task 3이 이 임포트에 의존한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_sdk_available.py`:

```python
# backend/tests/test_sdk_available.py — the SDK is a NEW backend dependency
# (it used to live only in harness/). These tests fail loudly if the wheel is
# missing or its bundled Claude Code binary can't run on this platform --
# which is exactly the failure that would otherwise surface as an opaque
# "session start failed" 502 at workshop time.
from __future__ import annotations

import subprocess
from pathlib import Path


def test_sdk_imports_with_expected_options():
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # noqa: F401
    from claude_agent_sdk.types import AgentDefinition, HookMatcher  # noqa: F401

    # The four options this feature depends on must exist on the dataclass.
    fields = ClaudeAgentOptions.__dataclass_fields__
    for name in ("session_store", "resume", "setting_sources", "skills"):
        assert name in fields, f"ClaudeAgentOptions lacks {name}"


def test_bundled_binary_is_executable():
    import claude_agent_sdk

    binary = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert binary.is_file(), f"bundled binary missing at {binary}"
    proc = subprocess.run([str(binary), "--version"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "Claude Code" in proc.stdout
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_sdk_available.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_agent_sdk'`

- [ ] **Step 3: 의존성 추가**

`backend/pyproject.toml`의 `dependencies` 리스트 끝에 추가한다. 주석의 boto3 설명도 갱신한다:

```toml
# boto3 floor 1.43.35 — Bedrock Converse + S3 + PutObject IfNoneMatch(조건부 쓰기).
# claude-agent-sdk는 정확 버전 핀(==): 0.2.x는 churn이 잦고, wheel이 Claude Code
# 네이티브 바이너리를 번들하므로 버전이 곧 실행 엔진 버전이다.
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.43.35", "uvicorn>=0.30", "python-dotenv>=1.0", "openpyxl>=3.1", "pypdf>=4.0", "python-multipart>=0.0.9", "strands-agents>=1.48,<2", "claude-agent-sdk==0.2.126"]
```

- [ ] **Step 4: 설치 후 통과 확인**

Run: `cd backend && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest tests/test_sdk_available.py -q`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/pyproject.toml backend/tests/test_sdk_available.py
git commit -m "feat(proto): add claude-agent-sdk to the backend, pinned

The build agent moves in-process, so the SDK is no longer a harness-only
dependency. Pinned with == because 0.2.x churns and the wheel bundles the
Claude Code binary — the version IS the execution engine version.

Two guard tests: the four ClaudeAgentOptions fields this feature needs
(session_store/resume/setting_sources/skills) and a --version run of the
bundled binary, so a platform mismatch fails here instead of as an opaque
502 at workshop time."
```

---

### Task 2: BuildSemaphore — 전역 동시 빌드 상한

**Files:**
- Create: `backend/pathfinder/proto/limits.py`
- Test: `backend/tests/test_proto_limits.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `class BuildSemaphore(max_concurrent: int)`
  - `def try_acquire(self) -> bool` — 여유 있으면 카운터 증가 후 True, 없으면 False (블로킹 없음)
  - `def release(self) -> None` — 카운터 감소, 0 미만으로 내려가지 않음(멱등)
  - `def snapshot(self) -> dict[str, int]` — `{"active_builds": int, "max_builds": int}`
  - Task 5(session close/idle에서 release), Task 7(route에서 try_acquire + snapshot)이 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_proto_limits.py`:

```python
# backend/tests/test_proto_limits.py
from __future__ import annotations

from pathfinder.proto.limits import BuildSemaphore


def test_acquires_up_to_limit_then_refuses():
    sem = BuildSemaphore(max_concurrent=2)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False       # 3rd caller is refused, not queued
    assert sem.snapshot() == {"active_builds": 2, "max_builds": 2}


def test_release_frees_a_slot():
    sem = BuildSemaphore(max_concurrent=1)
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False
    sem.release()
    assert sem.snapshot()["active_builds"] == 0
    assert sem.try_acquire() is True


def test_release_is_idempotent_and_never_goes_negative():
    """A session can be closed twice (explicit close + idle timeout racing),
    and each path releases. Over-releasing must not manufacture extra slots."""
    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    sem.release()
    sem.release()
    sem.release()
    assert sem.snapshot()["active_builds"] == 0
    assert sem.try_acquire() is True
    assert sem.try_acquire() is False       # still only ONE slot total


def test_zero_limit_refuses_everything():
    sem = BuildSemaphore(max_concurrent=0)
    assert sem.try_acquire() is False
    assert sem.snapshot() == {"active_builds": 0, "max_builds": 0}
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_limits.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.proto.limits'`

- [ ] **Step 3: 구현**

`backend/pathfinder/proto/limits.py`:

```python
# backend/pathfinder/proto/limits.py — global cap on concurrent prototype
# builds.
#
# The MicroVM era had no cap: each build booted its own VM in Tokyo and AWS
# quota was the only ceiling. In-process builds share ONE box (the workshop
# EC2), where each session holds a `claude` subprocess (~300-500MB RSS) that
# may spawn a `next build` peaking around 2GB. So the ceiling is now ours to
# enforce.
#
# Deliberately NOT asyncio.Semaphore: that blocks the caller until a slot
# frees, which would leave the HTTP request hanging with no way to tell the
# user why. We refuse immediately (429 + a message naming the situation) --
# the user decision was "refuse, don't queue".
from __future__ import annotations


class BuildSemaphore:
    """Non-blocking counting gate. Single-threaded asyncio use only: every
    caller runs on the event loop and neither method awaits, so no lock is
    needed (the increment cannot be interleaved)."""

    def __init__(self, max_concurrent: int):
        self._max = max(0, int(max_concurrent))
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._max:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        # Idempotent: close() and the idle timer can both fire for one
        # session, and clamping at 0 keeps an over-release from inventing a
        # slot that was never held.
        if self._active > 0:
            self._active -= 1

    def snapshot(self) -> dict[str, int]:
        return {"active_builds": self._active, "max_builds": self._max}
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_limits.py -q`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/proto/limits.py backend/tests/test_proto_limits.py
git commit -m "feat(proto): BuildSemaphore — global concurrent-build cap

MicroVM builds had no cap because each one booted its own VM and AWS quota
was the ceiling. In-process builds share the workshop EC2, so the ceiling is
ours: each session holds a claude subprocess (~300-500MB) that may spawn a
next build peaking near 2GB.

Non-blocking on purpose (not asyncio.Semaphore): a blocked acquire would
hang the request with no way to explain why. release() clamps at zero since
close() and the idle timer can both fire for one session."
```

---

### Task 3: PrototypeBuilder — sdk_driver 이식

**Files:**
- Create: `backend/pathfinder/proto/builder.py`
- Create: `backend/tests/fakes/fake_sdk.py` (from `harness/tests/fake_sdk.py`)
- Create: `backend/tests/test_proto_builder.py` (from `harness/tests/test_sdk_driver.py`)
- Create: `backend/tests/test_proto_builder_questions.py` (from `harness/tests/test_sdk_driver_questions.py`)

**Interfaces:**
- Consumes: `pathfinder.models.AgentEvent` (Task 1의 SDK 임포트)
- Produces:
  - `class PrototypeBuilder(workspace: str, config_dir: str, session_id: str, resume: bool, session_store=None, anthropic_model: str | None = None, client_factory: Callable[[], Any] | None = None)`
  - `def run(self, text: str) -> AsyncIterator[AgentEvent]`
  - `async def interrupt(self) -> None`
  - `async def submit_answers(self, interrupt_id: str, answers: dict[str, str]) -> bool`
  - `async def pending(self) -> str | None`
  - `async def disconnect(self) -> None`
  - Task 5(`PrototypeSession`)가 이 전부를 사용한다.

- [ ] **Step 1: fake SDK 이식**

`harness/tests/fake_sdk.py`를 `backend/tests/fakes/fake_sdk.py`로 복사하고, `disconnect` 호출 추적을 더한다:

```python
# backend/tests/fakes/fake_sdk.py
"""Shape-compatible stand-ins for claude_agent_sdk message types + a scripted
client. builder.py matches on class NAME (type(msg).__name__), not isinstance,
precisely so these fakes work without importing the real SDK.

Ported from harness/tests/fake_sdk.py; `disconnect_calls` is new (the
in-process builder must be explicitly disconnected on idle/close, which the
VM era handled by stopping the whole VM)."""
from dataclasses import dataclass


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
        self.disconnect_calls = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for msg in self.script:
            yield msg

    async def interrupt(self):
        self.interrupt_calls += 1
```

- [ ] **Step 2: builder 테스트 이식 (실패하는 상태)**

`backend/tests/test_proto_builder.py` — `harness/tests/test_sdk_driver.py`의 이식본. 임포트를 `pathfinder.proto.builder`로 바꾸고, 생성자에 새 인자를 넣고, `@pytest.mark.asyncio`를 제거한다(백엔드 `pytest.ini`가 `asyncio_mode = auto`):

```python
# backend/tests/test_proto_builder.py — ported from harness/tests/test_sdk_driver.py.
# The driver logic is unchanged; only its home and constructor moved.
from __future__ import annotations

from pathfinder.proto.builder import PrototypeBuilder
from fakes.fake_sdk import (AssistantMessage, FakeSdkClient, ResultMessage,
                            TextBlock, ToolUseBlock)


def _builder(tmp_path, client, **kw):
    return PrototypeBuilder(
        workspace=str(tmp_path),
        config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555",
        resume=False,
        client_factory=lambda: client,
        **kw,
    )


async def collect(builder, text="go"):
    return [ev async for ev in builder.run(text)]


async def test_text_and_result_translate(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[TextBlock(text="working on it")]),
        ResultMessage(subtype="success"),
    ])
    b = _builder(tmp_path, client)
    events = await collect(b)
    kinds = [(e.kind, e.text) for e in events]
    assert ("message", "working on it") in kinds
    assert events[-1].kind == "done"
    assert client.queries == ["go"]


async def test_tool_use_status_deduped(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[ToolUseBlock(id="1", name="Bash", input={}),
                                  ToolUseBlock(id="2", name="Bash", input={})]),
        AssistantMessage(content=[ToolUseBlock(id="3", name="Write",
                                               input={"file_path": "x"})]),
        ResultMessage(),
    ])
    b = _builder(tmp_path, client)
    events = await collect(b)
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["Bash", "Write"]


async def test_client_error_yields_sanitized_error(tmp_path):
    class Boom(FakeSdkClient):
        async def receive_response(self):
            raise RuntimeError("AWS_SECRET=xyz leaked")
            yield  # pragma: no cover

    b = _builder(tmp_path, Boom())
    events = await collect(b)
    assert events[-1].kind == "error"
    assert "xyz" not in (events[-1].text or "")


async def test_second_turn_reuses_connected_client(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    await collect(b, "one")
    await collect(b, "two")
    assert client.queries == ["one", "two"]


async def test_turn_already_in_progress(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    b._turn_active = True
    events = await collect(b)
    assert events[0].kind == "error"
    assert "in progress" in events[0].text


async def test_post_tool_hook_emits_file_changed(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    assert b.drain_queue() == [
        __import__("pathfinder.models", fromlist=["AgentEvent"]).AgentEvent(
            kind="file_changed", path="prototype/app.js")]


async def test_post_tool_hook_rejects_escape(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
        "toolu_1", None)
    events = b.drain_queue()
    assert [e.kind for e in events] == ["status"]
    assert "outside workspace" in events[0].text


async def test_disconnect_closes_client_and_is_idempotent(tmp_path):
    """NEW vs the VM era: stopping the VM used to reclaim everything. Now the
    idle timer / close path must explicitly disconnect, or the claude
    subprocess keeps holding ~300-500MB."""
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    await collect(b)
    await b.disconnect()
    await b.disconnect()
    assert client.disconnect_calls == 1


async def test_disconnect_without_a_turn_is_a_noop(tmp_path):
    client = FakeSdkClient()
    b = _builder(tmp_path, client)
    await b.disconnect()
    assert client.disconnect_calls == 0
```

- [ ] **Step 3: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.proto.builder'`

- [ ] **Step 4: builder 구현**

`harness/sdk_driver.py`를 `backend/pathfinder/proto/builder.py`로 이식한다. 변경점만 아래에 명시하고, 나머지 메서드(`_to_question_file`, `_rel`, `_on_post_tool_use`, `_answer_to_sdk`, `_on_can_use_tool`, `_translate`, `run`, `interrupt`, `submit_answers`, `pending`, `drain_queue`)는 원본을 **그대로** 옮긴다 — 단 `from events import AgentEvent`를 `from pathfinder.models import AgentEvent`로 바꾼다.

파일 헤더와 클라이언트 팩토리, 그리고 `disconnect()`가 새로 쓰는 부분이다:

```python
# backend/pathfinder/proto/builder.py — the prototype build agent, running
# IN-PROCESS in the backend (was harness/sdk_driver.py inside a Tokyo MicroVM).
#
# One build session = one connected ClaudeSDKClient. Hook/tool callbacks run on
# the SDK's tasks while run() drains on the caller's loop -- both on the SAME
# event loop, so a plain list handoff is safe.
#
# Three things differ from the VM-era driver:
#   1. CLAUDE_CONFIG_DIR is always injected. The bundled binary is ordinary
#      Claude Code and reads ~/.claude when this is unset -- harmless in the
#      VM (empty home) but on the workshop EC2 that is the operator's personal
#      skills/agents/CLAUDE.md, which would leak into every workshop build and
#      make results depend on host config.
#   2. session_store + resume make the transcript durable, so a session can be
#      resumed days later or after a backend redeploy.
#   3. disconnect() exists. Stopping the VM used to reclaim the process; now
#      the idle timer must do it explicitly.
from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Callable

from pathfinder.models import AgentEvent

_log = logging.getLogger(__name__)

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_LETTERS = "ABCDEFGHIJ"


# ... _to_question_file() and _rel() copied verbatim from harness/sdk_driver.py
# (including _rel's docstring about absolute-path escapes) ...


def _default_client_factory(builder: "PrototypeBuilder") -> Callable[[], Any]:
    def make():
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from claude_agent_sdk.types import HookMatcher

        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            # Swap the config HOME rather than disabling settings entirely
            # (setting_sources=[]): this keeps a place to put OUR skills and
            # subagents later, and keeps the local transcript copy under a
            # Pathfinder-owned path instead of the operator's home.
            "CLAUDE_CONFIG_DIR": builder._config_dir,
        }
        if builder._anthropic_model:
            env["ANTHROPIC_MODEL"] = builder._anthropic_model
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=builder._workspace,
            env=env,
            # "user" now means OUR config dir, so this is safe -- and it is
            # what `skills=[...]` needs open when we eventually enable one.
            setting_sources=["user", "project"],
            session_id=builder._session_id,
            resume=builder._session_id if builder._resume else None,
            session_store=builder._session_store,
            can_use_tool=builder._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[builder._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


class PrototypeBuilder:
    def __init__(self, workspace: str, config_dir: str, session_id: str,
                 resume: bool, session_store: Any = None,
                 anthropic_model: str | None = None,
                 client_factory: Callable[[], Any] | None = None):
        self._workspace = workspace
        self._config_dir = config_dir
        self._session_id = session_id
        self._resume = resume
        self._session_store = session_store
        self._anthropic_model = anthropic_model
        self._factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        # A plain list, not collections.deque: tests assert `_queue == []`
        # after draining, and deque never compares equal to a list literal.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._interrupted = False
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None

    # ... drain_queue / _ensure_client / _on_post_tool_use / _answer_to_sdk /
    # _on_can_use_tool / _translate / run / interrupt / submit_answers /
    # pending copied verbatim from harness/sdk_driver.py ...

    async def disconnect(self) -> None:
        """Tear down the claude subprocess. Idempotent -- close() and the idle
        timer can both reach here."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("builder disconnect failed")
```

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_builder.py -q`
Expected: 9 passed

- [ ] **Step 6: 질문 왕복 테스트 이식**

`harness/tests/test_sdk_driver_questions.py`를 `backend/tests/test_proto_builder_questions.py`로 이식한다. `from sdk_driver import SdkDriver` → `from pathfinder.proto.builder import PrototypeBuilder`, `from tests.fake_sdk import ...` → `from fakes.fake_sdk import ...`, `SdkDriver(str(tmp_path), client_factory=...)` → 위 `_builder` 헬퍼와 같은 생성자 호출, `@pytest.mark.asyncio` 제거.

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_builder_questions.py -q`
Expected: 이식한 테스트 전부 통과

- [ ] **Step 7: 커밋**

```bash
git add backend/pathfinder/proto/builder.py backend/tests/test_proto_builder.py backend/tests/test_proto_builder_questions.py backend/tests/fakes/fake_sdk.py
git commit -m "feat(proto): PrototypeBuilder — sdk_driver ported in-process

Straight port of harness/sdk_driver.py: interrupt, AskUserQuestion
interception and the PostToolUse path guard were already written there, so
the turn-relay logic is unchanged and its tests came along with it.

Three real differences, all forced by leaving the VM:
- CLAUDE_CONFIG_DIR is always injected. The bundled binary is ordinary
  Claude Code and reads ~/.claude when unset — empty inside the VM, but on
  the workshop EC2 that is the operator's personal skills/agents/CLAUDE.md.
  Chose swapping the config HOME over setting_sources=[] so there is still a
  place to put our own skills later.
- session_id/resume/session_store are wired for durable context.
- disconnect() exists: stopping the VM used to reclaim the process."
```

---

### Task 4: S3SessionStore — transcript 미러링 어댑터

**Files:**
- Create: `backend/pathfinder/proto/session_store.py`
- Test: `backend/tests/test_proto_session_store.py`

**Interfaces:**
- Consumes: `pathfinder.s3store.S3StoreLike`
- Produces:
  - `class S3SessionStore(s3: S3StoreLike, slug: str)`
  - `async def append(self, key: dict, entries: list[dict]) -> None`
  - `async def load(self, key: dict) -> list[dict] | None`
  - `async def list_subkeys(self, key: dict) -> list[str]`
  - `def transcript_prefix(slug: str) -> str` — `f"prototypes/{slug}/transcript/"`
  - Task 5가 `S3SessionStore`를 builder에 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_proto_session_store.py`:

```python
# backend/tests/test_proto_session_store.py
from __future__ import annotations

from pathfinder.proto.session_store import S3SessionStore, transcript_prefix

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
SID = "11111111-2222-3333-4444-555555555555"
KEY = {"project_key": "proj-1/todo-app", "session_id": SID}


async def test_append_then_load_round_trips_entries_in_order():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)

    await store.append(KEY, [{"type": "user", "uuid": "u1"},
                             {"type": "assistant", "uuid": "a1"}])
    await store.append(KEY, [{"type": "assistant", "uuid": "a2"}])

    loaded = await store.load(KEY)
    assert [e["uuid"] for e in loaded] == ["u1", "a1", "a2"]


async def test_load_returns_none_for_a_session_never_written():
    store = S3SessionStore(FakeS3Store(), slug=SLUG)
    assert await store.load(KEY) is None


async def test_entries_survive_non_ascii_and_nesting():
    """Entries are opaque JSON blobs -- the only invariant the SDK requires is
    a json round-trip, so Korean text and nested objects must come back deep-
    equal."""
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    entry = {"type": "user", "uuid": "u1",
             "message": {"content": [{"type": "text", "text": "버튼 색 바꿔줘"}]}}

    await store.append(KEY, [entry])

    assert (await store.load(KEY)) == [entry]


async def test_batches_land_under_the_prototype_transcript_prefix():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    assert all(k.startswith(transcript_prefix(SLUG)) for k in s3.blobs)


async def test_subagent_subpath_is_stored_and_listed_separately():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    sub = {**KEY, "subpath": "subagents/agent-7"}

    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    await store.append(sub, [{"type": "user", "uuid": "s1"}])

    assert [e["uuid"] for e in await store.load(KEY)] == ["u1"]
    assert [e["uuid"] for e in await store.load(sub)] == ["s1"]
    assert await store.list_subkeys(KEY) == ["subagents/agent-7"]


async def test_list_subkeys_empty_when_no_subagents():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    await store.append(KEY, [{"type": "user", "uuid": "u1"}])
    assert await store.list_subkeys(KEY) == []


async def test_sessions_do_not_bleed_across_session_ids():
    s3 = FakeS3Store()
    store = S3SessionStore(s3, slug=SLUG)
    other = {**KEY, "session_id": "99999999-8888-7777-6666-555555555555"}

    await store.append(KEY, [{"type": "user", "uuid": "u1"}])

    assert await store.load(other) is None
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.proto.session_store'`

- [ ] **Step 3: 구현**

`backend/pathfinder/proto/session_store.py`:

```python
# backend/pathfinder/proto/session_store.py — SDK SessionStore adapter over S3.
#
# This is what makes prototype build context outlive a session: the SDK mirrors
# every transcript line here, and on resume it loads them back and materializes
# a temp JSONL for the subprocess. Without it the ClaudeSDKClient dies with the
# session and a follow-up "change that button" starts from zero.
#
# Only append/load/list_subkeys are implemented. The Protocol's other methods
# raise NotImplementedError by default, and our call path never reaches them:
# we always pass an explicit `resume` (never continue_conversation), which is
# what would otherwise force list_sessions(); and deletion is handled by the
# project-delete path wiping the whole S3 prefix, so a WORM-style no-op here is
# correct.
#
# Batch ordering: each append() writes ONE object whose key sorts after every
# earlier one, so load() can restore order by sorting keys. A monotonic
# in-instance counter (not a timestamp) does that -- timestamps would collide
# at the SDK's ~100ms batch cadence.
from __future__ import annotations

import json

from pathfinder.s3store import S3StoreLike


def transcript_prefix(slug: str) -> str:
    return f"prototypes/{slug}/transcript/"


def _session_prefix(slug: str, key: dict) -> str:
    base = f"{transcript_prefix(slug)}{key['session_id']}/"
    subpath = key.get("subpath")
    # `subpath` is opaque to adapters -- use it as a storage key suffix only.
    # "main/" keeps the main transcript from sharing a prefix with a subagent
    # whose subpath could otherwise start with the same characters.
    return f"{base}sub/{subpath}/" if subpath else f"{base}main/"


class S3SessionStore:
    def __init__(self, s3: S3StoreLike, slug: str):
        self._s3 = s3
        self._slug = slug
        self._seq = 0

    async def append(self, key: dict, entries: list[dict]) -> None:
        if not entries:
            return
        self._seq += 1
        blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
        await self._s3.put(
            f"{_session_prefix(self._slug, key)}{self._seq:08d}.jsonl", blob)

    async def load(self, key: dict) -> list[dict] | None:
        prefix = _session_prefix(self._slug, key)
        keys = await self._s3.list(prefix)
        if not keys:
            return None  # never written (or emptied -- the SDK treats both the same)
        entries: list[dict] = []
        for k in sorted(keys):
            body = await self._s3.get(k)
            entries.extend(json.loads(line) for line in body.splitlines() if line)
        return entries

    async def list_subkeys(self, key: dict) -> list[str]:
        base = f"{transcript_prefix(self._slug)}{key['session_id']}/sub/"
        found: list[str] = []
        for k in await self._s3.list(base):
            subpath = k[len(base):].rsplit("/", 1)[0]
            if subpath and subpath not in found:
                found.append(subpath)
        return sorted(found)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session_store.py -q`
Expected: 7 passed

- [ ] **Step 5: moto로 실 S3Store 위에서 재확인하는 테스트 추가**

`backend/tests/test_proto_session_store.py` 끝에 추가한다. `FakeS3Store`가 실 `S3Store`의 계약을 잘못 흉내내면 여기서 잡힌다:

```python
async def test_round_trip_over_the_real_S3Store_shape():
    """FakeS3Store could drift from S3Store's contract (key namespacing,
    sorted list, FileNotFoundError). Run the same round trip against a real
    boto3 client backed by moto."""
    import boto3
    from moto import mock_aws
    from pathfinder.s3store import S3Store

    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        s3 = S3Store(bucket="pf-test", prefix="projects/proj-1/", client=client)
        store = S3SessionStore(s3, slug=SLUG)

        await store.append(KEY, [{"type": "user", "uuid": "u1"}])
        await store.append(KEY, [{"type": "assistant", "uuid": "a1"}])

        assert [e["uuid"] for e in await store.load(KEY)] == ["u1", "a1"]
        assert await store.load({**KEY, "session_id": SID.replace("1", "7")}) is None
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session_store.py -q`
Expected: 8 passed

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/proto/session_store.py backend/tests/test_proto_session_store.py
git commit -m "feat(proto): S3SessionStore — durable build transcripts

This is the piece that makes build context outlive a session. The SDK mirrors
every transcript line here and materializes it back on resume, so 'change that
button' works days later and across a backend redeploy.

Only append/load/list_subkeys are implemented: we always pass an explicit
resume (never continue_conversation, which is what would force
list_sessions), and project deletion already wipes the S3 prefix, so the
Protocol's no-op default for delete is correct.

Each append writes one key-sortable object using a monotonic counter rather
than a timestamp — the SDK batches at ~100ms, where timestamps collide.
Verified against both FakeS3Store and a real S3Store on moto."
```

---

### Task 5: PrototypeSession 재배선 + VM 코드 삭제

**Files:**
- Modify: `backend/pathfinder/proto/session.py` (전면 개편)
- Delete: `backend/pathfinder/proto/vm.py`
- Delete: `backend/pathfinder/proto/harness_client.py`
- Delete: `backend/tests/test_proto_vm.py`
- Delete: `backend/tests/test_proto_harness_client.py`
- Modify: `backend/tests/test_proto_session.py` (전면 개편)

**Interfaces:**
- Consumes: `PrototypeBuilder`(Task 3), `S3SessionStore`(Task 4), `BuildSemaphore`(Task 2)
- Produces:
  - `class PrototypeSession(project_id, slug, s3, build_root: Path, builder_factory: Callable[[str, bool], BuilderLike], semaphore, idle_seconds=1800)`
  - `async def start(self) -> None` — 세션 UUID 확보 → 워크스페이스 준비 → builder 생성
  - `async def send_message(text) -> AsyncIterator[AgentEvent]`, `async def send_answers(answers) -> bool`, `async def interrupt()`, `async def close()`, `def first_prompt() -> str` — 시그니처 무변경
  - `def build_dir(self) -> Path`
  - Task 6(host가 `build_dir` 규약 공유), Task 7(route)이 사용

- [ ] **Step 1: 세션 테스트 재작성 (실패하는 상태)**

`backend/tests/test_proto_session.py`를 아래로 **교체**한다. 살아남는 시나리오(질문 소유권, 상태 전이, 유휴 타이머, close 멱등성)는 유지하고, VM 시나리오(boot/토큰 민팅/stop)는 폐기하고, 새 시나리오(UUID 영속, resume 판단, 세마포어 반납, disconnect)를 추가한다:

```python
# backend/tests/test_proto_session.py — PrototypeSession over an in-process
# builder. VM scenarios (boot/token-mint/stop) are gone with vm.py; the
# question-ownership, status-transition, idle-timer and close-idempotency
# scenarios carried over unchanged in intent.
from __future__ import annotations

import asyncio
import json

import pytest

from pathfinder.models import AgentEvent
from pathfinder.proto.limits import BuildSemaphore
from pathfinder.proto.session import PrototypeSession

from fakes.in_memory_s3 import FakeS3Store

SLUG = "todo-app"
PROJECT_ID = "proj-1"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
SESSION_KEY = f"prototypes/{SLUG}/session.json"


class FakeBuilder:
    def __init__(self):
        self.queries: list[str] = []
        self.answer_calls: list[tuple[str, dict]] = []
        self.interrupt_calls = 0
        self.disconnect_calls = 0
        self.submit_result = True
        self._script: list[AgentEvent] = []

    def script(self, events: list[AgentEvent]) -> None:
        self._script = events

    async def run(self, text: str):
        self.queries.append(text)
        for ev in self._script:
            yield ev

    async def submit_answers(self, interrupt_id: str, answers: dict) -> bool:
        self.answer_calls.append((interrupt_id, answers))
        return self.submit_result

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def pending(self) -> str | None:
        return None

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


def _session(s3, tmp_path, builder, semaphore=None, idle_seconds=1800):
    calls: list[bool] = []

    def factory(session_id: str, resume: bool):
        calls.append(resume)
        return builder

    session = PrototypeSession(
        project_id=PROJECT_ID, slug=SLUG, s3=s3,
        build_root=tmp_path / "protos",
        builder_factory=factory,
        semaphore=semaphore or BuildSemaphore(max_concurrent=2),
        idle_seconds=idle_seconds,
    )
    session._test_resume_calls = calls  # type: ignore[attr-defined]
    return session


# ---- start(): session id persistence + resume decision ----

async def test_start_generates_and_persists_a_uuid_session_id(tmp_path):
    import uuid

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    saved = json.loads(s3.blobs[SESSION_KEY])
    uuid.UUID(saved["session_id"])          # must be a REAL uuid: SDK rejects others
    assert session._test_resume_calls == [False]   # first start: nothing to resume
    assert session.status == "ready"


async def test_start_reuses_the_saved_session_id_and_resumes(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps(
        {"session_id": "11111111-2222-3333-4444-555555555555"})
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert session._test_resume_calls == [True]
    assert json.loads(s3.blobs[SESSION_KEY])["session_id"] == \
        "11111111-2222-3333-4444-555555555555"


async def test_start_regenerates_when_the_saved_id_is_not_a_uuid(tmp_path):
    """A hand-edited or legacy session.json must not wedge the session: the
    SDK would reject a non-UUID resume value outright."""
    import uuid

    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[SESSION_KEY] = json.dumps({"session_id": "proj-1-todo-app"})
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    uuid.UUID(json.loads(s3.blobs[SESSION_KEY])["session_id"])
    assert session._test_resume_calls == [False]


async def test_start_raises_file_not_found_when_spec_missing(tmp_path):
    s3 = FakeS3Store()
    session = _session(s3, tmp_path, FakeBuilder())
    with pytest.raises(FileNotFoundError):
        await session.start()


async def test_start_creates_the_build_directory(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert session.build_dir().is_dir()
    assert session.build_dir() == tmp_path / "protos" / PROJECT_ID / SLUG


async def test_start_writes_the_spec_into_the_build_directory(tmp_path):
    """The agent reads the spec with its own file tools from cwd, so the spec
    must exist on local disk -- the VM era pushed it over HTTP instead."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec body"
    session = _session(s3, tmp_path, FakeBuilder())

    await session.start()

    assert (session.build_dir() / SPEC_KEY).read_text(encoding="utf-8") == "# spec body"


# ---- turn relay: status transitions + question ownership ----

async def test_send_message_relays_events_and_returns_to_ready(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    builder.script([AgentEvent(kind="message", text="building..."),
                    AgentEvent(kind="done")])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["message", "done"]
    assert session.status == "ready"


async def test_send_message_sets_waiting_input_on_questions_event(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    builder.script([AgentEvent(kind="questions", payload=payload)])
    seen = [ev async for ev in session.send_message("go")]

    assert [e.kind for e in seen] == ["questions"]
    assert session.status == "waiting_input"


async def test_send_answers_consumes_pending_interrupt_id(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    payload = json.dumps({"interrupt_id": "iid-1", "questions": {"name": "q"}})
    builder.script([AgentEvent(kind="questions", payload=payload)])
    [ev async for ev in session.send_message("go")]

    assert await session.send_answers({"1": "A"}) is True
    assert builder.answer_calls == [("iid-1", {"1": "A"})]
    assert session.status == "building"
    assert await session.send_answers({"1": "B"}) is False   # consumed


async def test_send_answers_false_when_nothing_pending(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder)
    await session.start()

    assert await session.send_answers({"1": "A"}) is False
    assert builder.answer_calls == []


# ---- close(): disconnect + semaphore release, NOT a context wipe ----

async def test_close_disconnects_and_releases_the_slot(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=1)
    assert sem.try_acquire() is True          # route acquires before start()
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    await session.close()

    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 0
    assert session.status == "closed"


async def test_close_keeps_the_build_directory_and_session_id(tmp_path):
    """Closing must NOT reset context: the transcript id and the built files
    are what a later resume stands on."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    session = _session(s3, tmp_path, FakeBuilder())
    await session.start()
    (session.build_dir() / "prototype").mkdir(parents=True, exist_ok=True)
    (session.build_dir() / "prototype" / "app.js").write_text("x", encoding="utf-8")

    await session.close()

    assert (session.build_dir() / "prototype" / "app.js").is_file()
    assert SESSION_KEY in s3.blobs


async def test_close_is_idempotent_and_releases_only_once(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=2)
    sem.try_acquire()
    sem.try_acquire()
    session = _session(s3, tmp_path, builder, semaphore=sem)
    await session.start()

    await session.close()
    await session.close()

    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 1   # the OTHER holder still counts


async def test_close_releases_the_slot_even_if_disconnect_fails(tmp_path):
    """A wedged subprocess must not permanently consume a build slot."""
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"

    class BoomBuilder(FakeBuilder):
        async def disconnect(self):
            raise RuntimeError("subprocess wedged")

    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    session = _session(s3, tmp_path, BoomBuilder(), semaphore=sem)
    await session.start()

    await session.close()      # must NOT raise

    assert sem.snapshot()["active_builds"] == 0
    assert session.status == "failed"


# ---- idle timer ----

async def test_idle_timer_auto_closes_and_frees_the_slot(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    sem = BuildSemaphore(max_concurrent=1)
    sem.try_acquire()
    session = _session(s3, tmp_path, builder, semaphore=sem, idle_seconds=0.05)

    await session.start()
    await asyncio.sleep(0.2)

    assert session.status == "closed"
    assert builder.disconnect_calls == 1
    assert sem.snapshot()["active_builds"] == 0


async def test_idle_timer_resets_on_send_message(tmp_path):
    s3 = FakeS3Store()
    s3.blobs[SPEC_KEY] = "# spec"
    builder = FakeBuilder()
    session = _session(s3, tmp_path, builder, idle_seconds=0.1)
    await session.start()

    await asyncio.sleep(0.06)
    builder.script([AgentEvent(kind="done")])
    [ev async for ev in session.send_message("go")]

    await asyncio.sleep(0.06)
    assert session.status == "ready"      # would be "closed" without the reset

    await asyncio.sleep(0.12)
    assert session.status == "closed"


# ---- first_prompt(): directives, now without the /workspace path ----

def test_first_prompt_covers_the_build_directives(tmp_path):
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())

    prompt = session.first_prompt()

    assert SPEC_KEY in prompt
    assert "AskUserQuestion" in prompt
    assert "prototype/" in prompt
    assert "README" in prompt
    assert f"/api/proto/{PROJECT_ID}/{SLUG}/" in prompt
    assert "basePath" in prompt or "상대 경로" in prompt
    assert "Bedrock" in prompt
    assert "하드코딩" in prompt


def test_first_prompt_no_longer_names_the_vm_absolute_path(tmp_path):
    """The VM's /workspace/ mount is gone; cwd is the build directory."""
    session = _session(FakeS3Store(), tmp_path, FakeBuilder())
    assert "/workspace/" not in session.first_prompt()
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -q`
Expected: FAIL — `TypeError: PrototypeSession.__init__() got an unexpected keyword argument 'build_root'`

- [ ] **Step 3: session.py 재작성**

`backend/pathfinder/proto/session.py`를 교체한다. 유지: `_interrupt_id_from`, 상태 리터럴, 유휴 타이머 패턴, 질문 소유권, `first_prompt`의 5개 지침. 제거: `MicroVMControllerLike`/`HarnessClientLike` Protocol, `BootSpec`/`VMHandle` 임포트, `_mint_headers`, 룰 push, 번들 push/pull, `_EXCLUDED_SEGMENTS`(Task 8의 zip으로 이동).

```python
# backend/pathfinder/proto/session.py — PrototypeSession: one prototype build
# session's orchestration.
#
# Post-MicroVM shape: no boot, no HTTP file push, no VM stop. What remains is
# (1) resolving the durable session id so context resumes, (2) making sure the
# build directory and the spec exist on local disk for the agent's own file
# tools, (3) relaying turns, (4) the idle timer -- which now reclaims a ~300-
# 500MB subprocess and a build slot rather than a VM.
#
# Closing a session no longer destroys context: the transcript lives in S3 and
# the build directory stays on disk, so the next start() resumes.
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Literal, Protocol

from pathfinder.models import AgentEvent
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        "failed", "closed"]


class BuilderLike(Protocol):
    def run(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool: ...
    async def interrupt(self) -> None: ...
    async def pending(self) -> str | None: ...
    async def disconnect(self) -> None: ...


class SemaphoreLike(Protocol):
    def try_acquire(self) -> bool: ...
    def release(self) -> None: ...
    def snapshot(self) -> dict[str, int]: ...


def _interrupt_id_from(payload: str | None) -> str | None:
    """Parse the interrupt id out of a questions payload. Mirrors runner.py --
    a malformed/contract-drifted payload must degrade (None) rather than blow
    up the turn relay."""
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class PrototypeSession:
    """One prototype's build session: owns the durable session id, the build
    directory, the turn relay, the questions interrupt id, and the idle timer.
    """

    def __init__(
        self,
        project_id: str,
        slug: str,
        s3: S3StoreLike,
        build_root: Path,
        builder_factory: Callable[[str, bool], BuilderLike],
        semaphore: SemaphoreLike,
        idle_seconds: int | float = 1800,
    ):
        self.project_id = project_id
        self.slug = slug
        self._s3 = s3
        self._build_root = Path(build_root)
        self._builder_factory = builder_factory
        self._semaphore = semaphore
        self._idle_seconds = idle_seconds

        self.status: SessionStatus = "starting"
        self._builder: BuilderLike | None = None
        self._session_id: str | None = None
        self._pending_interrupt_id: str | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._closed = False

    # ---- path/key helpers ----

    def _spec_key(self) -> str:
        return f"aiplc-docs/discovery/prototypes/{self.slug}/PROTOTYPE-{self.slug}.md"

    def _session_key(self) -> str:
        return f"prototypes/{self.slug}/session.json"

    def build_dir(self) -> Path:
        return self._build_root / self.project_id / self.slug

    # ---- durable session id ----

    async def _resolve_session_id(self) -> tuple[str, bool]:
        """Return (session_id, resume). A saved id means resume; a missing or
        non-UUID one means start fresh -- the SDK rejects a non-UUID resume
        value outright, so a legacy/hand-edited value must not wedge the
        session."""
        try:
            saved = json.loads(await self._s3.get(self._session_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = None
        if isinstance(saved, dict) and _is_uuid(saved.get("session_id")):
            return saved["session_id"], True
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        return new_id, False

    # ---- idle timer ----

    def _arm_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(self._idle_seconds, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    # ---- start ----

    async def start(self) -> None:
        spec_md = await self._s3.get(self._spec_key())  # FileNotFoundError -> route 404

        self._session_id, resume = await self._resolve_session_id()

        # The agent reads the spec with its own file tools from cwd, so it has
        # to exist on local disk (the VM era pushed it over HTTP instead).
        # Refreshed on every start so a spec edited in Discovery is picked up.
        build_dir = self.build_dir()
        spec_path = build_dir / self._spec_key()
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_md, encoding="utf-8")

        self._builder = self._builder_factory(self._session_id, resume)
        self.status = "ready"
        self._arm_idle_timer()

    # ---- turn relay ----

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        assert self._builder is not None, "start() must be called before send_message()"
        self._arm_idle_timer()
        self.status = "building"
        try:
            async for event in self._builder.run(text):
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                        self.status = "waiting_input"
                elif event.kind == "done":
                    self.status = "ready"
                elif event.kind == "error":
                    # Sanitized turn-level error: session stays usable and
                    # retryable -- NOT a session failure.
                    self.status = "ready"
                yield event
        except Exception:
            self.status = "failed"
            raise

    async def send_answers(self, answers: dict[str, str]) -> bool:
        assert self._builder is not None, "start() must be called before send_answers()"
        if self._pending_interrupt_id is None:
            return False
        interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
        ok = await self._builder.submit_answers(interrupt_id, answers)
        if not ok:
            return False
        self._arm_idle_timer()
        self.status = "building"
        return True

    async def interrupt(self) -> None:
        if self._builder is not None:
            await self._builder.interrupt()

    # ---- close: disconnect + release the slot. Context is NOT discarded. ----

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

        ok = True
        if self._builder is not None:
            try:
                await self._builder.disconnect()
            except Exception:
                # A wedged subprocess must not keep the build slot forever --
                # log it, mark the session failed, and still release below.
                _log.exception("builder disconnect failed: %s/%s",
                               self.project_id, self.slug)
                ok = False
            self._builder = None

        self._semaphore.release()
        self.status = "closed" if ok else "failed"

    # ---- first turn's auto-spoken prompt ----

    def first_prompt(self) -> str:
        spec_key = self._spec_key()
        proxy_path = f"/api/proto/{self.project_id}/{self.slug}/"
        return (
            f"`{spec_key}` 파일을 읽고, 그 내용에 따라 프로토타입을 빌드해줘.\n\n"
            "지침:\n"
            f"1. 먼저 `{spec_key}`를 읽고 요구사항을 정확히 파악한 뒤 빌드를 시작해줘.\n"
            "2. 진행 중 불확실하거나 결정이 필요한 사항이 있으면 마음대로 넘기지 말고, "
            "AskUserQuestion으로 나에게 먼저 물어봐줘.\n"
            "3. 완성물은 반드시 작업 디렉토리 아래 `prototype/`에 두고, 빌드 방법과 "
            "실행 방법을 설명하는 README를 함께 작성해줘.\n"
            f"4. 이 프로토타입은 경로 프록시(예: `{proxy_path}`) 하위 경로에서 서빙돼. "
            "basePath와 상대 경로를 사용해서, 어떤 하위 경로에 배치되어도 정상 동작하도록 "
            "구현해줘(절대 경로 하드코딩 금지).\n"
            "5. 코드에서 LLM 호출이 필요하면 Amazon Bedrock을 기본 자격증명 체인(인스턴스/"
            "실행 롤)으로 사용해줘. API 키를 코드에 하드코딩하지 말고, 리전과 모델 ID는 "
            "환경변수로 받도록 구현해줘.\n"
        )
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -q`
Expected: 18 passed

- [ ] **Step 5: VM 모듈과 그 테스트 삭제**

```bash
cd /home/ec2-user/project/pathfinder-sp
git rm backend/pathfinder/proto/vm.py backend/pathfinder/proto/harness_client.py
git rm backend/tests/test_proto_vm.py backend/tests/test_proto_harness_client.py
```

- [ ] **Step 6: 잔여 참조 확인**

Run: `cd backend && grep -rn "proto.vm\|proto import vm\|harness_client\|BootSpec\|VMHandle\|mint_harness_token\|FakeMicroVMController" pathfinder/ tests/`
Expected: `app.py`의 `proto_session_factory`만 남는다(Task 7에서 정리). 그 외 히트가 있으면 지운다.

- [ ] **Step 7: 커밋**

```bash
git add -A backend/pathfinder/proto backend/tests/test_proto_session.py
git commit -m "refactor(proto): PrototypeSession over an in-process builder; delete the VM layer

start() loses VM boot, token minting and HTTP file push. What it gains is
resolving the durable session id: a saved UUID means resume, and a missing or
non-UUID one means start fresh — the SDK rejects a non-UUID resume value, so a
legacy or hand-edited session.json must not wedge the prototype.

The spec is now written to local disk instead of pushed over HTTP, because the
agent reads it with its own file tools from cwd. Refreshed each start so a spec
edited in Discovery is picked up.

close() inverts meaning: it disconnects a ~300-500MB subprocess and frees a
build slot, but deliberately keeps the build directory and session id — the
transcript is what a later resume stands on. The slot is released even when
disconnect fails, so a wedged subprocess can't consume one forever.

Deletes vm.py, harness_client.py and their tests."
```

---

### Task 6: ProtoHost — in-place 호스팅

**Files:**
- Modify: `backend/pathfinder/proto/host.py`
- Modify: `backend/tests/test_proto_host.py`

**Interfaces:**
- Consumes: Task 5의 빌드 디렉토리 규약 (`{root}/{pid}/{slug}/`)
- Produces:
  - `ProtoHost(root: Path, port_range=range(4001, 4051))` — **`s3` 인자 제거**
  - `async def start(self, pid, slug, cwd: Path | None = None) -> HostInfo`
  - `async def stop`, `def status`, `def log_tail` — 시그니처 무변경
  - `def sweep_orphans(self) -> int` — 기동 시 pid 파일 기반 정리
  - Task 7(route)이 사용

- [ ] **Step 1: 새 테스트 추가 (실패하는 상태)**

`backend/tests/test_proto_host.py` 끝에 추가한다:

```python
# ---- in-place hosting (post-MicroVM): the build directory IS the served tree ----

async def test_start_serves_an_existing_directory_without_wiping_it(root):
    """The regression this replaces: start() used to rmtree the target and
    re-download from S3. With the builder writing into that same directory,
    that would delete a live build."""
    target = root / PID / SLUG
    target.mkdir(parents=True)
    for path in FIXTURE_DIR.iterdir():
        if path.is_file():
            (target / path.name).write_text(path.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    marker = target / "AGENT_WORK_IN_PROGRESS.txt"
    marker.write_text("do not delete me", encoding="utf-8")

    host = ProtoHost(root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    try:
        assert info.state == "running"
        assert marker.read_text(encoding="utf-8") == "do not delete me"
    finally:
        await host.stop(PID, SLUG)


async def test_start_404s_when_the_directory_does_not_exist(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    with pytest.raises(FileNotFoundError):
        await host.start(PID, "never-built")


async def test_port_reservation_prevents_two_hosts_picking_one_port(root):
    """The old scanner closed its probe socket before spawning, so two
    concurrent starts could pick the same port. Reservations are recorded in
    the registry and skipped by later scans."""
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    for slug in ("a", "b"):
        target = root / PID / slug
        target.mkdir(parents=True)
        for path in FIXTURE_DIR.iterdir():
            if path.is_file():
                (target / path.name).write_text(path.read_text(encoding="utf-8"),
                                                encoding="utf-8")
    try:
        first = await host.start(PID, "a")
        second = await host.start(PID, "b")
        assert first.port != second.port
    finally:
        await host.stop(PID, "a")
        await host.stop(PID, "b")


async def test_start_writes_a_pid_file_and_removes_it_on_stop(root):
    target = root / PID / SLUG
    target.mkdir(parents=True)
    for path in FIXTURE_DIR.iterdir():
        if path.is_file():
            (target / path.name).write_text(path.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.start(PID, SLUG)
    pid_file = target / ".proto-host.pid"
    assert pid_file.is_file()
    assert pid_file.read_text(encoding="utf-8").strip().isdigit()

    await host.stop(PID, SLUG)
    assert not pid_file.exists()


def test_sweep_orphans_removes_stale_pid_files(root):
    """Backend restart leaves the previous run's children behind -- this is the
    replacement for the orphan-VM sweep that went away with the VM layer. A pid
    that no longer exists just has its file cleaned up."""
    target = root / PID / SLUG
    target.mkdir(parents=True)
    (target / ".proto-host.pid").write_text("99999999", encoding="utf-8")

    host = ProtoHost(root=root, port_range=range(4001, 4010))
    swept = host.sweep_orphans()

    assert swept == 1
    assert not (target / ".proto-host.pid").exists()
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_host.py -q`
Expected: FAIL — `TypeError: ProtoHost.__init__() missing 1 required positional argument: 's3'`

- [ ] **Step 3: host.py 개조**

변경 지점 4개:

(a) `__init__`에서 `s3`와 `_store`/`_download_bundle`를 제거하고 포트 예약 집합을 추가한다:

```python
    def __init__(self, root: Path, port_range: range = range(4001, 4051)):
        # No `s3`: the build directory IS the served tree now (the builder
        # writes straight into it), so hosting no longer round-trips a bundle
        # through S3 -- which also means binary assets stop being mangled by
        # the text-only store.
        self._root = Path(root)
        self._port_range = port_range
        self._registry: dict[tuple[str, str], _HostEntry] = {}
        # Ports handed out but whose subprocess may not be listening yet. The
        # scanner's bind probe releases its socket before the spawn, so two
        # concurrent starts could otherwise pick the same port.
        self._reserved: set[int] = set()
```

(b) `_scan_port`를 예약 인식형 메서드로 바꾼다:

```python
    def _scan_port(self) -> int:
        for port in self._port_range:
            if port in self._reserved:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                self._reserved.add(port)
                return port
            except OSError:
                continue
            finally:
                sock.close()
        raise RuntimeError(f"no free port in {self._port_range}")
```

(c) `start()`의 앞부분에서 `rmtree`/`_download_bundle`를 없애고, 디렉토리 존재를 요구하고, pid 파일을 쓴다:

```python
    async def start(self, pid: str, slug: str, cwd: Path | None = None) -> HostInfo:
        await self.stop(pid, slug)

        target_dir = Path(cwd) if cwd is not None else self._root / pid / slug
        # NOT rmtree + re-download: the builder writes into this very
        # directory, so wiping it would delete a live build.
        if not target_dir.is_dir():
            raise FileNotFoundError(str(target_dir))
        log_path = target_dir / ".proto-host.log"
        log_path.touch()

        entry = _HostEntry(dir=target_dir, log_path=log_path, state="installing")
        self._registry[(pid, slug)] = entry
        # ... npm install / build / port scan as before, then:
```

기동 부분에서 `_scan_port()` 호출을 인자 없는 새 형태로 바꾸고, 자식을 새 세션으로 띄우고 pid를 기록한다:

```python
        port = self._scan_port()
        start_args = ["run", "start"] if "start" in scripts else ["run", "dev"]
        env = {**os.environ, "PORT": str(port)}

        log_fh = open(log_path, "ab")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", *start_args, cwd=str(target_dir), env=env,
                stdout=log_fh, stderr=log_fh,
                # Own process group: stop() can then signal the whole tree,
                # and a hard backend death leaves a pid file for sweep_orphans
                # instead of an untracked child.
                start_new_session=True,
            )
        finally:
            log_fh.close()

        entry.port = port
        entry.proc = proc
        (target_dir / ".proto-host.pid").write_text(str(proc.pid), encoding="utf-8")
```

(d) `stop()`에서 프로세스 그룹을 죽이고 예약·pid 파일을 정리한다. 그리고 `sweep_orphans`를 추가한다:

```python
    async def stop(self, pid: str, slug: str) -> None:
        entry = self._registry.get((pid, slug))
        if entry is None:
            return  # unknown (pid, slug) -- idempotent no-op
        proc = entry.proc
        if proc is not None and proc.returncode is None:
            # npm spawns the real server as a child, so signal the GROUP --
            # terminating npm alone orphans the listener and leaks the port.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                await proc.wait()
        if entry.port is not None:
            self._reserved.discard(entry.port)
        (entry.dir / ".proto-host.pid").unlink(missing_ok=True)
        entry.proc = None
        entry.state = "stopped"

    def sweep_orphans(self) -> int:
        """Kill hosting processes left over from a previous backend run and
        clean up their pid files. Replaces the orphan-VM sweep that went away
        with the VM layer: an in-process build's children are OUR children, so
        a hard backend death leaves them holding CPU and ports.

        Best effort -- a pid that no longer exists (or was recycled onto
        something we don't own) only costs a stale file."""
        swept = 0
        if not self._root.is_dir():
            return 0
        for pid_file in self._root.glob("*/*/.proto-host.pid"):
            try:
                target = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
                continue
            try:
                os.killpg(os.getpgid(target), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass  # already gone, or not ours to signal
            pid_file.unlink(missing_ok=True)
            swept += 1
        return swept
```

임포트에 `signal`을 추가하고, `shutil`은 더 이상 쓰지 않으면 제거한다.

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_host.py -q`
Expected: 기존 + 신규 테스트 통과. 실패하는 기존 테스트(S3 번들 다운로드 전제)는 in-place 전제로 고친다 — `_seed_bundle` 헬퍼를 아래 `_seed_build_dir`로 대체하고 호출처를 바꾼다:

```python
def _seed_build_dir(root: Path, pid: str = PID, slug: str = SLUG,
                    fixture_dir: Path = FIXTURE_DIR) -> Path:
    target = root / pid / slug
    target.mkdir(parents=True, exist_ok=True)
    for path in fixture_dir.iterdir():
        if path.is_file():
            (target / path.name).write_text(path.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    return target
```

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/proto/host.py backend/tests/test_proto_host.py
git commit -m "refactor(proto): host the build directory in place

start() no longer rmtrees the target and re-downloads from S3. With the
builder writing into that same directory, the old path would have deleted a
live build — and dropping the S3 round trip also stops the text-only store
from mangling binary assets on the serving path.

Two adjacent leaks fixed while here:
- Port scanning released its probe socket before spawning, so concurrent
  starts could pick the same port. Ports are now reserved in the registry.
- npm spawns the real server as a child, so stop() signals the process group;
  terminating npm alone orphaned the listener and leaked the port.

sweep_orphans() replaces the orphan-VM sweep: in-process builds' children are
our children, so a hard backend death used to leave them holding CPU."
```

---

### Task 7: 라우트 + app 배선 — 세마포어 429, VM 배선 제거

**Files:**
- Modify: `backend/pathfinder/routes/prototypes.py`
- Modify: `backend/pathfinder/app.py`
- Modify: `backend/tests/test_routes_prototypes.py`

**Interfaces:**
- Consumes: Task 2/3/4/5/6 전부
- Produces:
  - `app_module.build_semaphore` — `BuildSemaphore` 싱글턴
  - `app_module.proto_session_factory(pid, slug)` — builder/store 조립 (시그니처 무변경)
  - `app_module.proto_host()` — `s3` 없는 `ProtoHost` 싱글턴
  - `GET /projects/{pid}/prototypes`에 `active_builds`/`max_builds` 노출
  - Task 8(zip 라우트)이 같은 파일에 추가

- [ ] **Step 1: 라우트 테스트 수정 (실패하는 상태)**

`backend/tests/test_routes_prototypes.py`의 `proto_env` fixture에서 VM env 두 줄을 제거하고 세마포어를 리셋한다:

```python
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    # VM env vars are gone -- the session route's config guard now checks a
    # build slot, not an image ARN.
    fake_s3 = FakeS3Store()
    ...
    monkeypatch.setattr(app_module, "build_semaphore",
                        BuildSemaphore(max_concurrent=2))
```

`FakeProtoHost`의 `start`가 `cwd` 키워드를 받도록 고치고, 아래 테스트를 추가한다:

```python
def test_session_start_429_when_the_cap_is_reached(proto_env, monkeypatch):
    """Third concurrent build is refused, not queued -- and the message has to
    say why, since a bare 429 reads as a bug to a workshop attendee."""
    monkeypatch.setattr(app_module, "build_semaphore",
                        BuildSemaphore(max_concurrent=1))
    _seed_spec(proto_env["s3"])
    _install_session_factory(monkeypatch, FakePrototypeSession())

    first = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert first.status_code == 202

    proto_env["s3"].blobs[
        "aiplc-docs/discovery/prototypes/other/PROTOTYPE-other.md"] = "# other"
    second = client.post(f"/projects/{PID}/prototypes/other/session")
    assert second.status_code == 429
    assert "빌드" in second.json()["detail"]


def test_session_start_releases_the_slot_when_start_fails(proto_env, monkeypatch):
    """A failed start must not burn a slot permanently -- otherwise two bad
    attempts wedge the whole backend at cap 2."""
    sem = BuildSemaphore(max_concurrent=1)
    monkeypatch.setattr(app_module, "build_semaphore", sem)
    _seed_spec(proto_env["s3"])
    _install_session_factory(
        monkeypatch, FakePrototypeSession(start_exc=RuntimeError("boom")))

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/session").status_code == 502
    assert sem.snapshot()["active_builds"] == 0


def test_list_reports_build_capacity(proto_env):
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["active_builds"] == 0
    assert body["max_builds"] == 2
    assert [p["slug"] for p in body["prototypes"]] == [SLUG]


def test_host_start_409_while_a_build_session_is_live(proto_env, monkeypatch):
    """Hosting used to wipe and re-download the directory; now it serves the
    build directory in place, so starting it under a live build must be
    refused rather than racing the agent."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")
    assert resp.status_code == 409
```

`test_session_start_503_when_vm_image_unset`, `test_session_start_503_when_role_arn_malformed`, `test_session_start_503_when_role_arn_unset` 세 개는 삭제한다(가드 자체가 사라졌다).

`list_prototypes` 응답이 리스트 → 오브젝트로 바뀌므로 기존 `test_list_*` 테스트의 단언을 `resp.json()["prototypes"]`로 고친다.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -q`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute 'build_semaphore'`

- [ ] **Step 3: app.py 배선 교체**

`proto_session_factory`, `proto_host`, `_cleanup_orphan_vms`, `_proto_http_client`를 아래로 교체한다. **survey 배선 블록(`surveys_root_s3_factory` 이하)은 건드리지 않는다.**

```python
# ---- prototype build/hosting wiring (routes/prototypes.py) ----

# 살아있는 빌드 세션 레지스트리 — (pid, slug) → PrototypeSession. 인메모리:
# 백엔드 재시작 시 소멸(빌드 디렉토리와 transcript는 남아 resume으로 이어진다).
proto_sessions: dict = {}

_proto_host_singleton = None


def _proto_root() -> Path:
    return Path(os.environ.get("PATHFINDER_PROTO_ROOT",
                               "~/pathfinder-protos")).expanduser()


def _proto_config_dir() -> Path:
    """빌드 에이전트 전용 CLAUDE_CONFIG_DIR. 지정하지 않으면 번들 바이너리가
    백엔드 유저의 ~/.claude(개인 skills/agents/CLAUDE.md)를 읽는다."""
    return Path(os.environ.get("PATHFINDER_PROTO_CONFIG_DIR",
                               "~/pathfinder-proto-config")).expanduser()


# 전역 동시 빌드 상한 (monkeypatchable in tests).
build_semaphore = None  # set below, after BuildSemaphore import


def proto_host():
    """ProtoHost 싱글턴 (monkeypatchable in tests)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from pathfinder.proto.host import ProtoHost
        _proto_host_singleton = ProtoHost(root=_proto_root())
    return _proto_host_singleton


def proto_session_factory(project_id: str, slug: str):
    """PrototypeSession 조립 (monkeypatchable in tests). VM은 없다 — 빌더가
    백엔드 프로세스 안에서 claude 서브프로세스를 띄운다."""
    from pathfinder.proto.builder import PrototypeBuilder
    from pathfinder.proto.session import PrototypeSession
    from pathfinder.proto.session_store import S3SessionStore

    s3 = s3_store_factory(project_id)
    build_root = _proto_root()
    config_dir = _proto_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    store = S3SessionStore(s3, slug=slug) if os.environ.get("PATHFINDER_S3_BUCKET") else None

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            config_dir=str(config_dir),
            session_id=session_id,
            resume=resume,
            session_store=store,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL"),
        )

    return PrototypeSession(
        project_id=project_id, slug=slug, s3=s3,
        build_root=build_root,
        builder_factory=builder_factory,
        semaphore=build_semaphore,
    )
```

임포트부에 추가하고 싱글턴을 초기화한다:

```python
from pathfinder.proto.limits import BuildSemaphore  # noqa: E402

build_semaphore = BuildSemaphore(
    max_concurrent=int(os.environ.get("PATHFINDER_PROTO_MAX_CONCURRENT", "2")))
```

lifespan에서 VM 스윕을 호스팅 스윕으로 교체한다:

```python
    # 재시작으로 소멸한 인메모리 세션이 남긴 고아 호스팅 프로세스 정리
    # (구 고아 VM 스윕의 대체물 — 이제 그 자식들은 우리 프로세스의 자식이다).
    try:
        swept = proto_host().sweep_orphans()
        if swept:
            _log.info("swept %d orphan prototype hosting process(es)", swept)
    except Exception:
        _log.exception("orphan hosting sweep failed; continuing startup")
```

`_cleanup_orphan_vms`와 `_proto_http_client`/`_proto_http` 전역, `import httpx`(다른 사용처가 없으면)를 삭제한다.

- [ ] **Step 4: prototypes.py 라우트 수정**

(a) `list_prototypes`가 용량을 함께 반환한다:

```python
@router.get("/projects/{pid}/prototypes")
async def list_prototypes(pid: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    s3 = app_module.s3_store_factory(pid)

    slugs: dict[str, str] = {}
    for key in await s3.list(_SPEC_PREFIX):
        m = _SPEC_RE.match(key)
        if m:
            slugs[m.group(1)] = key

    host = app_module.proto_host()
    out = []
    for slug, spec_path in sorted(slugs.items()):
        state = "none"
        port: int | None = None

        session = app_module.proto_sessions.get((pid, slug))
        host_info = host.status(pid, slug)
        bundle_exists = bool(await s3.list(f"prototypes/{slug}/bundle/"))

        if session is not None and session.status in _LIVE_STATUSES:
            state = "building"
        elif host_info is not None and host_info.state == "running":
            state = "running"
            port = host_info.port
        elif bundle_exists:
            state = "built"
        elif session is not None and session.status == "failed":
            state = "failed"

        out.append({"slug": slug, "spec_path": spec_path,
                    "state": state, "port": port})
    # Capacity travels with the list so a card can explain a 429 before the
    # user clicks (the cap is new -- MicroVM builds had no ceiling).
    return {"prototypes": out, **app_module.build_semaphore.snapshot()}
```

(b) `start_session`의 VM ARN 가드를 세마포어로 교체한다:

```python
@router.post("/projects/{pid}/prototypes/{slug}/session", status_code=202)
async def start_session(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    if _live_session(pid, slug) is not None:
        raise HTTPException(status_code=409, detail="build session already active")
    app_module.proto_sessions.pop((pid, slug), None)

    # In-process builds share one box: each session holds a claude subprocess
    # that may spawn a peak-2GB `next build`. Refuse rather than queue, and
    # name the situation -- a bare 429 reads as a bug to an attendee.
    if not app_module.build_semaphore.try_acquire():
        raise HTTPException(
            status_code=429,
            detail="다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요")

    session = app_module.proto_session_factory(pid, slug)
    try:
        await session.start()
    except FileNotFoundError:
        app_module.build_semaphore.release()
        raise HTTPException(status_code=404, detail="prototype spec not found")
    except Exception:
        # A failed start must not burn a slot permanently.
        app_module.build_semaphore.release()
        _log.exception("prototype session start failed: %s/%s", pid, slug)
        raise HTTPException(status_code=502, detail="session start failed")
    app_module.proto_sessions[(pid, slug)] = session
    return {"status": session.status}
```

(c) `start_host`가 라이브 세션을 거부하고 in-place로 띄운다:

```python
@router.post("/projects/{pid}/prototypes/{slug}/host")
async def start_host(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    # Hosting serves the build directory IN PLACE now, so starting it under a
    # live build session would race the agent writing into that same tree.
    if _live_session(pid, slug) is not None:
        raise HTTPException(
            status_code=409,
            detail="빌드 세션이 진행 중입니다 — 세션을 먼저 종료해 주세요")
    try:
        info = await app_module.proto_host().start(pid, slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype bundle not found")
    if info.state == "failed":
        raise HTTPException(status_code=502, detail=info.log_tail)
    return {"state": info.state, "port": info.port, "log_tail": info.log_tail}
```

`os` 임포트가 더 이상 쓰이지 않으면 제거한다.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -q`
Expected: all passed

- [ ] **Step 6: 전체 백엔드 스위트 확인**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: all passed (VM 테스트 삭제분만큼 총계 감소)

- [ ] **Step 7: 커밋**

```bash
git add backend/pathfinder/app.py backend/pathfinder/routes/prototypes.py backend/tests/test_routes_prototypes.py
git commit -m "feat(proto): gate session start on a build slot; drop the VM wiring

The 503 ARN-shape guard goes away with the VM env vars and is replaced by a
429 on the concurrency cap — the failure mode that actually exists now. The
slot is released on every start failure path, so two bad attempts can't wedge
the backend at cap 2.

list_prototypes returns {prototypes, active_builds, max_builds} so a card can
explain a refusal before the user clicks; the cap is new, since MicroVM builds
had no ceiling.

start_host now 409s under a live build session: hosting serves the build
directory in place, so it would otherwise race the agent writing that tree.

Startup sweeps orphan hosting processes instead of orphan VMs."
```

---

### Task 8: S3Store bytes 경로 + 아티팩트 zip 다운로드

**Files:**
- Modify: `backend/pathfinder/s3store.py`
- Modify: `backend/pathfinder/routes/prototypes.py`
- Test: `backend/tests/test_s3store_bytes.py` (create)
- Test: `backend/tests/test_routes_prototypes_archive.py` (create)

**Interfaces:**
- Consumes: Task 6의 빌드 디렉토리 규약
- Produces:
  - `S3Store.get_bytes(key) -> bytes`, `S3Store.put_bytes(key, content: bytes) -> None`
  - `GET /projects/{pid}/prototypes/{slug}/archive` → `application/zip`
  - Task 10(프론트)이 이 라우트를 링크한다.

- [ ] **Step 1: bytes 테스트 작성**

`backend/tests/test_s3store_bytes.py`:

```python
# backend/tests/test_s3store_bytes.py — binary-safe path for prototype bundles.
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from pathfinder.s3store import S3Store

PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd"


def _store(client):
    return S3Store(bucket="pf-test", prefix="projects/p1/", client=client)


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("s3", region_name="ap-northeast-2")
        c.create_bucket(
            Bucket="pf-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        yield c


async def test_put_bytes_get_bytes_round_trips_binary_unchanged(client):
    """The text API mangles this: .decode('utf-8', errors='replace') turns
    non-UTF-8 bytes into U+FFFD, which is why prototype images and fonts came
    back corrupt from the S3 bundle."""
    store = _store(client)
    await store.put_bytes("prototypes/x/bundle/logo.png", PNG_HEADER)
    assert await store.get_bytes("prototypes/x/bundle/logo.png") == PNG_HEADER


async def test_get_bytes_raises_file_not_found_like_get(client):
    with pytest.raises(FileNotFoundError):
        await _store(client).get_bytes("prototypes/x/bundle/missing.png")


async def test_text_api_still_works_alongside(client):
    store = _store(client)
    await store.put("aiplc-docs/a.md", "# 한글 문서")
    assert await store.get("aiplc-docs/a.md") == "# 한글 문서"


async def test_bytes_and_text_share_one_namespace(client):
    """put_bytes must land on the same key the text API would use, so listing
    and delete_prefix keep working across both."""
    store = _store(client)
    await store.put_bytes("prototypes/x/bundle/a.bin", b"\x00\x01")
    assert await store.list("prototypes/x/bundle/") == ["prototypes/x/bundle/a.bin"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_s3store_bytes.py -q`
Expected: FAIL — `AttributeError: 'S3Store' object has no attribute 'put_bytes'`

- [ ] **Step 3: s3store.py에 bytes 경로 추가**

`S3StoreLike` Protocol과 `S3Store`에 추가한다:

```python
class S3StoreLike(Protocol):
    async def get(self, key: str) -> str: ...
    async def put(self, key: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    # Binary-safe pair, used only by the prototype bundle backup/restore and
    # the handoff zip. The text methods above decode as UTF-8, which mangles
    # images and fonts (U+FFFD) -- fine for markdown, wrong for a bundle.
    async def get_bytes(self, key: str) -> bytes: ...
    async def put_bytes(self, key: str, content: bytes) -> None: ...
```

```python
    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from e
                raise
            return resp["Body"].read()

        return await asyncio.to_thread(_get)

    async def put_bytes(self, key: str, content: bytes) -> None:
        def _put() -> None:
            self._client.put_object(Bucket=self._bucket,
                                    Key=self._full_key(key), Body=content)

        await asyncio.to_thread(_put)
```

`backend/tests/fakes/in_memory_s3.py`에도 같은 쌍을 더한다. 텍스트/바이트가 한 네임스페이스를 공유해야 하므로 내부 저장을 bytes로 통일한다:

```python
class FakeS3Store:
    """In-memory S3StoreLike for runner/route unit tests (no boto3, no AWS).

    Stores bytes internally so the text and binary APIs share one namespace,
    exactly as S3Store does -- a text put must be visible to get_bytes and
    vice versa.
    """

    def __init__(self) -> None:
        self._raw: dict[str, bytes] = {}

    # `blobs` stays the text-facing view the existing tests were written
    # against: `s3.blobs[key] = "..."` and `assert s3.blobs[key] == "..."`.
    @property
    def blobs(self) -> "_TextView":
        return _TextView(self._raw)

    async def get(self, key: str) -> str:
        return (await self.get_bytes(key)).decode("utf-8")

    async def put(self, key: str, content: str) -> None:
        await self.put_bytes(key, content.encode("utf-8"))

    async def get_bytes(self, key: str) -> bytes:
        if key not in self._raw:
            raise FileNotFoundError(key)
        return self._raw[key]

    async def put_bytes(self, key: str, content: bytes) -> None:
        self._raw[key] = content

    async def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self._raw if k.startswith(prefix))

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._raw if k.startswith(prefix)]
        for k in doomed:
            del self._raw[k]
        return len(doomed)


class _TextView:
    """dict-like text view over the byte store, so existing tests that do
    `s3.blobs[key] = "text"` / `key in s3.blobs` keep working unchanged."""

    def __init__(self, raw: dict[str, bytes]):
        self._raw = raw

    def __setitem__(self, key: str, value: str) -> None:
        self._raw[key] = value.encode("utf-8")

    def __getitem__(self, key: str) -> str:
        return self._raw[key].decode("utf-8")

    def __contains__(self, key: object) -> bool:
        return key in self._raw

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def get(self, key: str, default=None):
        raw = self._raw.get(key)
        return default if raw is None else raw.decode("utf-8")

    def update(self, other: dict) -> None:
        for k, v in other.items():
            self[k] = v

    def keys(self):
        return self._raw.keys()
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_s3store_bytes.py tests/test_proto_session.py tests/test_routes_prototypes.py -q`
Expected: all passed

- [ ] **Step 5: zip 라우트 테스트 작성**

`backend/tests/test_routes_prototypes_archive.py`:

```python
# backend/tests/test_routes_prototypes_archive.py — handoff zip for the dev team.
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID = "archive-test"
SLUG = "demo"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    client.post("/projects", json={"project_id": PID})
    yield {"s3": s3, "root": tmp_path}
    app_module.registry.remove(PID)


def _names(resp) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        return sorted(zf.namelist())


def test_archive_zips_the_local_build_directory(env):
    build = env["root"] / PID / SLUG
    (build / "prototype").mkdir(parents=True)
    (build / "prototype" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (build / "prototype" / "README.md").write_text("# howto", encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert _names(resp) == ["prototype/README.md", "prototype/app.js"]


def test_archive_excludes_build_artifacts(env):
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "app.js").write_text("x", encoding="utf-8")
    for rel in ("node_modules/pkg/index.js", ".next/cache/x.bin", ".git/HEAD"):
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("junk", encoding="utf-8")
    (build.parent / ".proto-host.log").write_text("log", encoding="utf-8")
    (build.parent / ".proto-host.pid").write_text("123", encoding="utf-8")

    assert _names(client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")) == \
        ["prototype/app.js"]


def test_archive_excludes_survey_and_transcript_from_the_s3_fallback(env):
    """Survey responses are anonymous respondents' words and the transcript is
    build chatter -- neither belongs in a zip handed to the dev team, and both
    live under the same prototypes/{slug}/ prefix as the bundle."""
    s3 = env["s3"]
    s3.blobs[f"prototypes/{SLUG}/bundle/app.js"] = "console.log(1)"
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = '{"a":"secret"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/main/00000001.jsonl"] = '{"type":"user"}'

    names = _names(client.get(f"/projects/{PID}/prototypes/{SLUG}/archive"))

    assert names == ["app.js"]


def test_archive_preserves_binary_assets(env):
    png = b"\x89PNG\r\n\x1a\n\xff\xfe\xfd"
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "logo.png").write_bytes(png)

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.read("prototype/logo.png") == png


def test_archive_404_when_nothing_built(env):
    assert client.get(f"/projects/{PID}/prototypes/{SLUG}/archive").status_code == 404


def test_archive_content_disposition_survives_non_ascii_slug(env):
    build = env["root"] / PID / "한글-앱"
    build.mkdir(parents=True)
    (build / "app.js").write_text("x", encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/한글-앱/archive")

    assert resp.status_code == 200
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
```

- [ ] **Step 6: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes_archive.py -q`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 7: zip 라우트 구현**

`backend/pathfinder/routes/prototypes.py`의 hosting 섹션 앞에 추가한다:

```python
# ---- handoff archive ----

# Never shipped to the dev team: build artifacts (reproducible, huge), our own
# host bookkeeping, and -- from the S3 fallback -- the survey and transcript
# subtrees, which share the prototypes/{slug}/ prefix with the bundle but are
# anonymous respondents' words and build chatter respectively.
_ARCHIVE_EXCLUDED_DIRS = {"node_modules", ".next", ".git"}
_ARCHIVE_EXCLUDED_FILES = {".proto-host.log", ".proto-host.pid"}


def _archive_excluded(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if any(p in _ARCHIVE_EXCLUDED_DIRS for p in parts):
        return True
    return parts[-1] in _ARCHIVE_EXCLUDED_FILES if parts else True


def _archive_filename_header(slug: str) -> str:
    """RFC 6266/5987. A Korean slug raw-interpolated into a latin-1 header
    raises UnicodeEncodeError (500) -- same fix as artifacts.py."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "prototype"
    utf8 = quote(f"{slug}-prototype.zip", safe="")
    return (f'attachment; filename="{safe}-prototype.zip"; '
            f"filename*=UTF-8''{utf8}")


async def _archive_entries(pid: str, slug: str) -> list[tuple[str, bytes]]:
    """Prefer the local build directory -- it is the authoritative copy the
    agent wrote and hosting serves. The S3 bundle is the fallback for a box
    whose disk was wiped by a redeploy."""
    import pathfinder.app as app_module

    build_dir = app_module._proto_root() / pid / slug
    if build_dir.is_dir():
        entries = []
        for path in sorted(build_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(build_dir).as_posix()
            if _archive_excluded(rel):
                continue
            entries.append((rel, path.read_bytes()))
        if entries:
            return entries

    s3 = app_module.s3_store_factory(pid)
    bundle_prefix = f"prototypes/{slug}/bundle/"
    entries = []
    for key in await s3.list(bundle_prefix):
        rel = key[len(bundle_prefix):]
        if _archive_excluded(rel):
            continue
        entries.append((rel, await s3.get_bytes(key)))
    return entries


@router.get("/projects/{pid}/prototypes/{slug}/archive")
async def download_prototype_archive(pid: str, slug: str):
    """The dev-team handoff: prototype source as a zip. Binary-safe (bytes
    straight into the zip), so images and fonts survive."""
    _require_registered(pid)
    entries = await _archive_entries(pid, slug)
    if not entries:
        raise HTTPException(status_code=404, detail="prototype bundle not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries:
            zf.writestr(rel, content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _archive_filename_header(slug)},
    )
```

임포트부에 `io`, `zipfile`, `PurePosixPath`를 추가하고, `Response`가 이미 임포트돼 있는지 확인한다(`starlette.responses`에서 임포트 중).

- [ ] **Step 8: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes_archive.py -q`
Expected: 6 passed

- [ ] **Step 9: 커밋**

```bash
git add backend/pathfinder/s3store.py backend/pathfinder/routes/prototypes.py backend/tests/fakes/in_memory_s3.py backend/tests/test_s3store_bytes.py backend/tests/test_routes_prototypes_archive.py
git commit -m "feat(proto): binary-safe S3 pair + prototype handoff zip

S3Store gains get_bytes/put_bytes. The text methods decode as UTF-8, which
turns prototype images and fonts into U+FFFD — fine for markdown, wrong for a
bundle. Only the bundle backup and this zip use the new pair; Discovery paths
are untouched. FakeS3Store now stores bytes internally with a text view, so
both APIs share one namespace exactly as the real store does.

The zip prefers the local build directory (what the agent wrote and hosting
serves) and falls back to the S3 bundle for a redeployed box. It excludes
build artifacts, our host bookkeeping, and — the reason this needed care —
the survey and transcript subtrees, which sit under the same
prototypes/{slug}/ prefix but are anonymous respondents' words and build
chatter, not something to hand a dev team."
```

---

### Task 9: 업로드 키 개편 — uuid8 + 조건부 쓰기

**Files:**
- Modify: `backend/pathfinder/parsers/uploads.py`
- Modify: `backend/pathfinder/routes/uploads.py`
- Modify: `backend/pathfinder/s3store.py`
- Modify: `backend/tests/test_uploads_parser.py`
- Modify: `backend/tests/test_routes_uploads.py`

**Interfaces:**
- Consumes: 없음 (독립)
- Produces:
  - `upload_key(filename: str) -> str` in `parsers/uploads.py` — `uploads/{uuid8}/{원본명}.{원본확장자}.md`
  - `S3Store.put_if_absent(key, content) -> bool` — `IfNoneMatch="*"`, 이미 있으면 False
  - Task 10(프론트 표시)이 키 형태에 의존

- [ ] **Step 1: 파서 테스트 작성**

`backend/tests/test_uploads_parser.py`에서 `safe_name` 테스트를 `upload_key`로 교체하고 추가한다:

```python
import re

from pathfinder.parsers.uploads import upload_key

_KEY_RE = re.compile(r"^uploads/[0-9a-f]{8}/(.+)$")


def test_upload_key_preserves_the_original_name_and_extension():
    key = upload_key("요구사항.pdf")
    m = _KEY_RE.match(key)
    assert m, key
    assert m.group(1) == "요구사항.pdf.md"


def test_same_name_different_extension_stays_distinguishable():
    """The old safe_name() forced every upload to .md, so 요구사항.pdf and
    요구사항.xlsx collided into 요구사항.md / 요구사항-2.md with no way to tell
    which was which."""
    pdf = upload_key("요구사항.pdf")
    xlsx = upload_key("요구사항.xlsx")
    assert pdf.endswith("요구사항.pdf.md")
    assert xlsx.endswith("요구사항.xlsx.md")


def test_identical_uploads_get_distinct_keys():
    assert upload_key("a.md") != upload_key("a.md")


def test_upload_key_strips_path_and_control_characters():
    key = upload_key("../../etc/pa sswd.txt")
    assert ".." not in key
    m = _KEY_RE.match(key)
    assert m and "/" not in m.group(1)


def test_upload_key_handles_a_nameless_file():
    m = _KEY_RE.match(upload_key(""))
    assert m and m.group(1) == "upload.md"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_uploads_parser.py -q`
Expected: FAIL — `ImportError: cannot import name 'upload_key'`

- [ ] **Step 3: 파서 구현**

`backend/pathfinder/parsers/uploads.py`에서 `safe_name`을 `upload_key`로 교체한다:

```python
def upload_key(filename: str) -> str:
    """`uploads/{uuid8}/{원본명}.{원본확장자}.md`.

    The uuid directory is what makes this safe: it is unique per upload, so
    there is no read-then-write window to lose a race in, and no collision
    check to get subtly wrong. (The previous scheme listed existing keys, then
    computed a `-2` suffix, then wrote -- two concurrent uploads of the same
    name both saw "free" and the later write silently deleted the earlier
    file.)

    The original name AND extension are preserved, because the stored content
    is a CONVERSION of them: `요구사항.pdf` and `요구사항.xlsx` used to both
    become `요구사항.md`, leaving no way to tell which was which. The trailing
    `.md` stays -- the body really is markdown, and the frontend, the agent
    and the rules all expect `.md`.
    """
    name = PurePosixPath(filename or "").name          # drop any path parts
    stem = re.sub(r"[^\w가-힣.-]+", "-", name).strip("-.") or "upload"
    return f"uploads/{uuid.uuid4().hex[:8]}/{stem}.md"
```

임포트에 `import re`, `import uuid`, `from pathlib import PurePosixPath`를 추가한다(`re`는 이미 있다).

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_uploads_parser.py -q`
Expected: all passed

- [ ] **Step 5: 라우트 테스트 수정**

`backend/tests/test_routes_uploads.py`에서 경로 단언을 정규식으로 바꾸고 레이스 테스트를 추가한다:

```python
import re

_KEY_RE = re.compile(r"^uploads/[0-9a-f]{8}/(.+)$")


def test_upload_md_saved_under_a_uuid_directory(monkeypatch):
    _local_project(monkeypatch, "u1")
    r = client.post("/projects/u1/uploads",
                    files={"file": ("의견.md", io.BytesIO("# 의견".encode()), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    m = _KEY_RE.match(body["path"])
    assert m and m.group(1) == "의견.md.md"
    assert body["truncated"] is False
    ws = app_module.registry.get("u1")
    assert asyncio.get_event_loop().run_until_complete(
        ws.runner.read_file(body["path"])) == "# 의견"


def test_same_name_uploads_do_not_overwrite(monkeypatch):
    """The regression: the old list-then-write path let two uploads of one
    name land on the same key, and the later write silently deleted the
    earlier file."""
    _local_project(monkeypatch, "u2")
    paths = []
    for _ in range(2):
        r = client.post("/projects/u2/uploads",
                        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
        paths.append(r.json()["path"])
    assert paths[0] != paths[1]

    ws = app_module.registry.get("u2")
    loop = asyncio.get_event_loop()
    for p in paths:
        assert loop.run_until_complete(ws.runner.read_file(p)) == "x"
```

기존 `test_upload_collision_gets_suffix`를 위 두 번째 테스트로 대체한다.

- [ ] **Step 6: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_uploads.py -q`
Expected: FAIL — 경로가 `uploads/의견.md` 형태

- [ ] **Step 7: 라우트 수정**

`backend/pathfinder/routes/uploads.py`:

```python
# backend/pathfinder/routes/uploads.py
from fastapi import APIRouter, HTTPException, Request, UploadFile
from pathfinder.routes.deps import ensure_workspace
from pathfinder.parsers.uploads import convert, upload_key, MAX_UPLOAD_BYTES

router = APIRouter()

@router.post("/projects/{pid}/uploads")
async def upload_file(pid: str, file: UploadFile, request: Request):
    ws = await ensure_workspace(pid)
    # Cheap pre-check: reject oversized uploads before reading the body.
    # Content-Length is client-controlled (not a security boundary — the
    # post-read check below remains authoritative) but stops honest large
    # uploads from spooling to disk first.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES + 10_000:  # multipart overhead margin
        raise HTTPException(status_code=413, detail="file exceeds 5MB limit")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 5MB limit")
    try:
        content, truncated = convert(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    # No list-then-name step: the key carries a fresh uuid, so there is no
    # window for two concurrent uploads to agree on one key.
    path = upload_key(file.filename or "upload")
    await ws.runner.write_file(path, content)
    return {"path": path, "chars": len(content), "truncated": truncated}
```

- [ ] **Step 8: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_uploads.py -q`
Expected: all passed

- [ ] **Step 9: 조건부 쓰기 방어 추가**

`backend/tests/test_s3store_bytes.py` 끝에 추가:

```python
async def test_put_if_absent_refuses_to_overwrite(client):
    """Defence in depth behind the uuid key: even an impossible collision must
    fail loudly rather than silently replace someone's upload."""
    store = _store(client)
    assert await store.put_if_absent("uploads/abc12345/a.md", "first") is True
    assert await store.put_if_absent("uploads/abc12345/a.md", "second") is False
    assert await store.get("uploads/abc12345/a.md") == "first"
```

`backend/pathfinder/s3store.py`에 추가:

```python
    async def put_if_absent(self, key: str, content: str) -> bool:
        """Conditional write (S3 IfNoneMatch). Returns False if the key
        already exists instead of replacing it. Used by the upload path as a
        backstop behind its uuid keys -- a silent overwrite there costs a
        user's file."""
        def _put() -> bool:
            try:
                self._client.put_object(
                    Bucket=self._bucket, Key=self._full_key(key),
                    Body=content.encode("utf-8"), IfNoneMatch="*")
            except ClientError as e:
                if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
                    return False
                raise
            return True

        return await asyncio.to_thread(_put)
```

`S3StoreLike`와 `FakeS3Store`에도 같은 메서드를 더한다:

```python
    async def put_if_absent(self, key: str, content: str) -> bool:
        if key in self._raw:
            return False
        await self.put(key, content)
        return True
```

`AgentRunner.write_file`은 그대로 두고(에이전트 산출물은 덮어쓰기가 정상), 업로드 라우트만 이 경로를 쓰도록 `runner.write_file` 호출을 바꾼다:

`backend/pathfinder/runner.py`에 추가:

```python
    async def write_file_if_absent(self, rel_path: str, content: str) -> bool:
        """Upload path only: never silently replace an existing key."""
        reject_unsafe(rel_path)
        return await self._s3.put_if_absent(rel_path, content)
```

라우트에서:

```python
    path = upload_key(file.filename or "upload")
    if not await ws.runner.write_file_if_absent(path, content):
        # Impossible in practice (fresh uuid per upload) -- surfaced as a
        # retryable conflict rather than a silent overwrite.
        raise HTTPException(status_code=409, detail="upload key already exists")
```

`backend/tests/fakes/fake_runner.py`에도 `write_file_if_absent`를 더한다.

- [ ] **Step 10: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_uploads.py tests/test_s3store_bytes.py tests/test_uploads_parser.py -q`
Expected: all passed

- [ ] **Step 11: 전체 스위트 + 커밋**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: all passed

```bash
git add backend/pathfinder/parsers/uploads.py backend/pathfinder/routes/uploads.py backend/pathfinder/s3store.py backend/pathfinder/runner.py backend/tests/fakes backend/tests/test_uploads_parser.py backend/tests/test_routes_uploads.py backend/tests/test_s3store_bytes.py
git commit -m "fix(uploads): uuid-keyed uploads; stop silently overwriting

Two real defects with concurrent users. The key was derived by listing
existing uploads, computing a -2 suffix, then writing — two uploads of one
name both saw 'free' and the later write silently deleted the earlier file.
And safe_name forced every upload to .md, so 요구사항.pdf and 요구사항.xlsx
collided with no way to tell which was which.

Keys are now uploads/{uuid8}/{original name}.{original ext}.md. The uuid
directory removes the read-then-write window entirely, so no lock is needed —
there is no shared state left to serialise. Original name and extension are
preserved because the stored body is a conversion of them.

put_if_absent (S3 IfNoneMatch) backs it up: even an impossible uuid collision
fails loudly instead of replacing a user's file. Old keys stay readable; no
migration script."
```

---

### Task 10: 프론트엔드 — 다운로드 버튼 + 용량 표시

**Files:**
- Modify: `frontend/lib/api/prototypes.ts`
- Modify: `frontend/components/prototypes/PrototypeCard.tsx`
- Modify: `frontend/app/projects/[projectId]/prototypes/page.tsx`
- Test: `frontend/components/prototypes/PrototypeCard.test.tsx`
- Test: `frontend/lib/api/prototypes.test.ts`

**Interfaces:**
- Consumes: Task 7의 `{prototypes, active_builds, max_builds}`, Task 8의 archive 라우트
- Produces: `prototypeArchiveUrl(pid, slug): string`, `listPrototypes` 반환형 변경

- [ ] **Step 1: API 클라이언트 테스트 추가 (실패)**

`frontend/lib/api/prototypes.test.ts`에 추가:

```ts
import { prototypeArchiveUrl } from "./prototypes";

it("builds an absolute archive URL with encoded segments", () => {
  const url = prototypeArchiveUrl("proj 1", "한글-앱");
  expect(url).toContain("/projects/proj%201/prototypes/");
  expect(url).toContain(encodeURIComponent("한글-앱"));
  expect(url).toMatch(/\/archive$/);
});
```

`listPrototypes`가 새 응답 형태를 다루는 테스트도 추가한다:

```ts
it("unwraps the prototypes array and reports build capacity", async () => {
  server.use(
    http.get("*/projects/p1/prototypes", () =>
      HttpResponse.json({
        prototypes: [{ slug: "demo", spec_path: "s.md", state: "built", port: null }],
        active_builds: 1,
        max_builds: 2,
      }),
    ),
  );
  const result = await listPrototypes("p1");
  expect(result.prototypes.map((p) => p.slug)).toEqual(["demo"]);
  expect(result.active_builds).toBe(1);
  expect(result.max_builds).toBe(2);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run lib/api/prototypes.test.ts`
Expected: FAIL — `prototypeArchiveUrl` 없음

- [ ] **Step 3: 클라이언트 구현**

`frontend/lib/api/prototypes.ts`:

```ts
export interface PrototypeListing {
  prototypes: PrototypeInfo[];
  /** Concurrent builds in flight backend-wide, and the cap. New with
   *  in-process builds: MicroVM builds had no ceiling, so a card needs to be
   *  able to explain a 429 before the user clicks. */
  active_builds: number;
  max_builds: number;
}

export async function listPrototypes(pid: string): Promise<PrototypeListing> {
  return request<PrototypeListing>(`/projects/${encodeURIComponent(pid)}/prototypes`);
}

/** Plain URL, not a Blob fetch: the browser handles Content-Disposition and
 *  the filename, matching how surveyCsvUrl is consumed via <a href>. */
export function prototypeArchiveUrl(pid: string, slug: string): string {
  return `${API_BASE_URL}${sessionPath(pid, slug, "/archive")}`;
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run lib/api/prototypes.test.ts`
Expected: PASS

- [ ] **Step 5: 카드 테스트 추가 (실패)**

`frontend/components/prototypes/PrototypeCard.test.tsx`에 추가:

```tsx
it("offers a download link once a bundle exists", () => {
  render(
    <PrototypeCard
      info={{ slug: "demo", spec_path: "s.md", state: "built", port: null }}
      onBuild={() => {}}
      onStartHost={() => {}}
      onStopHost={() => {}}
      archiveUrl="/api/projects/p1/prototypes/demo/archive"
      busy={false}
    />,
  );
  const link = screen.getByRole("link", { name: "다운로드" });
  expect(link).toHaveAttribute("href", "/api/projects/p1/prototypes/demo/archive");
});

it("offers download while running too", () => {
  render(
    <PrototypeCard
      info={{ slug: "demo", spec_path: "s.md", state: "running", port: 4001 }}
      onBuild={() => {}}
      onStartHost={() => {}}
      onStopHost={() => {}}
      archiveUrl="/api/x"
      busy={false}
    />,
  );
  expect(screen.getByRole("link", { name: "다운로드" })).toBeInTheDocument();
});

it("hides download when there is nothing built yet", () => {
  render(
    <PrototypeCard
      info={{ slug: "demo", spec_path: "s.md", state: "none", port: null }}
      onBuild={() => {}}
      onStartHost={() => {}}
      onStopHost={() => {}}
      archiveUrl="/api/x"
      busy={false}
    />,
  );
  expect(screen.queryByRole("link", { name: "다운로드" })).toBeNull();
});
```

- [ ] **Step 6: 카드 구현**

`PrototypeCard.tsx`의 props에 `archiveUrl?: string`을 더하고, `built`/`running` 분기 안에 링크를 넣는다. `설문` 버튼과 시각적으로 동일하도록 `SECONDARY_BTN`을 쓴다:

```tsx
  archiveUrl,
```
```tsx
  archiveUrl?: string;
```

`built` 분기:

```tsx
        {info.state === "built" && (
          <>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onStartHost}>
              호스팅 시작
            </button>
            <button type="button" className={SECONDARY_BTN} disabled={busy} onClick={onBuild}>
              다시 빌드
            </button>
            {archiveUrl && <ArchiveLink href={archiveUrl} />}
          </>
        )}
```

`running` 분기의 로그 버튼 뒤에 `{archiveUrl && <ArchiveLink href={archiveUrl} />}`를 더하고, 파일 하단에 헬퍼를 둔다:

```tsx
/** An <a>, not a button: the dev-team handoff is a plain file download, so
 *  the browser handles Content-Disposition and the filename (same shape as
 *  the survey CSV link). */
function ArchiveLink({ href }: { href: string }) {
  return (
    <a href={href} className={SECONDARY_BTN}>
      다운로드
    </a>
  );
}
```

- [ ] **Step 7: 통과 확인**

Run: `cd frontend && npx vitest run components/prototypes/PrototypeCard.test.tsx`
Expected: PASS

- [ ] **Step 8: 페이지 배선**

`frontend/app/projects/[projectId]/prototypes/page.tsx`에서 `listPrototypes` 결과를 새 형태로 받고, 카드에 `archiveUrl`을 넘긴다. 상한 표시도 더한다:

```tsx
import { prototypeArchiveUrl } from "@/lib/api/prototypes";
```

`listPrototypes(projectId)` 결과를 쓰는 자리에서 `.prototypes`를 꺼내고, 용량은 헤더 옆에 노출한다:

```tsx
              <PrototypeCard
                key={info.slug}
                info={info}
                archiveUrl={prototypeArchiveUrl(projectId, info.slug)}
                ...
              />
```

용량 안내(빌드가 상한에 도달했을 때만):

```tsx
        {listing && listing.active_builds >= listing.max_builds && (
          <p className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            동시 빌드 상한({listing.max_builds}건)에 도달했습니다 — 진행 중인 빌드가
            끝나면 새 빌드를 시작할 수 있습니다.
          </p>
        )}
```

- [ ] **Step 9: 프론트 전체 테스트**

Run: `cd frontend && npm test`
Expected: all passed. `listPrototypes` 반환형 변경으로 깨지는 기존 테스트/페이지 테스트가 있으면 `.prototypes`로 고친다.

- [ ] **Step 10: 커밋**

```bash
git add frontend/lib/api/prototypes.ts frontend/lib/api/prototypes.test.ts frontend/components/prototypes/PrototypeCard.tsx frontend/components/prototypes/PrototypeCard.test.tsx "frontend/app/projects/[projectId]/prototypes/page.tsx"
git commit -m "feat(frontend): prototype download link + build-capacity notice

Download is an <a href>, not a Blob fetch — the handoff is a plain file
download, so the browser handles Content-Disposition and the filename. Same
shape as the existing survey CSV link, and it follows the optional-prop
pattern 95a2876 established for card actions. It sits inside the
built/running branches since it needs a bundle, unlike 설문 which is
state-independent.

listPrototypes now returns {prototypes, active_builds, max_builds}; the page
warns when the cap is reached so a 429 isn't the first the user hears of it."
```

---

### Task 11: 인프라 — 인스턴스 상향, VM 스택 삭제

**Files:**
- Modify: `infra/lib/pathfinder-hosting-stack.ts`
- Modify: `infra/lib/backend-permissions.ts`
- Modify: `infra/lib/user-data.ts`
- Modify: `infra/bin/app.ts`
- Modify: `infra/test/hosting-stack.assert.ts`
- Delete: `infra/lib/pathfinder-vm-stack.ts`
- Delete: `infra/test/vm-stack.assert.ts`
- Delete: `infra/package-harness.sh`

**Interfaces:**
- Consumes: Task 7의 새 env 이름
- Produces: m7i.2xlarge / x86_64 / EBS 100GB 인스턴스 + `PATHFINDER_PROTO_MAX_CONCURRENT`·`PATHFINDER_PROTO_CONFIG_DIR` env

- [ ] **Step 1: assertion 수정 (실패하는 상태)**

`infra/test/hosting-stack.assert.ts:101-103` 부근을 바꾼다:

```typescript
  // x86_64 인스턴스 1대, IMDSv2 강제(HttpTokens required). Graviton은 쓰지
  // 않는다 — SDK 번들 바이너리가 x86-64 ELF이고, 프로토타입이 설치하는
  // 네이티브 npm 모듈도 x86_64 prebuilt를 받는다.
  template.hasResourceProperties('AWS::EC2::Instance', {
    InstanceType: 'm7i.2xlarge',
```

EBS 단언을 추가한다(같은 `hasResourceProperties` 블록 또는 인접 블록):

```typescript
  // 빌드가 이 박스로 들어오면서 프로토타입당 node_modules가 상주한다 — 20GB로는
  // 부족하다.
  template.hasResourceProperties('AWS::EC2::Instance', {
    BlockDeviceMappings: Match.arrayWith([
      Match.objectLike({ Ebs: Match.objectLike({ VolumeSize: 100 }) }),
    ]),
  });

  // lambda-microvms 제어 권한은 VM 계층과 함께 사라졌다.
  const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));
  if (policies.includes('lambda-microvms')) {
    throw new Error('hosting: instance role still carries lambda-microvms permissions');
  }
```

- [ ] **Step 2: 실패 확인**

Run: `cd infra && npx cdk synth PathfinderHostingStack > /dev/null && npx tsx test/hosting-stack.assert.ts`
Expected: FAIL — InstanceType이 `t4g.medium`

> 테스트 실행 명령이 다르면 `infra/package.json`의 `scripts`를 확인해 그것을 쓴다.

- [ ] **Step 3: 인스턴스 상향 + VM props 제거**

`infra/lib/pathfinder-hosting-stack.ts`:

```typescript
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      // 프로토타입 빌드가 이 박스로 들어왔다: 세션마다 claude 서브프로세스
      // (~300-500MB)가 상주하고 next build가 피크 2GB를 쓴다. Graviton은 쓰지
      // 않는다(SDK 번들 바이너리가 x86-64).
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.M7I, ec2.InstanceSize.XLARGE2),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      securityGroup: sg,
      role,
      userData,
      requireImdsv2: true,
      userDataCausesReplacement: true,
      blockDevices: [{
        deviceName: '/dev/xvda',
        // 프로토타입당 node_modules가 상주한다(실측 ~23MB/건이지만 여유를 둔다).
        volume: ec2.BlockDeviceVolume.ebs(100, { encrypted: true }),
      }],
    });
```

`HostingStackProps`에서 `vmImageId`/`vmRoleArn`/`vmRegion`을 지우고, `microvmControlStatements` 임포트와 그 for 루프(`:85-87`)를 지우고, `renderUserData` 호출에서 `vmRegion`/`vmImageId`/`vmRoleArn`을 지운다.

- [ ] **Step 4: backend-permissions / user-data / app.ts 정리**

- `infra/lib/backend-permissions.ts`: `microvmControlStatements` 함수 전체 삭제.
- `infra/lib/user-data.ts`: `vmRegion`/`vmImageId`/`vmRoleArn` 파라미터와 그 env 라인 삭제. 대신 두 줄을 추가한다:

```typescript
# 프로토타입 빌드: 동시 빌드 상한과 빌드 에이전트 전용 CLAUDE_CONFIG_DIR.
# 후자를 비우면 번들 Claude Code 바이너리가 이 유저의 ~/.claude(개인
# skills/agents/CLAUDE.md)를 읽어 워크숍 결과가 호스트 설정에 의존한다.
Environment=PATHFINDER_PROTO_MAX_CONCURRENT=2
Environment=PATHFINDER_PROTO_CONFIG_DIR=/home/ec2-user/pathfinder-proto-config
```

> 실제 값/경로는 `user-data.ts`의 기존 `Environment=` 라인들이 쓰는 유저·경로 규약에 맞춘다.

- `infra/bin/app.ts`: `PathfinderVmStack` 임포트와 인스턴스화 삭제, `PathfinderHostingStack` 호출에서 `vmImageId`/`vmRoleArn`/`vmRegion` 삭제, 그 위 크로스리전 주입 주석 삭제.

- [ ] **Step 5: VM 파일 삭제**

```bash
cd /home/ec2-user/project/pathfinder-sp
git rm infra/lib/pathfinder-vm-stack.ts infra/test/vm-stack.assert.ts infra/package-harness.sh
```

- [ ] **Step 6: 합성 + assertion 통과 확인**

Run: `cd infra && npx cdk synth && npx tsx test/hosting-stack.assert.ts`
Expected: synth 성공 + assertion 통과. `cdk.context.json`에 캐시된 프리픽스 리스트 조회가 있어 크리덴셜 없이도 synth가 돌아야 한다.

- [ ] **Step 7: harness 디렉토리 삭제**

```bash
cd /home/ec2-user/project/pathfinder-sp
git rm -r harness
```

Run: `grep -rn "harness" --include=*.py --include=*.ts --include=*.tsx --include=*.sh . | grep -v node_modules | grep -v docs/ | grep -v "\.git/"`
Expected: 히트 없음(또는 주석의 역사적 언급만 — 그건 남겨도 된다).

- [ ] **Step 8: 커밋**

```bash
git add -A infra
git commit -m "feat(infra): m7i.2xlarge on x86_64, EBS 100GB; delete the VM stack

The build agent now runs on this box, so t4g.medium's 2 vCPU / 4GB no longer
fits: each session holds a claude subprocess (~300-500MB) and next build peaks
near 2GB, alongside the frontend, backend and nginx already there. EBS goes to
100GB because prototype node_modules now live here.

x86_64 rather than Graviton (user decision), which also removes an
architecture risk: the SDK's bundled binary is an x86-64 ELF and prototype
native npm modules get x86_64 prebuilts.

Deletes PathfinderVmStack, the harness image asset script, the
lambda-microvms permissions and the cross-region context injection — deploy is
plain 'npx cdk deploy' again. Run 'npx cdk destroy PathfinderVmStack --region
ap-northeast-1' once to clean up the deployed Tokyo stack."
```

---

### Task 12: README · .env.example · e2e 체크리스트

**Files:**
- Modify: `README.md`
- Modify: `backend/.env.example`
- Modify: `infra/README.md`
- Modify: `docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md`

**Interfaces:**
- Consumes: Task 1–11 전부
- Produces: 배포·검증 문서

- [ ] **Step 1: README 갱신**

`README.md`에서:
- 상단 설명의 "Tokyo MicroVM 안의 Claude Agent SDK" → 백엔드 in-process + 맥락 resume으로 고친다.
- 구조 설명의 `infra/` 줄에서 MicroVM 언급을 지운다.
- env 표에서 `PATHFINDER_VM_REGION`/`PATHFINDER_VM_IMAGE_ID`/`PATHFINDER_VM_ROLE_ARN` 세 행을 지우고 두 행을 추가한다:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PATHFINDER_PROTO_MAX_CONCURRENT` | `2` | 동시 프로토타입 빌드 상한(전역). 초과 시 세션 시작이 429 |
| `PATHFINDER_PROTO_CONFIG_DIR` | `~/pathfinder-proto-config` | 빌드 에이전트 전용 `CLAUDE_CONFIG_DIR`. 미지정 시 호스트 유저의 `~/.claude`(개인 skills/agents)가 빌드에 섞인다 |

- "참고" 절의 리전 설명에서 "프로토타입 생성 기능의 MicroVM만 예외 — 도쿄" 문단을 삭제하고, 이제 전 리소스가 서울로 통일된다고 적는다.

- [ ] **Step 2: .env.example 갱신**

`backend/.env.example`에서 VM 세 줄을 지우고 위 두 변수를 주석과 함께 추가한다.

- [ ] **Step 3: infra/README 갱신**

"PathfinderVmStack 배포 절차" 절을 삭제하고, 그 자리에 한 문단을 남긴다:

```markdown
> **PathfinderVmStack은 제거됐다** (2026-07-25). 프로토타입 빌드는 백엔드
> 프로세스 안에서 돌고, 도쿄 MicroVM·이미지 빌드·토큰 민팅이 모두 사라졌다.
> 이전에 배포한 적이 있다면 한 번 정리한다:
> `npx cdk destroy PathfinderVmStack --region ap-northeast-1`
```

`PathfinderHostingStack` 설명의 `AL2023 arm64` → `AL2023 x86_64 (m7i.2xlarge)`로 고친다.

- [ ] **Step 4: e2e 체크리스트 갱신**

`docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md`에서 VM 절차(이미지 빌드, ARN 주입, VM 상태 확인)를 삭제하고 아래 항목을 추가한다:

```markdown
## 흡수 후 신규 확인 항목 (2026-07-25)

- [ ] **번들 바이너리 기동** — 배포된 EC2에서
      `backend/.venv/bin/python -c "import claude_agent_sdk, pathlib, subprocess;
      p=pathlib.Path(claude_agent_sdk.__file__).parent/'_bundled'/'claude';
      print(subprocess.run([str(p),'--version'],capture_output=True,text=True).stdout)"`
      → `2.x.x (Claude Code)` 출력
- [ ] **config 격리** — 빌드 턴 중 `ps -eo args | grep claude`로 뜬 프로세스의
      `CLAUDE_CONFIG_DIR`가 `PATHFINDER_PROTO_CONFIG_DIR`를 가리킨다. 그리고
      호스트 유저의 `~/.claude/skills/`에 아무 스킬을 하나 두고 빌드를 돌려도
      에이전트가 그것을 인식하지 못한다(격리 확인).
- [ ] **프로세스 RSS 실측** — 빌드 1건 진행 중
      `ps -eo rss,args | grep -c "[c]laude"`와 RSS 합계를 기록한다.
      스펙 §4의 예산(claude 1건당 300–577MB)과 비교한다.
- [ ] **동시 2건 피크** — 서로 다른 프로토타입 2개를 동시에 빌드하고
      `free -m`으로 피크 사용량을 기록한다. 3번째 세션 시작이 429 +
      한국어 안내를 반환한다.
- [ ] **맥락 재개** — 프로토타입을 빌드하고 세션을 종료한 뒤 백엔드를
      재시작한다. 세션을 다시 시작해 "방금 만든 화면에서 버튼 색만 바꿔줘"라고
      요청했을 때 에이전트가 스펙을 다시 읽지 않고 이전 구현을 참조한다.
- [ ] **in-place 호스팅** — 빌드 완료 후 호스팅을 시작하면 `npm install`이
      다시 돌지 않는다(로그 확인). 빌드 세션이 살아있는 동안 호스팅 시작은 409.
- [ ] **바이너리 에셋** — 이미지를 포함한 프로토타입을 빌드해 프리뷰에서
      이미지가 정상 렌더된다. `.../archive` zip을 내려 이미지 바이트가 온전하다.
- [ ] **아티팩트 zip** — 다운로드한 zip에 README·package.json이 있고,
      `node_modules`/`.next`/`survey`/`transcript`가 없다.
- [ ] **고아 프로세스 정리** — 호스팅 중 `kill -9 <uvicorn pid>` 후 백엔드를
      재기동하면 로그에 `swept N orphan prototype hosting process(es)`가 남고
      해당 포트가 해제된다.
- [ ] **업로드 키** — 같은 이름의 파일을 두 번 올리면 서로 다른
      `uploads/{uuid8}/...` 경로가 반환되고 두 파일 모두 읽힌다.
```

- [ ] **Step 5: 전체 검증**

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npm test
cd ../infra && npx cdk synth > /dev/null && echo "synth ok"
```
Expected: 셋 다 통과

- [ ] **Step 6: 커밋**

```bash
git add README.md backend/.env.example infra/README.md docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md
git commit -m "docs: update README/env/e2e for the in-process builder

Drops the three PATHFINDER_VM_* variables and the 'MicroVM is the one Tokyo
exception' region caveat — every resource is Seoul now. Adds
PATHFINDER_PROTO_MAX_CONCURRENT and PATHFINDER_PROTO_CONFIG_DIR, the latter
with the reason it matters: leave it unset and the bundled binary reads the
host user's personal ~/.claude.

The e2e checklist gains the items unit tests cannot cover on a dev box:
bundled-binary startup on the deployed instance, config isolation (plant a
skill in the operator's ~/.claude and confirm the agent can't see it),
measured RSS against the spec's budget, the concurrent-2 peak plus a 429 on
the third, and the context-resume scenario that is the whole point of this
change."
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | Task |
|---|---|
| §2 아키텍처 / 삭제 목록 | 3, 5, 7, 11 |
| §3 맥락 지속성 (session_store + resume) | 4, 5 |
| §4 자원·동시성 (상한, arm64→x86, 유휴 타이머) | 2, 5, 7, 11 |
| §5 ProtoHost in-place (rmtree·포트·고아) | 6, 7 |
| §6 S3 바이너리 안전 경로 | 8 |
| §7 아티팩트 zip | 8, 10 |
| §8 Claude Code 설정 격리 | 3 (`CLAUDE_CONFIG_DIR`), 7 (배선), 11 (env), 12 (검증) |
| §9 업로드 경로 개편 | 9 |
| §10 인프라 | 11 |
| §11 에러 처리 | 각 Task의 테스트에 분산 (429/409/404/fail-soft resume/세마포어 반납) |
| §12 테스트 | 각 Task + 12 (e2e 체크리스트) |
| §13 survey 영향 | 8 (zip 제외 단언), 7 (app.py survey 블록 보존) |

갭 없음. 단 §11의 "resume 실패 → 새 세션 fail-soft"는 Task 5의 `test_start_regenerates_when_the_saved_id_is_not_a_uuid`가 저장된 id 쪽만 덮는다 — SDK의 `load()` 실패는 SDK 내부에서 처리되고 우리 코드 경로가 아니므로 유닛 테스트 대상이 아니고, e2e 체크리스트의 맥락 재개 항목이 실동작을 확인한다.

**2. 플레이스홀더 스캔**

"TBD"/"적절히 처리"/"Task N과 유사" 없음. 코드 스텝은 전부 실제 코드를 담았다. 두 곳에 "원본을 그대로 옮긴다"는 지시가 있다(Task 3의 sdk_driver 메서드들, Task 6의 npm 라이프사이클) — 이는 플레이스홀더가 아니라 **이식 지시**이며, 원본 파일 경로와 변경할 임포트를 정확히 명시했다. Task 3의 이식 대상 메서드는 이름을 하나하나 열거했다.

**3. 타입 일관성**

- `PrototypeBuilder` 생성자 인자(`workspace`/`config_dir`/`session_id`/`resume`/`session_store`/`anthropic_model`/`client_factory`)가 Task 3 정의, Task 5 `FakeBuilder`, Task 7 `builder_factory` 전부에서 일치.
- `builder_factory(session_id: str, resume: bool)` 시그니처가 Task 5 테스트 헬퍼와 Task 7 배선에서 일치.
- `BuildSemaphore`의 `try_acquire`/`release`/`snapshot`이 Task 2 정의와 Task 5·7 사용처에서 일치.
- `S3SessionStore(s3, slug=...)` 키워드가 Task 4와 Task 7에서 일치.
- `ProtoHost(root=..., port_range=...)` — `s3` 없음이 Task 6과 Task 7에서 일치.
- `S3Store.get_bytes`/`put_bytes`/`put_if_absent`가 Task 8·9의 정의·`S3StoreLike`·`FakeS3Store` 전부에 존재.
- `listPrototypes` 반환 `PrototypeListing`이 Task 7 백엔드 응답 형태와 Task 10 프론트 타입에서 일치.
