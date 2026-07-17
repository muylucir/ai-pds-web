# Pathfinder MicroVM Sandbox — Compute Relay (Part 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the real `MicroVMSandbox` compute path — boot an AWS Lambda MicroVM running Claude Code headless with `aiplc-rules` injected, relay a serialized agent turn back over SSE, and forward workspace file ops — behind the existing `Sandbox` ABC so it drops into `make_sandbox` with zero route/parser changes, verified against a shared contract test both sandboxes pass.

**Architecture:** Three injectable seams keep the whole thing unit-testable without AWS: (1) a **`HarnessClient`** — the HTTP client for the MicroVM's harness endpoint (`POST /message` → SSE, `GET/PUT /files/*`, `GET /health`), tested against a fake ASGI harness; (2) a **`MicroVMController`** ABC abstracting boot/resume/suspend/stop of the Lambda MicroVM, with a `FakeMicroVMController` for tests and a `LambdaMicroVMController` whose real AWS calls are pinned in the integration task; (3) **`MicroVMSandbox`**, which implements the `Sandbox` ABC exactly, lazily boots on first use, serializes turns to one at a time, upholds the same path-safety guarantee as `LocalSandbox`, and relays events unchanged. A shared `sandbox_contract` test module runs the same assertions against both `LocalSandbox` and `MicroVMSandbox`.

**Tech Stack:** Python 3.11 (`str | None`), FastAPI/Starlette, Pydantic v2, `httpx` (already a dependency; used for the harness client and, via `ASGITransport`, the fake harness in tests), `sse-starlette` (already present), pytest + pytest-asyncio (auto mode). **No new dependencies in this plan.** boto3/moto arrive in Part 2.

## Scope — this is Part 1 of a 2-part split

The full "real sandbox" work (MicroVMSandbox + harness + S3 persistence + recovery) is too large for one plan. It is split at the **"does it touch durable S3 storage?"** boundary. Each part produces working, testable software on its own.

- **Part 1 (this plan) — Compute relay.** A `MicroVMSandbox` that boots a MicroVM, injects rules, relays a real Claude Code turn, forwards file ops to the live harness, serializes turns, and swaps into `make_sandbox` behind an env flag. All file ops in Part 1 lazily boot the VM and use the harness. Verified against the `Sandbox` contract with injected fakes (unit) plus one scripted real-AWS boot/turn drill (integration).
- **Part 2 (follow-on) — Durable persistence + recovery.** File name: `docs/superpowers/plans/2026-07-18-pathfinder-microvm-persistence-recovery.md`. Adds: an `S3Store` (injectable client / moto-backed tests, dependency justified there); reroutes **not-booted** file ops to S3 so a project can be read/written with NO live MicroVM (true laziness); syncs `aiplc-docs/` + prototype source to S3 **after every turn**; recovery on MicroVM expiry (max 8h)/failure by booting fresh + restoring the workspace from S3 and letting the methodology's session-continuity rule resume itself (no custom resume logic); and the suspend/resume reconcile for writes that landed in S3 while suspended. Integration drills: real S3 round-trip and a recovery drill. The `S3` durable store lives in **Seoul**; MicroVMs run in **Tokyo (ap-northeast-1)** — Part 2 carries the cross-region data-governance disclosure note.

**Operational caveat carried into Part 1:** because Part 1 has no S3 sync, a MicroVM expiry or crash loses in-flight workspace state. Part 1 is a verifiable engineering milestone (passes the contract, relays real turns), **not** a production-safe deployment. Do not run real customer workshops on Part 1 alone; ship Part 2 first.

## Global Constraints

Binding project-wide rules. Every task implicitly includes these.

- **The `Sandbox` ABC (`backend/pathfinder/sandbox/base.py`) is the fixed boundary.** `MicroVMSandbox` implements it exactly — `async start()`, `async read_file(rel_path: str) -> str`, `async write_file(rel_path: str, content: str) -> None`, `async list_files(glob: str) -> list[str]`, `send_message(text: str) -> AsyncIterator[AgentEvent]` (an async-generator function, matching `LocalSandbox`), `async stop()`. Routes and parsers do NOT change.
- **No path may escape the workspace root.** Reject any `rel_path`/glob that starts with `/` or contains a `..` segment, for **any** path `MicroVMSandbox` forwards to the harness — the same guarantee `LocalSandbox._resolve` gives (`backend/pathfinder/sandbox/local.py:17-20`). Rejection raises `ValueError`.
- **Never log, persist, or echo credential-shaped strings.** The `POST /message` and `GET /events` routes already redact agent output via `redact_credentials` at the surface seam (`backend/pathfinder/routes/turns.py`); `MicroVMSandbox` must not defeat that (it relays `AgentEvent` objects unchanged and adds no new logging of `event.text`). Auth uses the MicroVM IAM execution role (`CLAUDE_CODE_USE_BEDROCK`) — **no long-lived API keys** exist anywhere in the boot env.
- **Claude Code model is pinned to Sonnet 5** via `ANTHROPIC_MODEL` = the Bedrock cross-region inference profile id. **Do NOT hardcode a guessed id** — the exact id is resolved at impl time via `aws bedrock list-inference-profiles` (Task 6). `BootSpec.anthropic_model` stays `str | None` and empty until verified.
- **MicroVMs run in `ap-northeast-1` (Tokyo).** Seoul is unsupported as of 2026-07. Durable storage (S3) is Seoul — Part 2. The cross-region data-governance disclosure is documented in Part 2 and referenced in Task 6.
- **No methodology logic in the backend.** `MicroVMSandbox` boots Claude Code + injects `aiplc-rules` and relays turns; it contains no stage lists, no question wording, and (critically) **no session-continuity/resume logic** — resuming from `aiplc-state.md` is the rule's job, handled in Part 2 by simply booting + restoring and letting the agent read the file itself.
- **Python 3.11**, `str | None` unions, `from __future__ import annotations` at the top of every module (matches the existing codebase).
- **Concurrency:** single Claude Code session per project; turns are serialized (one at a time). A turn in progress makes a concurrent `send_message` yield a clear soft "busy" signal. This is a soft hint + server-side serialization, **not** a hard multi-session queue.

---

## File Structure

```
backend/
  pathfinder/
    sandbox/
      pathsafe.py            # NEW: reject_unsafe(path) — shared path-safety guard (raises ValueError)
      harness.py             # NEW: HarnessClient — HTTP client for the MicroVM harness protocol
      microvm_control.py     # NEW: BootSpec, VMHandle, MicroVMController ABC, FakeMicroVMController
      microvm_control_aws.py # NEW: LambdaMicroVMController (AWS binding; body pinned in Task 6 integration)
      microvm.py             # NEW: MicroVMSandbox — implements the Sandbox ABC (lazy boot, turn relay, serialization)
      base.py                # unchanged (the ABC)
      local.py               # unchanged (reference impl + its path guard)
    app.py                   # MODIFY: make_sandbox becomes env-gated (LocalSandbox default; MicroVMSandbox when PATHFINDER_SANDBOX=microvm)
  tests/
    sandbox_contract.py      # NEW: shared contract assertions run against BOTH sandboxes (not a test file itself)
    fakes/
      __init__.py            # NEW
      harness_app.py         # NEW: build_fake_harness_app() — Starlette ASGI app emulating the harness (for HarnessClient tests)
      in_memory_harness.py   # NEW: FakeHarness — in-memory object with the HarnessClient method surface (for MicroVMSandbox tests)
    test_pathsafe.py         # NEW
    test_harness_client.py   # NEW
    test_microvm_control.py  # NEW
    test_sandbox_contract.py # NEW: runs sandbox_contract against LocalSandbox AND MicroVMSandbox(fakes)
    test_microvm_sandbox.py  # NEW: lazy-boot, serialization, path-safety, relay specifics
    test_make_sandbox.py     # NEW: env-gated factory swap; routes unchanged
```

Rationale: the three seams (`harness.py`, `microvm_control*.py`, `microvm.py`) are separated because they change for independent reasons (protocol shape vs. AWS control-plane vs. sandbox lifecycle) and each has a distinct fake. `pathsafe.py` centralizes the escape guard so `MicroVMSandbox` provably shares `LocalSandbox`'s guarantee (the contract test enforces parity). `sandbox_contract.py` is the "real, valuable" shared module the design calls for.

---

### Task 1: Shared path-safety guard

**Files:**
- Create: `backend/pathfinder/sandbox/pathsafe.py`
- Test: `backend/tests/test_pathsafe.py`

**Interfaces:**
- Produces: `reject_unsafe(path: str) -> None` — raises `ValueError(f"unsafe path: {path}")` if `path` starts with `/` or contains a `..` segment; returns `None` otherwise. This is the exact predicate `LocalSandbox._resolve`/`list_files` apply (`backend/pathfinder/sandbox/local.py:18, 34`), factored out so `MicroVMSandbox` upholds the identical guarantee before forwarding any path/glob to the harness.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pathsafe.py
import pytest
from pathfinder.sandbox.pathsafe import reject_unsafe

def test_accepts_normal_relative_paths():
    for ok in ("aiplc-docs/audit.md", "aiplc-docs/discovery/x.md", "a-questions.md",
               "aiplc-docs/*-questions.md", "aiplc-docs/**/*.md"):
        reject_unsafe(ok)  # must not raise

def test_rejects_absolute_paths():
    with pytest.raises(ValueError):
        reject_unsafe("/etc/passwd")

def test_rejects_parent_traversal_segment():
    for bad in ("../evil.md", "aiplc-docs/../../evil.md", "../*"):
        with pytest.raises(ValueError):
            reject_unsafe(bad)

def test_dotdot_only_as_whole_segment():
    # A literal ".." substring inside a filename is NOT a traversal (matches
    # LocalSandbox: it checks Path(path).parts, so "..foo" is a safe name).
    reject_unsafe("aiplc-docs/..foo.md")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pathsafe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.sandbox.pathsafe'`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/sandbox/pathsafe.py
from __future__ import annotations
from pathlib import PurePosixPath

def reject_unsafe(path: str) -> None:
    """Raise ValueError if `path` could escape the workspace root.

    Identical guarantee to LocalSandbox._resolve (local.py): reject any path
    that is absolute (leading "/") or contains a ".." path segment. Wildcards
    ("*", "**", "?") are ordinary, non-".." segments and pass, so legitimate
    globs like "aiplc-docs/*-questions.md" are accepted. Used by MicroVMSandbox
    before it forwards any path/glob to the harness.
    """
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ValueError(f"unsafe path: {path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pathsafe.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/pathsafe.py backend/tests/test_pathsafe.py
git commit -m "feat: shared path-safety guard for sandbox path forwarding"
```

---

### Task 2: Shared sandbox contract module

**Files:**
- Create: `backend/tests/sandbox_contract.py` (helpers, not collected as tests itself)
- Create: `backend/tests/test_sandbox_contract.py` (runs the contract against `LocalSandbox` now; Task 5 extends it to `MicroVMSandbox`)

**Interfaces:**
- Consumes: `Sandbox`, `AgentEvent` from `pathfinder.sandbox.base`; `LocalSandbox`.
- Produces: `async def run_sandbox_contract(sb: Sandbox) -> None` plus the individual async assertion helpers it calls. `sb` must already be `start()`-ed. The contract exercises ONLY the public `Sandbox` surface (no scripting internals), so any conforming implementation passes it: read/write roundtrip, path-escape rejection on read/write/list, glob listing returns POSIX relative paths, and `send_message` yields ≥1 event ordered with exactly one terminal (`done`/`error`) event last.

Sibling-module import (`from sandbox_contract import ...`) works because this repo's `pyproject.toml` sets `pythonpath = ["."]` and pytest prepends the test dir (verified: a sibling `tests/*.py` module imports cleanly under this config).

- [ ] **Step 1: Write the contract module and the LocalSandbox test**

```python
# backend/tests/sandbox_contract.py
from __future__ import annotations
import pytest
from pathfinder.sandbox.base import Sandbox

async def _collect(aiter):
    return [e async for e in aiter]

async def assert_read_write_roundtrip(sb: Sandbox) -> None:
    await sb.write_file("aiplc-docs/audit.md", "hello")
    assert await sb.read_file("aiplc-docs/audit.md") == "hello"

async def assert_rejects_unsafe_paths(sb: Sandbox) -> None:
    for bad in ("../evil.md", "/etc/evil.md"):
        with pytest.raises(ValueError):
            await sb.write_file(bad, "x")
        with pytest.raises(ValueError):
            await sb.read_file(bad)
    with pytest.raises(ValueError):
        await sb.list_files("../*")

async def assert_list_glob_returns_relative_posix(sb: Sandbox) -> None:
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")  # must not match the glob
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]

async def assert_send_message_ordered_and_terminates(sb: Sandbox) -> None:
    events = await _collect(sb.send_message("hello"))
    assert len(events) >= 1, "a turn must yield at least one event"
    assert events[-1].kind in ("done", "error"), "a turn must end with done/error"
    # exactly one terminal event, and it is last
    assert all(e.kind not in ("done", "error") for e in events[:-1])

async def run_sandbox_contract(sb: Sandbox) -> None:
    await assert_read_write_roundtrip(sb)
    await assert_rejects_unsafe_paths(sb)
    await assert_list_glob_returns_relative_posix(sb)
    await assert_send_message_ordered_and_terminates(sb)
```

```python
# backend/tests/test_sandbox_contract.py
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from sandbox_contract import run_sandbox_contract

async def test_local_sandbox_satisfies_contract(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await run_sandbox_contract(sb)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sandbox_contract.py -v`
Expected: PASS (1 test). This proves the contract is satisfiable by the reference implementation before any MicroVM code exists; Task 5 adds the `MicroVMSandbox` run of the identical helpers.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/sandbox_contract.py backend/tests/test_sandbox_contract.py
git commit -m "test: shared Sandbox contract module, verified against LocalSandbox"
```

---

### Task 3: Harness protocol client (`HarnessClient`)

**Files:**
- Create: `backend/pathfinder/sandbox/harness.py`
- Create: `backend/tests/fakes/__init__.py`
- Create: `backend/tests/fakes/harness_app.py`
- Test: `backend/tests/test_harness_client.py`

**Interfaces:**
- Consumes: `AgentEvent` from `pathfinder.sandbox.base`; an injected `httpx.AsyncClient` (so tests drive it against a fake ASGI app via `ASGITransport`, and prod passes a real client).
- Produces: `HarnessClient(base_url: str, http: httpx.AsyncClient)` with:
  - `async def send_message(self, text: str) -> AsyncIterator[AgentEvent]` — `POST {base_url}/message` with JSON `{"text": text}`, reads the SSE response line-by-line, parses each `data: {json}` frame into an `AgentEvent`, yields them in order, and stops after a `done`/`error` frame (or end of stream). Async-generator function.
  - `async def read_file(self, rel_path: str) -> str` — `GET {base_url}/files/{rel_path}`, returns body text; raises `FileNotFoundError` on 404.
  - `async def write_file(self, rel_path: str, content: str) -> None` — `PUT {base_url}/files/{rel_path}` with the raw content body.
  - `async def list_files(self, glob: str) -> list[str]` — `GET {base_url}/files` with `params={"glob": glob}`, returns the JSON list of POSIX relative paths.
  - `async def heartbeat(self) -> bool` — `GET {base_url}/health`, returns `True` on 2xx else `False`.
  - The client does **no** path-safety (that is `MicroVMSandbox`'s job before it calls the client) and adds no logging of response text.

- [ ] **Step 1: Write the fake harness app + failing test**

```python
# backend/tests/fakes/__init__.py
```

```python
# backend/tests/fakes/harness_app.py
from __future__ import annotations
import json
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

def build_fake_harness_app(scripted_events: list[dict] | None = None) -> Starlette:
    """A Starlette app emulating the MicroVM harness for HarnessClient tests.

    In-memory file store + a scripted /message SSE stream. `scripted_events`
    is a list of AgentEvent-shaped dicts; defaults to an echo turn.
    """
    files: dict[str, str] = {}

    async def message(request):
        body = await request.json()
        events = scripted_events or [
            {"kind": "message", "text": f"echo: {body['text']}", "path": None},
            {"kind": "done", "text": None, "path": None},
        ]
        async def gen():
            for ev in events:
                yield {"data": json.dumps(ev)}
        return EventSourceResponse(gen())

    async def get_file(request):
        path = request.path_params["path"]
        if path not in files:
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(files[path])

    async def put_file(request):
        path = request.path_params["path"]
        files[path] = (await request.body()).decode("utf-8")
        return Response(status_code=204)

    async def list_files(request):
        import fnmatch
        glob = request.query_params.get("glob", "*")
        return JSONResponse(sorted(p for p in files if fnmatch.fnmatch(p, glob)))

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/message", message, methods=["POST"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/files/{path:path}", get_file, methods=["GET"]),
        Route("/files/{path:path}", put_file, methods=["PUT"]),
        Route("/health", health, methods=["GET"]),
    ])
```

```python
# backend/tests/test_harness_client.py
import httpx
import pytest
from pathfinder.sandbox.harness import HarnessClient
from fakes.harness_app import build_fake_harness_app

def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")

async def test_send_message_streams_ordered_events():
    app = build_fake_harness_app([
        {"kind": "status", "text": "working", "path": None},
        {"kind": "message", "text": "hi there", "path": None},
        {"kind": "done", "text": None, "path": None},
    ])
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        events = [e async for e in hc.send_message("go")]
    assert [e.kind for e in events] == ["status", "message", "done"]
    assert events[1].text == "hi there"

async def test_send_message_stops_on_error_frame():
    app = build_fake_harness_app([
        {"kind": "status", "text": "working", "path": None},
        {"kind": "error", "text": "boom", "path": None},
    ])
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        events = [e async for e in hc.send_message("go")]
    assert events[-1].kind == "error"
    assert events[-1].text == "boom"

async def test_file_write_read_roundtrip():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.write_file("aiplc-docs/x.md", "content")
        assert await hc.read_file("aiplc-docs/x.md") == "content"

async def test_read_missing_file_raises_filenotfound():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        with pytest.raises(FileNotFoundError):
            await hc.read_file("aiplc-docs/missing.md")

async def test_list_files_returns_matching_paths():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.write_file("aiplc-docs/a-questions.md", "x")
        await hc.write_file("aiplc-docs/b-questions.md", "y")
        await hc.write_file("aiplc-docs/audit.md", "z")
        found = await hc.list_files("aiplc-docs/*-questions.md")
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]

async def test_heartbeat_true_on_healthy():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.heartbeat() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_harness_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.sandbox.harness'`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/sandbox/harness.py
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
from pathfinder.sandbox.base import AgentEvent

_TERMINAL = ("done", "error")

class HarnessClient:
    """HTTP client for the MicroVM harness protocol (spec §2).

    Pure transport: performs no path-safety (the caller guarantees safe paths)
    and no credential redaction (that happens at the route seam, on the
    AgentEvent objects this yields). `http` is injected so tests can drive a
    fake ASGI harness via httpx.ASGITransport.
    """

    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._http = http

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        async with self._http.stream(
            "POST", f"{self._base}/message", json={"text": text}
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

    async def read_file(self, rel_path: str) -> str:
        resp = await self._http.get(f"{self._base}/files/{rel_path}")
        if resp.status_code == 404:
            raise FileNotFoundError(rel_path)
        resp.raise_for_status()
        return resp.text

    async def write_file(self, rel_path: str, content: str) -> None:
        resp = await self._http.put(
            f"{self._base}/files/{rel_path}",
            content=content.encode("utf-8"),
        )
        resp.raise_for_status()

    async def list_files(self, glob: str) -> list[str]:
        resp = await self._http.get(f"{self._base}/files", params={"glob": glob})
        resp.raise_for_status()
        return list(resp.json())

    async def heartbeat(self) -> bool:
        try:
            resp = await self._http.get(f"{self._base}/health")
        except httpx.HTTPError:
            return False
        return resp.is_success
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_harness_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/harness.py backend/tests/fakes/ backend/tests/test_harness_client.py
git commit -m "feat: HarnessClient for the MicroVM harness protocol"
```

---

### Task 4: MicroVM control-plane abstraction + boot spec + fake

**Files:**
- Create: `backend/pathfinder/sandbox/microvm_control.py`
- Test: `backend/tests/test_microvm_control.py`

**Interfaces:**
- Produces:
  - `BootSpec` (dataclass): `region: str = "ap-northeast-1"`, `snapshot_id: str | None = None`, `exec_role_arn: str | None = None`, `anthropic_model: str | None = None`, `max_idle_seconds: int = 300`, `auto_resume: bool = True`; method `env() -> dict[str, str]` returning the MicroVM env — always `{"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": region}`, plus `"ANTHROPIC_MODEL": anthropic_model` **only if set**. Contains no key material (IAM role auth).
  - `VMHandle` (dataclass): `vm_id: str`, `base_url: str`, `status: VMStatus`.
  - `VMStatus = Literal["booting", "ready", "suspended", "stopped"]`.
  - `MicroVMController` (ABC): `async boot(project_id: str, spec: BootSpec) -> VMHandle`, `async resume(handle: VMHandle) -> VMHandle`, `async suspend(handle: VMHandle) -> None`, `async stop(handle: VMHandle) -> None`, `async status(handle: VMHandle) -> VMStatus`.
  - `FakeMicroVMController(base_url: str)` — in-memory implementation for unit tests. Records `boot_calls`, `resume_calls`, `suspend_calls`, `stop_calls`; `boot` returns a `VMHandle` pointing at `base_url` with status `"ready"`; transitions status on suspend/resume/stop. `boot` is where a future Part-2 recovery test can inject a "previous VM expired" signal.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_microvm_control.py
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController

def test_bootspec_env_has_bedrock_flag_and_region():
    spec = BootSpec(region="ap-northeast-1")
    env = spec.env()
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "ap-northeast-1"

def test_bootspec_omits_model_until_resolved():
    # anthropic_model stays None until verified via `aws bedrock list-inference-profiles`
    assert "ANTHROPIC_MODEL" not in BootSpec().env()
    assert "ANTHROPIC_MODEL" in BootSpec(anthropic_model="apac.anthropic.claude-sonnet-5-vX").env()

def test_bootspec_env_has_no_credential_material():
    # IAM-role auth only — no static keys of any shape may appear in the env.
    env = BootSpec(anthropic_model="apac.anthropic.claude-sonnet-5-vX").env()
    joined = " ".join(f"{k}={v}" for k, v in env.items())
    for marker in ("AKIA", "sk-", "bedrock-api-key-", "AWS_BEARER_TOKEN", "AWS_SECRET"):
        assert marker not in joined

async def test_fake_controller_boot_ready():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("proj-1", BootSpec())
    assert handle.status == "ready"
    assert handle.base_url == "http://fake-vm"
    assert ctrl.boot_calls == 1

async def test_fake_controller_suspend_resume_stop():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("proj-1", BootSpec())
    await ctrl.suspend(handle)
    assert await ctrl.status(handle) == "suspended"
    handle = await ctrl.resume(handle)
    assert handle.status == "ready"
    await ctrl.stop(handle)
    assert await ctrl.status(handle) == "stopped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_microvm_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.sandbox.microvm_control'`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/sandbox/microvm_control.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

VMStatus = Literal["booting", "ready", "suspended", "stopped"]

@dataclass
class BootSpec:
    """Everything needed to boot a Claude Code MicroVM (spec §1, §6).

    Auth is via the MicroVM IAM execution role (CLAUDE_CODE_USE_BEDROCK); there
    are NO long-lived keys. `anthropic_model` pins Claude Code to Sonnet 5 via
    the Bedrock cross-region inference profile id — left None until resolved via
    `aws bedrock list-inference-profiles` (Task 6); do NOT hardcode a guess.
    """
    region: str = "ap-northeast-1"
    snapshot_id: str | None = None
    exec_role_arn: str | None = None
    anthropic_model: str | None = None
    max_idle_seconds: int = 300
    auto_resume: bool = True

    def env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": self.region,
        }
        if self.anthropic_model:
            env["ANTHROPIC_MODEL"] = self.anthropic_model
        return env

@dataclass
class VMHandle:
    vm_id: str
    base_url: str
    status: VMStatus

class MicroVMController(ABC):
    """Control-plane for the Lambda MicroVM lifecycle. The concrete AWS binding
    is LambdaMicroVMController (microvm_control_aws.py); tests use
    FakeMicroVMController. MicroVMSandbox depends only on this ABC."""

    @abstractmethod
    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle: ...
    @abstractmethod
    async def resume(self, handle: VMHandle) -> VMHandle: ...
    @abstractmethod
    async def suspend(self, handle: VMHandle) -> None: ...
    @abstractmethod
    async def stop(self, handle: VMHandle) -> None: ...
    @abstractmethod
    async def status(self, handle: VMHandle) -> VMStatus: ...

@dataclass
class FakeMicroVMController(MicroVMController):
    """In-memory controller for unit tests. Points every VM at `base_url`
    (a fake harness). Records call counts and tracks status."""
    base_url: str
    boot_calls: int = 0
    resume_calls: int = 0
    suspend_calls: int = 0
    stop_calls: int = 0
    _status: dict[str, VMStatus] = field(default_factory=dict)

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        self.boot_calls += 1
        vm_id = f"fake-{project_id}-{self.boot_calls}"
        self._status[vm_id] = "ready"
        return VMHandle(vm_id=vm_id, base_url=self.base_url, status="ready")

    async def resume(self, handle: VMHandle) -> VMHandle:
        self.resume_calls += 1
        self._status[handle.vm_id] = "ready"
        return VMHandle(vm_id=handle.vm_id, base_url=handle.base_url, status="ready")

    async def suspend(self, handle: VMHandle) -> None:
        self.suspend_calls += 1
        self._status[handle.vm_id] = "suspended"

    async def stop(self, handle: VMHandle) -> None:
        self.stop_calls += 1
        self._status[handle.vm_id] = "stopped"

    async def status(self, handle: VMHandle) -> VMStatus:
        return self._status.get(handle.vm_id, "stopped")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_microvm_control.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/microvm_control.py backend/tests/test_microvm_control.py
git commit -m "feat: MicroVM control-plane abstraction, BootSpec, and fake controller"
```

---

### Task 5: `MicroVMSandbox` — lazy boot, turn relay, serialization

**Files:**
- Create: `backend/pathfinder/sandbox/microvm.py`
- Create: `backend/tests/fakes/in_memory_harness.py`
- Modify: `backend/tests/test_sandbox_contract.py` (add the `MicroVMSandbox` contract run)
- Test: `backend/tests/test_microvm_sandbox.py`

**Interfaces:**
- Consumes: `Sandbox`, `AgentEvent` (`base`); `reject_unsafe` (Task 1); `MicroVMController`, `BootSpec`, `VMHandle` (Task 4); a harness client of the `HarnessClient` shape (Task 3) built by an injected factory.
- Produces: `MicroVMSandbox(project_id: str, controller: MicroVMController, spec: BootSpec, harness_factory: Callable[[VMHandle], HarnessLike])` implementing the `Sandbox` ABC exactly. `HarnessLike` is any object with `send_message/read_file/write_file/list_files/heartbeat` (duck-typed; prod passes a real `HarnessClient`, tests pass a `FakeHarness`).
  - `async start() -> None` — **does not boot** (lazy creation). Marks the sandbox initialized and cleanly represents the "not yet booted" state: `self._handle is None`.
  - `async _ensure_ready() -> HarnessLike` — boots on first use (single-flight via a lock), resumes if suspended; returns the live harness client. Idempotent.
  - `read_file/write_file/list_files` — call `reject_unsafe(path)` first, then `_ensure_ready()`, then forward to the harness. (Part 2 reroutes these to S3 when not booted, for true laziness; Part 1 boots on any file op and uses the harness.)
  - `send_message(text) -> AsyncIterator[AgentEvent]` — async-generator; serializes turns: if a turn is already active, yields exactly one `AgentEvent(kind="error", text="turn already in progress")` and returns (soft busy signal — no hard queue). Otherwise `_ensure_ready()` then relays the harness's events through unchanged, ending on `done`/`error`. (Part 2 adds S3 sync after the terminal event.)
  - `set_input_holder(holder: str | None) -> None` / `input_holder` attribute — the soft "current input holder" hint; no enforcement.
  - `async stop() -> None` — `controller.stop(handle)` if booted; resets to not-booted.

- [ ] **Step 1: Write the in-memory fake harness + failing tests**

```python
# backend/tests/fakes/in_memory_harness.py
from __future__ import annotations
import fnmatch
from typing import AsyncIterator
from pathfinder.sandbox.base import AgentEvent

class FakeHarness:
    """In-memory object with the HarnessClient method surface, for
    MicroVMSandbox unit tests (no HTTP). `events_for` maps a message text to a
    canned event list; the default is an echo turn ending in `done`."""

    def __init__(self, events_for=None):
        self.files: dict[str, str] = {}
        self._events_for = events_for or (
            lambda text: [
                AgentEvent(kind="message", text=f"echo: {text}"),
                AgentEvent(kind="done"),
            ]
        )

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for ev in self._events_for(text):
            yield ev

    async def read_file(self, rel_path: str) -> str:
        if rel_path not in self.files:
            raise FileNotFoundError(rel_path)
        return self.files[rel_path]

    async def write_file(self, rel_path: str, content: str) -> None:
        self.files[rel_path] = content

    async def list_files(self, glob: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatch(p, glob))

    async def heartbeat(self) -> bool:
        return True
```

```python
# backend/tests/test_microvm_sandbox.py
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness

def _sandbox():
    # One shared FakeHarness so writes-then-reads roundtrip across (re)boots.
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
    )
    return sb, ctrl, harness

async def test_start_does_not_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    assert ctrl.boot_calls == 0          # lazy: no VM until first use
    assert sb._handle is None            # "not yet booted" represented cleanly

async def test_first_file_op_boots_once_and_reuses():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert ctrl.boot_calls == 1
    assert await sb.read_file("aiplc-docs/x.md") == "hi"
    assert ctrl.boot_calls == 1          # reused, not re-booted

async def test_path_safety_rejected_before_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert ctrl.boot_calls == 0          # guard runs before any control-plane call

async def test_send_message_relays_ordered_events():
    sb, _, _ = _sandbox()
    await sb.start()
    events = [e async for e in sb.send_message("승인")]
    assert [e.kind for e in events] == ["message", "done"]
    assert "승인" in events[0].text

async def test_concurrent_turn_gets_busy_signal():
    sb, _, _ = _sandbox()
    await sb.start()
    sb._turn_active = True               # simulate an in-flight turn
    events = [e async for e in sb.send_message("second")]
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "in progress" in events[0].text

async def test_input_holder_hint_is_settable():
    sb, _, _ = _sandbox()
    await sb.start()
    assert sb.input_holder is None
    sb.set_input_holder("facilitator-42")
    assert sb.input_holder == "facilitator-42"

async def test_stop_resets_to_not_booted():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None
```

- [ ] **Step 2: Add the MicroVMSandbox contract run**

```python
# append to backend/tests/test_sandbox_contract.py
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from sandbox_contract import run_sandbox_contract

async def test_microvm_sandbox_satisfies_same_contract():
    harness = FakeHarness()
    sb = MicroVMSandbox(
        project_id="p1",
        controller=FakeMicroVMController(base_url="http://fake-vm"),
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
    )
    await sb.start()
    await run_sandbox_contract(sb)       # SAME assertions LocalSandbox passes
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_microvm_sandbox.py tests/test_sandbox_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.sandbox.microvm'`

- [ ] **Step 4: Write the implementation**

```python
# backend/pathfinder/sandbox/microvm.py
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Callable, Protocol
from pathfinder.sandbox.base import Sandbox, AgentEvent
from pathfinder.sandbox.pathsafe import reject_unsafe
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle

class HarnessLike(Protocol):
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def read_file(self, rel_path: str) -> str: ...
    async def write_file(self, rel_path: str, content: str) -> None: ...
    async def list_files(self, glob: str) -> list[str]: ...
    async def heartbeat(self) -> bool: ...

class MicroVMSandbox(Sandbox):
    """Real sandbox: boots a Claude Code MicroVM (with aiplc-rules baked into
    the image) and relays turns over the harness. Implements the Sandbox ABC
    exactly, so it drops into make_sandbox with zero route changes.

    Part 1 scope: file ops lazily boot the VM and use the live harness. Part 2
    reroutes not-booted file ops to S3 and syncs after each turn. No
    methodology/resume logic lives here — session-continuity is the rule's job.
    """

    def __init__(
        self,
        project_id: str,
        controller: MicroVMController,
        spec: BootSpec,
        harness_factory: Callable[[VMHandle], HarnessLike],
    ):
        self.project_id = project_id
        self._controller = controller
        self._spec = spec
        self._harness_factory = harness_factory
        self._handle: VMHandle | None = None
        self._harness: HarnessLike | None = None
        self._boot_lock = asyncio.Lock()
        self._turn_active = False
        self.input_holder: str | None = None

    async def start(self) -> None:
        # Lazy: do NOT boot here. A project can exist with no live MicroVM until
        # first needed. "Not yet booted" == self._handle is None.
        self._handle = None
        self._harness = None

    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder

    async def _ensure_ready(self) -> HarnessLike:
        async with self._boot_lock:
            if self._handle is None:
                self._handle = await self._controller.boot(self.project_id, self._spec)
                self._harness = self._harness_factory(self._handle)
            elif self._handle.status == "suspended":
                self._handle = await self._controller.resume(self._handle)
                self._harness = self._harness_factory(self._handle)
            assert self._harness is not None
            return self._harness

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        harness = await self._ensure_ready()
        return await harness.read_file(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        harness = await self._ensure_ready()
        await harness.write_file(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        harness = await self._ensure_ready()
        return await harness.list_files(glob)

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        # Single Claude Code session per project: serialize turns. A concurrent
        # turn gets a clear soft busy signal (no hard queue).
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            async for event in harness.send_message(text):
                yield event
            # Part 2 hook: after the terminal event, sync workspace -> S3 here.
        finally:
            self._turn_active = False

    async def stop(self) -> None:
        if self._handle is not None:
            await self._controller.stop(self._handle)
        self._handle = None
        self._harness = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_microvm_sandbox.py tests/test_sandbox_contract.py -v`
Expected: PASS (7 + 2 = 9 tests). The `test_microvm_sandbox_satisfies_same_contract` case is the proof that `MicroVMSandbox` is a drop-in for `LocalSandbox` against the fixed `Sandbox` boundary.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/sandbox/microvm.py backend/tests/fakes/in_memory_harness.py backend/tests/test_microvm_sandbox.py backend/tests/test_sandbox_contract.py
git commit -m "feat: MicroVMSandbox with lazy boot, turn serialization, and contract parity"
```

---

### Task 6: Env-gated `make_sandbox` swap (zero route changes)

**Files:**
- Create: `backend/pathfinder/sandbox/microvm_control_aws.py` (AWS binding skeleton; body pinned in Task 7)
- Modify: `backend/pathfinder/app.py` (`make_sandbox` becomes env-gated; add a monkeypatchable controller factory)
- Test: `backend/tests/test_make_sandbox.py`

**Interfaces:**
- Consumes: `MicroVMSandbox`, `BootSpec`, `HarnessClient`, `LocalSandbox`, `MicroVMController`.
- Produces:
  - `LambdaMicroVMController(spec_region: str)` in `microvm_control_aws.py` — implements `MicroVMController`; its methods currently raise `NotImplementedError` with a pointer to Task 7 (the real AWS API is unverifiable without AWS, so its body is completed in the integration task, not asserted in CI).
  - `app.make_sandbox(project_id: str) -> Sandbox` — unchanged signature; if `os.environ.get("PATHFINDER_SANDBOX") == "microvm"` it builds a `MicroVMSandbox` (controller from `app.microvm_controller_factory(project_id)`, `BootSpec` from env vars, a `harness_factory` that wraps a shared `httpx.AsyncClient`), else it builds a `LocalSandbox` (the existing default, byte-for-byte behavior preserved).
  - `app.microvm_controller_factory(project_id: str) -> MicroVMController` — module-level hook (defaults to `LambdaMicroVMController`), monkeypatched in tests to inject a `FakeMicroVMController` so no AWS is touched. This mirrors how Phase 1 tests monkeypatch `make_sandbox`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_make_sandbox.py
import importlib
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController

async def test_default_is_local_sandbox(monkeypatch):
    monkeypatch.delenv("PATHFINDER_SANDBOX", raising=False)
    sb = await app_module.make_sandbox("proj-local")
    assert isinstance(sb, LocalSandbox)

async def test_microvm_flag_builds_microvm_sandbox(monkeypatch):
    monkeypatch.setenv("PATHFINDER_SANDBOX", "microvm")
    # Inject a fake controller so no AWS is contacted.
    monkeypatch.setattr(
        app_module, "microvm_controller_factory",
        lambda project_id: FakeMicroVMController(base_url="http://fake-vm"),
    )
    sb = await app_module.make_sandbox("proj-vm")
    assert isinstance(sb, MicroVMSandbox)
    await sb.start()
    assert sb._handle is None  # lazy: still not booted right after creation

def test_make_sandbox_signature_unchanged():
    import inspect
    sig = inspect.signature(app_module.make_sandbox)
    assert list(sig.parameters) == ["project_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_make_sandbox.py -v`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute 'microvm_controller_factory'` (and the microvm branch does not yet exist).

- [ ] **Step 3: Write the AWS controller skeleton**

```python
# backend/pathfinder/sandbox/microvm_control_aws.py
from __future__ import annotations
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle, VMStatus

class LambdaMicroVMController(MicroVMController):
    """AWS Lambda MicroVM control-plane binding (ap-northeast-1).

    The exact boot/resume/suspend API (snapshot id, maxIdleDurationSeconds,
    autoResumeEnabled, IAM exec role wiring) is resolved and completed in Task 7
    against real AWS — it cannot be verified in CI without credentials. Until
    then these raise NotImplementedError; unit tests inject FakeMicroVMController
    via app.microvm_controller_factory instead.
    """

    def __init__(self, region: str = "ap-northeast-1"):
        self.region = region

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        raise NotImplementedError("Task 7: bind to the real Lambda MicroVM boot API")

    async def resume(self, handle: VMHandle) -> VMHandle:
        raise NotImplementedError("Task 7: bind to the real Lambda MicroVM resume API")

    async def suspend(self, handle: VMHandle) -> None:
        raise NotImplementedError("Task 7: bind to the real Lambda MicroVM suspend API")

    async def stop(self, handle: VMHandle) -> None:
        raise NotImplementedError("Task 7: bind to the real Lambda MicroVM stop API")

    async def status(self, handle: VMHandle) -> VMStatus:
        raise NotImplementedError("Task 7: bind to the real Lambda MicroVM status API")
```

- [ ] **Step 4: Modify `make_sandbox`**

Replace the body of `backend/pathfinder/app.py` with the env-gated factory (the `registry`, `app`, and router includes stay identical — only `make_sandbox` changes and two imports/hooks are added):

```python
# backend/pathfinder/app.py
from __future__ import annotations
import os
import tempfile
from pathlib import Path
import httpx
from fastapi import FastAPI
from pathfinder.workspace import ProjectRegistry
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import Sandbox
from pathfinder.sandbox.harness import HarnessClient
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, MicroVMController, VMHandle
from pathfinder.sandbox.microvm_control_aws import LambdaMicroVMController

registry = ProjectRegistry()

# Monkeypatchable in tests to inject a FakeMicroVMController (no AWS).
def microvm_controller_factory(project_id: str) -> MicroVMController:
    return LambdaMicroVMController(region=os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1"))

def _boot_spec() -> BootSpec:
    return BootSpec(
        region=os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1"),
        snapshot_id=os.environ.get("PATHFINDER_VM_SNAPSHOT") or None,
        exec_role_arn=os.environ.get("PATHFINDER_VM_ROLE_ARN") or None,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL") or None,  # resolved in Task 7
    )

async def _make_microvm_sandbox(project_id: str) -> Sandbox:
    controller = microvm_controller_factory(project_id)
    shared_http = httpx.AsyncClient(timeout=None)  # streaming SSE: no read timeout
    def harness_factory(handle: VMHandle) -> HarnessClient:
        return HarnessClient(base_url=handle.base_url, http=shared_http)
    sb = MicroVMSandbox(
        project_id=project_id,
        controller=controller,
        spec=_boot_spec(),
        harness_factory=harness_factory,
    )
    await sb.start()
    return sb

async def _make_local_sandbox(project_id: str) -> Sandbox:
    root = Path(tempfile.mkdtemp(prefix=f"pf-{project_id}-"))
    sb = LocalSandbox(root=root)
    await sb.start()
    return sb

async def make_sandbox(project_id: str) -> Sandbox:
    if os.environ.get("PATHFINDER_SANDBOX") == "microvm":
        return await _make_microvm_sandbox(project_id)
    return await _make_local_sandbox(project_id)

app = FastAPI(title="Pathfinder")

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)

from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)

from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_make_sandbox.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full suite (no regressions in Phase 1 routes/tests)**

Run: `cd backend && python -m pytest -v`
Expected: PASS — all Phase 1 tests (unchanged behavior; env flag defaults to `LocalSandbox`) plus this plan's Tasks 1–6. Confirms the seam swap requires **zero route/parser changes**.

- [ ] **Step 7: Commit**

```bash
git add backend/pathfinder/sandbox/microvm_control_aws.py backend/pathfinder/app.py backend/tests/test_make_sandbox.py
git commit -m "feat: env-gated make_sandbox swap to MicroVMSandbox (routes unchanged)"
```

---

### Task 7: Integration — real MicroVM boot, rules injection, Sonnet-5 turn (requires AWS)

> **INTEGRATION — REQUIRES AWS. This task is scripted manual verification, NOT pytest.** Do not add these as CI tests; they need real credentials, a real Lambda MicroVM, and Bedrock access in `ap-northeast-1`. Record observed outputs in the PR description. This task also completes the `LambdaMicroVMController` body against the real API.

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm_control_aws.py` (fill in the real boot/resume/suspend/stop/status calls once the API is confirmed here)

**Interfaces:**
- Consumes: `LambdaMicroVMController`, `BootSpec`, `HarnessClient`, `MicroVMSandbox`.

- [ ] **Step 1: Resolve the Sonnet-5 Bedrock inference-profile id (do NOT hardcode a guess)**

Run:
```bash
aws bedrock list-inference-profiles --region ap-northeast-1 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'sonnet-5')].[inferenceProfileId,status]" \
  --output table
```
Expected observation: at least one `ACTIVE` cross-region profile whose id contains `sonnet-5` (e.g. an `apac.anthropic.claude-sonnet-5-*` form — **use the exact id printed**, do not assume). Export it:
```bash
export ANTHROPIC_MODEL="<exact id from the command above>"
```
If no Sonnet-5 profile is listed in `ap-northeast-1`, STOP and escalate — the model pin cannot be satisfied and this is a blocking open question (see Open Questions).

- [ ] **Step 2: Confirm MicroVM control-plane API and complete `LambdaMicroVMController`**

Using the confirmed Lambda MicroVM / Firecracker-snapshot API for `ap-northeast-1`, fill in `LambdaMicroVMController.boot/resume/suspend/stop/status` so `boot`:
- launches from the pre-baked snapshot (Claude Code + `aiplc-rules` + harness already installed),
- attaches the IAM **execution role** (`BootSpec.exec_role_arn`) — NO static keys,
- injects `BootSpec.env()` (`CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION`, `ANTHROPIC_MODEL`),
- sets `maxIdleDurationSeconds=BootSpec.max_idle_seconds` and `autoResumeEnabled=BootSpec.auto_resume`,
- returns a `VMHandle` whose `base_url` is the harness HTTPS endpoint.

Verify the harness is reachable:
```bash
curl -fsS "$VM_BASE_URL/health"
```
Expected: HTTP 200 with a JSON body indicating the Claude Code process is alive.

- [ ] **Step 3: Confirm `aiplc-rules` are present in the booted workspace**

```bash
curl -fsS "$VM_BASE_URL/files?glob=aiplc-rules/**/*.md" | python -m json.tool
```
Expected: a non-empty list including `aws-aiplc-rules/core-workflow.md` and `common/session-continuity.md`, confirming the rules were baked into the image and mounted in the workspace.

- [ ] **Step 4: Drive one real Sonnet-5 turn end-to-end**

With `PATHFINDER_SANDBOX=microvm` and the env from Steps 1–2, start the backend and drive a single turn:
```bash
curl -fsS -X POST localhost:8000/projects -H 'content-type: application/json' \
  -d '{"project_id":"aws-smoke"}'
curl -N -sS "localhost:8000/projects/aws-smoke/events?text=ai-plc%EB%A5%BC%20%EC%8B%9C%EC%9E%91%ED%95%98%EA%B3%A0%20%EC%8B%B6%EC%96%B4"
```
Expected observations:
- The SSE stream yields ordered `AgentEvent` frames ending in a `done` frame (Bedrock auth via the IAM role succeeded — no `ValidationException`, which is the pilot1 failure mode a wrong model id caused).
- A subsequent `GET /projects/aws-smoke/questions/<generated file>` returns a parsed question file, proving the agent wrote a real artifact under the rules.
- Grep the streamed body for credential markers: `AKIA`, `sk-`, `bedrock-api-key-`, `AWS_BEARER_TOKEN=` — expected: none present (redaction at the route seam holds on real output).

- [ ] **Step 5: Record results and commit the completed controller**

```bash
git add backend/pathfinder/sandbox/microvm_control_aws.py
git commit -m "feat: complete LambdaMicroVMController against verified AWS MicroVM API"
```
Paste the observed `ANTHROPIC_MODEL` id, `/health` result, rules listing, and turn transcript summary into the PR description as the integration evidence.

---

## Self-Review

**Design-decision coverage (every confirmed decision mapped to a task, or explicitly deferred to Part 2):**
- Lazy creation + aggressive suspend → Task 5 (`start()` does not boot; `_ensure_ready` lazy-boots; `_handle is None` == not booted). `maxIdleDurationSeconds`/`autoResumeEnabled` → Task 4 `BootSpec` + applied in Task 7 boot.
- "not yet booted" represented cleanly → Task 5 (`_handle`/`_harness` are `None`; `start()` is a no-op boot-wise; `test_start_does_not_boot`).
- Concurrency (single session, serialized turns, soft busy signal, input-holder hint, no hard queue) → Task 5 (`_turn_active` guard, `set_input_holder`; `test_concurrent_turn_gets_busy_signal`, `test_input_holder_hint_is_settable`).
- S3 sync after every turn → **deferred to Part 2** (explicit hook comment in `send_message`; needs `S3Store`).
- Recovery on expiry/failure + restore-from-S3 + methodology self-resume (no custom resume logic) → **deferred to Part 2** (Part 1 has no durable store; the "no resume logic" constraint is honored by containing none here).
- AUTH: Bedrock via IAM exec role (`CLAUDE_CODE_USE_BEDROCK`), no long-lived keys → Task 4 (`BootSpec.env`, `test_bootspec_env_has_no_credential_material`) + Task 7 (role attach).
- Sonnet-5 pin via `ANTHROPIC_MODEL` = Bedrock inference profile, verified not hardcoded → Task 4 (`anthropic_model` stays `None`) + Task 7 Step 1 (`aws bedrock list-inference-profiles`).
- Region `ap-northeast-1` → Task 4 default + Task 7; S3-in-Seoul + cross-region disclosure → Part 2 (noted in Scope).
- Contract-test module both sandboxes run → Task 2 (module) + Task 5 (`MicroVMSandbox` run of the identical helpers).
- Harness protocol client tested against a fake HTTP server → Task 3.
- `MicroVMSandbox` implements the ABC exactly / drops into `make_sandbox` with zero route changes → Task 5 (contract parity) + Task 6 (`test_make_sandbox_signature_unchanged`, full-suite green).
- Path-safety upheld for any forwarded path → Task 1 (`reject_unsafe`) + Task 5 (`test_path_safety_rejected_before_boot`, and the contract's `assert_rejects_unsafe_paths` run against `MicroVMSandbox`).
- Never defeat credential redaction / no new logging → Task 3 (client logs no response text) + Task 5 (relays `AgentEvent`s unchanged; route-seam redaction in `turns.py` untouched).
- Unit-testable without AWS; integration clearly labeled and not run as CI pytest → Tasks 1–6 are pure unit (injected fakes); Task 7 is labeled "INTEGRATION — REQUIRES AWS," scripted manual with AWS CLI.

**Placeholder scan:** No TBD/TODO/"similar to Task N". Every code step shows complete code. The only intentional `NotImplementedError` is `LambdaMicroVMController` (Task 6 skeleton), which is honestly deferred to the AWS-only Task 7 rather than faked as CI-passing — and the reason is stated inline.

**Type/signature consistency with the `Sandbox` ABC and neighbors:** `MicroVMSandbox` methods match `base.py` exactly — `async start()->None`, `async read_file(rel_path:str)->str`, `async write_file(rel_path:str,content:str)->None`, `async list_files(glob:str)->list[str]`, `send_message(text:str)->AsyncIterator[AgentEvent]` (async-generator, like `LocalSandbox`), `async stop()->None`. `HarnessClient`/`FakeHarness`/`HarnessLike` share one method surface. `BootSpec`/`VMHandle`/`VMStatus`/`MicroVMController` names are used identically across Tasks 4–6. `make_sandbox(project_id)->Sandbox` and module-level `registry` are preserved (Phase 1 routes/tests unaffected).

**Scope sizing:** 7 tasks (6 unit + 1 integration), comparable to Phase 1's density, all inside the single "compute relay" boundary. The larger durable-persistence/recovery surface is split out to Part 2 (named above), each part independently testable.

## Open Questions (surfaced, not resolved in this plan)

1. **Exact Lambda MicroVM control-plane API.** The spec says "Lambda MicroVM (Firecracker snapshot)" with `maxIdleDurationSeconds`/`autoResumeEnabled`, but the precise AWS API/SDK surface for boot/suspend/resume in `ap-northeast-1` is not pinned. The plan abstracts it behind `MicroVMController` and resolves the real calls in Task 7 — but if the intended service is actually Bedrock AgentCore, Firecracker-on-EC2, or a bespoke harness, the `LambdaMicroVMController` binding (only) changes; the sandbox/tests do not.
2. **Sonnet-5 availability in `ap-northeast-1`.** Task 7 Step 1 will confirm a Sonnet-5 cross-region inference profile exists in Tokyo. If none is `ACTIVE` there, the model pin is blocked (fallback: a different APAC profile, or accept a different region for compute — a governance decision).
3. **Suspend/resume vs. durable state (a Part 2 boundary risk).** Snapshot resume restores the VM's own filesystem, but writes that landed in S3 while suspended (e.g. `[Answer]` write-backs during early Discovery) would be stale on resume. Part 2 must add a reconcile step (re-push S3-newer files into the resumed VM). Flagging here because it shapes the `_ensure_ready` resume path defined in Task 5.
4. **Busy-signal vs. wait.** Part 1 implements the soft busy **signal** (concurrent turn gets an immediate `error` event). The design permits "wait" as an alternative; if product wants the waiting behavior, it is a localized change to `send_message` (await the lock) — noted so it is a conscious choice, not an oversight.
