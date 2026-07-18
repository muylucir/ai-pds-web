# Pathfinder AWS Integration (Real Harness · CDK Tokyo Infra · Controller Binding · Drills) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the two "INTEGRATION — requires AWS" stubs (Part-1 Task 7, Part-2 Task 8) into real, testable software: a production in-VM harness server, a TypeScript CDK stack that builds the MicroVM image + roles in Tokyo, a real boto3 `LambdaMicroVMController` binding with JWE auth wiring, and a suite of scripted-manual AWS drills that prove boot/turn/persistence/recovery end-to-end.

**Architecture:** A new top-level `harness/` package is the code that runs *inside* the MicroVM — a Starlette server (port 8080) speaking the exact `HarnessClient` protocol, driving Claude Code via a subprocess `claude_driver`, plus a hooks server (port 9000) for the image `/ready` and `/validate` lifecycle. A new top-level `infra/` CDK app packages `harness/` + `files/aiplc-rules/` into an `AWS::Lambda::MicrovmImage` and provisions the build/execution roles and artifacts bucket, all pinned to `ap-northeast-1`. The existing `LambdaMicroVMController` skeleton is filled with a boto3 `lambda-microvms` client (poll-until-RUNNING boot), `app.py`'s `harness_factory` mints a `CreateMicrovmAuthToken` JWE and attaches it via a new `HarnessClient(headers=...)` seam, and Phase-B bash drills exercise the whole thing against real AWS.

**Tech Stack:** Python 3.11 (backend + harness: Starlette, sse-starlette, httpx, uvicorn, boto3/botocore Stubber), Claude Code CLI + Node (baked into the image), TypeScript CDK (`aws-cdk-lib` v2, `constructs`, `ts-node`), Bedrock (`global.anthropic.claude-sonnet-5` inference profile), AWS Lambda MicroVMs (GA 2026-06-22, `ap-northeast-1`), bash (`set -euo pipefail`) drills.

## Global Constraints

- **Region — code defaults STAY Seoul; drills unify on Tokyo.** `s3_store_factory` default `PATHFINDER_S3_REGION=ap-northeast-2` (Seoul) is unchanged in code; drill scripts export `PATHFINDER_S3_REGION=ap-northeast-1` so all drill resources live in Tokyo (user decision — synthetic data only). Production region choice (Seoul-persistence + customer disclosure vs Tokyo-unified) is re-decided before the first real workshop.
- **Lambda MicroVMs is `ap-northeast-1` only.** GA 2026-06-22; ABSENT in Seoul. All MicroVM/image/CDK resources pin `ap-northeast-1`.
- **Model pin:** `ANTHROPIC_MODEL=global.anthropic.claude-sonnet-5` (Bedrock inference profile, confirmed ACTIVE `ap-northeast-1` 2026-07-17). Injected from env, never hardcoded. Re-verified in the preflight drill.
- **IAM shape (pilot1-validated):** execution role gets `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` on BOTH `arn:aws:bedrock:*:<acct>:inference-profile/global.anthropic.claude-sonnet-5` AND `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5*` (a `global.*` profile fans out across regions — a single-region model ARN caused pilot1's `ValidationException`). Execution role gets NO S3 (preserves the security boundary: the VM cannot reach durable storage).
- **Harness endpoint auth:** header **`X-aws-proxy-auth: <token>`** (NOT `Authorization: Bearer` — the old Part-1 Task-7 text guessed wrong). Default proxy port **8080** (harness listens there); override per-request with `X-aws-proxy-port`. Tokens from `CreateMicrovmAuthToken`, max TTL 60 min → we mint 30 min, `allowedPorts=[{"port":8080}]`.
- **`boto3` floor bump — REQUIRED.** The `lambda-microvms` service model must be present in the installed botocore (it is NOT in the repo's current `boto3>=1.34` pin → `boto3.client("lambda-microvms")` raises `UnknownServiceError`, verified 2026-07-18). Task 4 bumps `backend/pyproject.toml` to `boto3>=1.40` (the first floor whose bundled botocore ships the GA `lambda-microvms` model). Exact minimum version is an Open Question confirmed by the preflight `get_available_services()` check.
- **No moto for MicroVMs.** `lambda-microvms` has no moto support; the CI-testable seam is botocore `Stubber` (real client, stubbed HTTP). `S3Store` keeps its existing moto coverage.
- **Auth is IAM-role only.** Bedrock via the MicroVM execution role (`CLAUDE_CODE_USE_BEDROCK=1`); no long-lived keys anywhere. `X-aws-proxy-auth` tokens are short-lived, minted per handle transition (mint-on-resume).
- **Redaction-at-rest is already implemented** (`_sync_workspace_to_s3` redacts `aiplc-docs/audit.md`). Part-2's old "empirical grep decides" step is now a VERIFICATION drill: expect NO credential markers in S3-at-rest `audit.md`.
- **Scope guards — do NOT touch:** FastAPI routes, parsers, the `Sandbox` ABC, `LocalSandbox`, `MicroVMSandbox` (no change needed), the frontend. `HarnessClient` changes are additive-only (new optional `headers` param; `FakeHarness`/contract untouched).
- **Regression floors:** backend suite 136 passed, frontend 123 passed. Nothing may regress them. New backend unit tests are pure (Stubber + stub `claude` executable, no AWS) and run in CI. Harness unit tests likewise (no AWS). All Phase-B drills are labeled INTEGRATION — REQUIRES AWS and are NEVER collected by pytest.
- **New top-level dirs:** `harness/` and `infra/` are new, each self-contained with their own dependency manifest. `files/` is gitignored reference material — the CDK asset references `files/aiplc-rules/` by path; the drill machine MUST have it present.

---

## File Structure

**New — `harness/` (production code, runs inside the MicroVM):**
- `harness/claude_driver.py` — subprocess driver. Pure `translate(obj: dict, workspace: str) -> list[AgentEvent]` maps one Claude Code `--output-format stream-json` object to zero or more `AgentEvent`s IN BLOCK ORDER (a real assistant message can carry several content blocks together — text + tool_use, or parallel tool_use — every block is translated, not just the first); a `Write`/`Edit`/`MultiEdit` `file_path` that would resolve outside `workspace` (e.g. via a `..` segment) is rejected — no `file_changed`, no path echo, emits `status` instead. `ClaudeDriver.run(text, *, continue_session)` spawns `claude -p … --output-format stream-json --verbose --dangerously-skip-permissions [--continue]` (cwd=workspace), drains stderr concurrently (never surfaced verbatim — only a bounded `"claude exited N"` on failure), and yields `AgentEvent`s (nonzero exit / parse failure → `error`); on generator abandonment (early `.aclose()`/cancellation) a `try/finally` kills and reaps the subprocess so no orphan `claude` process survives the call.
- `harness/app.py` — Starlette server on 8080. `build_app(driver, workspace)`: POST `/message` (SSE of `AgentEvent` JSON, `--continue` after the first turn), GET/PUT `/files/{path}`, GET `/files?glob=`, GET `/health`. Speaks the identical protocol as `backend/tests/fakes/harness_app.py`.
- `harness/hooks.py` — Starlette server on 9000 for the image lifecycle. `build_hooks_app(version_check, rules_present, health_check)`: GET `/ready` (200 once HTTP up AND `claude --version` succeeds → platform snapshots), GET `/validate` (200 when `/health` ok AND rules files present after resume — a cheap re-check, NOT a paid Sonnet turn; tradeoff documented).
- `harness/serve.py` — entrypoint: `asyncio.gather` two `uvicorn.Server`s (app:8080, hooks:9000). This is the container CMD.
- `harness/Dockerfile` — layers Node + Claude Code CLI + python deps over the al2023 base (base supplied at build via `BaseImageArn`), copies `harness/` code + `files/aiplc-rules/` → `/workspace/aiplc-rules`, `EXPOSE 8080 9000`, `CMD ["python","-m","serve"]`.
- `harness/pyproject.toml` — deps (starlette, sse-starlette, httpx, uvicorn), dev (pytest, pytest-asyncio), `asyncio_mode="auto"`, `pythonpath=["."]`.
- `harness/requirements.txt` — pinned runtime deps for the Dockerfile `pip install`.
- `harness/tests/conftest.py` — a `stub_claude` fixture: writes an executable `claude` script that echoes recorded stream-json fixtures, returns its path for `claude_bin`.
- `harness/tests/fixtures/*.jsonl` — RECORDED Claude Code stream-json samples (assistant text, Write tool_use, Bash tool_use, result). The fixture-vs-reality seam; a drill captures a REAL sample to validate them.
- `harness/tests/test_driver.py` — `translate()` mapping table + `ClaudeDriver.run()` against the stub `claude`.
- `harness/tests/test_app.py` — `/message` SSE, `/files` GET/PUT/list, `/health`, `--continue` toggling; injected fake driver.
- `harness/tests/test_hooks.py` — `/ready` and `/validate` truth tables with injected checks.

**New — `infra/` (TypeScript CDK app, `ap-northeast-1`):**
- `infra/bin/app.ts` — CDK app entry; instantiates `PathfinderDrillStack` with `env.region="ap-northeast-1"`.
- `infra/lib/pathfinder-drill-stack.ts` — the single stack: artifacts bucket (autoDelete, DESTROY), `aws_s3_assets.Asset` of the packaged harness build dir, build role (S3 read + CW logs + confused-deputy `SourceAccount`), execution role (Bedrock-only, no S3), `CfnMicrovmImage` (al2023 base, hooks ready/validate on 9000, `EnvironmentVariables` = `BootSpec.env()` values, CW logging, ARM_64), and `CfnOutput`s (image ARN, exec-role ARN, bucket name, region).
- `infra/package-harness.sh` — stages `harness/` + `files/aiplc-rules/` into `infra/build/harness/` with the Dockerfile at root (the asset the CDK zips).
- `infra/cdk.json`, `infra/package.json`, `infra/tsconfig.json` — complete, minimal (`aws-cdk-lib`, `constructs`, `typescript`, `ts-node`).
- `infra/README.md` — one-paragraph "what/how to synth+deploy" (NO other docs created).

**New — `scripts/aws-drills/` (Phase B, bash, INTEGRATION):**
- `00-preflight.sh`, `10-smoke-turn.sh`, `20-s3-roundtrip.sh`, `30-recovery-drill.sh`, `40-reconcile-drill.sh`, `50-glob-parity.sh`, `99-teardown.sh` — each `set -euo pipefail`, EXPECTED echo blocks, nonzero exit on automatable failed expectations, outputs recorded under `scripts/aws-drills/out/`.
- `scripts/aws-drills/README.md` — env-var table + run order (NO other docs).

**Modified — backend:**
- `backend/pathfinder/sandbox/harness.py` — `HarnessClient.__init__` gains `headers: dict[str,str] | None = None`, merged into every request. Additive only.
- `backend/pathfinder/sandbox/microvm_control_aws.py` — fill the five methods with boto3 `lambda-microvms`; add module-level `mint_harness_token(vm_id, region, client=None) -> dict[str,str]`.
- `backend/pathfinder/app.py` — `harness_factory` mints the token via `mint_harness_token` and passes `headers=` into `HarnessClient`.
- `backend/pyproject.toml` — bump `boto3` floor.

**New — backend tests (CI, no AWS):**
- `backend/tests/test_harness_headers.py`, `backend/tests/test_microvm_control_aws.py`, `backend/tests/test_app_harness_factory.py`.

---

## Phase A — MicroVM Part 1 Task 7 made real

### Task 1: `HarnessClient` auth-header seam (`X-aws-proxy-auth`)

The controller-made decision (spec §4): `HarnessClient.__init__` gains an optional `headers` dict merged into every request. This is the minimal transport change — `FakeHarness`, the sandbox contract, and `HarnessLike` are untouched (the new param defaults to `None`). The factory (Task 5) supplies per-handle `X-aws-proxy-auth`. This keeps `app.py`'s single shared `httpx.AsyncClient` (headers attached per-`HarnessClient`, not on the shared client).

**Files:**
- Modify: `backend/pathfinder/sandbox/harness.py:19-63`
- Test: `backend/tests/test_harness_headers.py` (new)

**Interfaces:**
- Consumes: `HarnessClient(base_url: str, http: httpx.AsyncClient)`, `build_fake_harness_app()` (from `fakes.harness_app`), `AgentEvent`.
- Produces: `HarnessClient(base_url: str, http: httpx.AsyncClient, headers: dict[str, str] | None = None)` — `headers` merged into `/message`, `/files` GET/PUT, `/files` list, and `/health` requests. Existing 2-arg construction still works unchanged.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_harness_headers.py`:

```python
import httpx
import pytest
from pathfinder.sandbox.harness import HarnessClient
from fakes.harness_app import build_fake_harness_app


class _Recorder(httpx.AsyncBaseTransport):
    """Wraps an ASGI transport, capturing the headers of every request."""
    def __init__(self, app):
        self._inner = httpx.ASGITransport(app=app)
        self.seen: list[httpx.Headers] = []

    async def handle_async_request(self, request):
        self.seen.append(request.headers)
        return await self._inner.handle_async_request(request)


def _client_with_recorder(app):
    rec = _Recorder(app)
    return httpx.AsyncClient(transport=rec, base_url="http://vm"), rec


async def test_auth_header_attached_to_message_stream():
    app = build_fake_harness_app([
        {"kind": "message", "text": "hi", "path": None},
        {"kind": "done", "text": None, "path": None},
    ])
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http,
                           headers={"X-aws-proxy-auth": "tok-123"})
        _ = [e async for e in hc.send_message("go")]
    assert rec.seen, "no request captured"
    assert rec.seen[0]["X-aws-proxy-auth"] == "tok-123"


async def test_auth_header_attached_to_file_and_health_ops():
    app = build_fake_harness_app()
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http,
                           headers={"X-aws-proxy-auth": "tok-xyz"})
        await hc.write_file("aiplc-docs/a.md", "x")
        await hc.read_file("aiplc-docs/a.md")
        await hc.list_files("aiplc-docs/*")
        await hc.heartbeat()
    assert len(rec.seen) == 4
    for h in rec.seen:
        assert h["X-aws-proxy-auth"] == "tok-xyz"


async def test_no_headers_arg_still_works_and_sends_none():
    app = build_fake_harness_app()
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http)  # 2-arg, unchanged
        await hc.heartbeat()
    assert "X-aws-proxy-auth" not in rec.seen[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_harness_headers.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'headers'`.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/pathfinder/sandbox/harness.py` — add the `headers` param and merge it into every request. Replace the `__init__` and the request bodies:

```python
    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        headers: dict[str, str] | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._http = http
        # Per-handle auth (e.g. X-aws-proxy-auth JWE), merged into every
        # request. app.py keeps ONE shared AsyncClient; auth is attached
        # per HarnessClient, not on the shared client.
        self._headers = headers or None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        async with self._http.stream(
            "POST", f"{self._base}/message", json={"text": text},
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

    async def read_file(self, rel_path: str) -> str:
        resp = await self._http.get(
            f"{self._base}/files/{rel_path}", headers=self._headers
        )
        if resp.status_code == 404:
            raise FileNotFoundError(rel_path)
        resp.raise_for_status()
        return resp.text

    async def write_file(self, rel_path: str, content: str) -> None:
        resp = await self._http.put(
            f"{self._base}/files/{rel_path}",
            content=content.encode("utf-8"),
            headers=self._headers,
        )
        resp.raise_for_status()

    async def list_files(self, glob: str) -> list[str]:
        resp = await self._http.get(
            f"{self._base}/files", params={"glob": glob}, headers=self._headers
        )
        resp.raise_for_status()
        return list(resp.json())

    async def heartbeat(self) -> bool:
        try:
            resp = await self._http.get(
                f"{self._base}/health", headers=self._headers
            )
        except httpx.HTTPError:
            return False
        return resp.is_success
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_harness_headers.py tests/test_harness_client.py -v`
Expected: PASS — the 3 new tests plus all pre-existing `test_harness_client.py` tests (the 2-arg path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/harness.py backend/tests/test_harness_headers.py
git commit -m "feat(harness): optional per-request headers on HarnessClient (X-aws-proxy-auth seam)"
```

---

### Task 2: Harness `claude_driver` — stream-json → AgentEvent

`harness/claude_driver.py` is the in-VM subprocess driver. A pure `translate()` function maps one Claude Code `--output-format stream-json` object to a LIST of `AgentEvent`s — zero, one, or several, in block order (real assistant messages carry multiple content blocks together; a first-match-return design silently drops the rest) — unit-testable against RECORDED fixtures (the fixture-vs-reality seam a drill later validates). `ClaudeDriver.run()` spawns the CLI and yields events, draining stderr concurrently so the child never deadlocks on a full pipe, and killing/reaping the subprocess on early generator abandonment. `AgentEvent` shape must match `backend/pathfinder/sandbox/base.py` exactly: `kind ∈ {message,file_changed,status,done,error}`, `text`, `path`. The harness re-declares a local `AgentEvent` (it cannot import the backend package) as a Pydantic model with the identical fields.

> **Reviewer-mandated hardening (folded in before this task's first implementation, since Task 3 consumes this module and a signature change is cheapest before any caller exists):**
> 1. **Path-escape guard.** `PurePosixPath.relative_to` does not normalize `..` segments — relativizing `/workspace/../etc/passwd` against `/workspace` yields the syntactically-relative-but-escaping `"../etc/passwd"`, which a naive implementation forwards straight into `file_changed.path`. `_rel()` now rejects any relativized result containing a `..` segment or still starting with `/`, returning `None`; the caller (`translate()`) turns a `None` into `AgentEvent(kind="status", text="file outside workspace ignored")` — no path echo, ever, for output outside the workspace.
> 2. **Multi-block translation.** `translate()`'s signature changes to `translate(obj: dict, workspace: str) -> list[AgentEvent]` (dropping the `| None` single-event return) so every content block in an assistant message is translated, not just the first that matches. `ClaudeDriver.run()` changes its inner loop to `for ev in translate(...): yield ev`.
> 3. **stderr deadlock.** `stderr=PIPE` is drained by a concurrent background task for the lifetime of the subprocess (discarded, capped-read loop) — otherwise a child that fills the OS pipe buffer on stderr blocks there and never produces the stdout stream-json the driver is reading. Only the bounded, credential-free `"claude exited N"` string (built from the exit code alone) ever reaches an `AgentEvent`; raw stderr bytes are never echoed.
> 4. **Subprocess cleanup on abandonment.** The subprocess-handling body of `run()` is wrapped in `try/finally`; the `finally` kills (`proc.kill()`) and reaps (`await proc.wait()`) the child if it's still running, and cancels the stderr-drain task — covering normal completion, an unparseable-line early return, AND the generator being closed/cancelled mid-turn by a caller that stops consuming early.
> 5. **`/workspace` CI dependency.** The test constant `WS = "/workspace"` stays (plan-mandated, matches the real MicroVM mount) — but `harness/tests/conftest.py` gains an autouse fixture that `os.makedirs("/workspace", exist_ok=True)`s before every test (skipping cleanly, not erroring, if permission is denied), so the suite doesn't require a pre-provisioned `/workspace` outside the MicroVM image.

**Files:**
- Create: `harness/pyproject.toml`, `harness/__init__.py` (empty), `harness/claude_driver.py`
- Create: `harness/tests/conftest.py`, `harness/tests/fixtures/basic_turn.jsonl`, `harness/tests/fixtures/multi_block_turn.jsonl`
- Test: `harness/tests/test_driver.py`

**Interfaces:**
- Consumes: nothing from the backend (separate package). Claude Code CLI stream-json line schema (assistant/tool_use/result objects).
- Produces:
  - `AgentEvent(BaseModel)` with `kind: Literal["message","file_changed","status","done","error"]`, `text: str | None = None`, `path: str | None = None`.
  - `translate(obj: dict, workspace: str) -> list[AgentEvent]` — pure; `[]` for objects that produce no event (e.g. `system`/`user` framing); a single assistant `message` object can yield MULTIPLE events (one per content block, in order).
  - `class ClaudeDriver:` `__init__(self, workspace: str, claude_bin: str = "claude")`; `async def run(self, text: str, *, continue_session: bool) -> AsyncIterator[AgentEvent]`.

**Mapping (spec §1), asserted in tests:**
- `{"type":"assistant","message":{"content":[{"type":"text","text":T}]}}` → `[AgentEvent(kind="message", text=T)]`.
- assistant content `{"type":"tool_use","name":N,"input":{"file_path":P,...}}` where `N ∈ {Write,Edit,MultiEdit}` and `P` resolves INSIDE `workspace` → `AgentEvent(kind="file_changed", path=<P made workspace-relative>)`; where `P` would resolve OUTSIDE `workspace` (e.g. a `..` segment, or an absolute path elsewhere) → `AgentEvent(kind="status", text="file outside workspace ignored")` (no path echo).
- any other `tool_use` (name `N`) → `AgentEvent(kind="status", text=N)`.
- a `content` list with multiple blocks (text + tool_use, or several tool_use in parallel) → one `AgentEvent` per block, IN ORDER, all in the same returned list.
- `{"type":"result",...}` → `[AgentEvent(kind="done")]`.
- objects that carry neither text nor a recognized tool → `[]`.

- [ ] **Step 1: Write the fixture + conftest**

Create `harness/tests/fixtures/basic_turn.jsonl` (one JSON object per line — a recorded-shape sample):

```json
{"type":"system","subtype":"init","session_id":"s1"}
{"type":"assistant","message":{"content":[{"type":"text","text":"시작합니다"}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"/workspace/aiplc-docs/audit.md","content":"x"}}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}
{"type":"result","subtype":"success","is_error":false,"result":"ok"}
```

Create `harness/tests/fixtures/multi_block_turn.jsonl` (recorded-shape sample where a single assistant message carries multiple content blocks together — the shape a first-match-return `translate()` would truncate):

```json
{"type":"system","subtype":"init","session_id":"s2"}
{"type":"assistant","message":{"content":[{"type":"text","text":"작성 중"},{"type":"tool_use","name":"Write","input":{"file_path":"/workspace/aiplc-docs/notes.md","content":"y"}}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}},{"type":"tool_use","name":"Bash","input":{"command":"pwd"}}]}}
{"type":"result","subtype":"success","is_error":false,"result":"ok"}
```

Create `harness/tests/conftest.py`:

```python
import os
import stat
import textwrap
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
WORKSPACE = "/workspace"


@pytest.fixture(autouse=True)
def _ensure_workspace_dir():
    """`ClaudeDriver.run()` spawns the subprocess with cwd=workspace, and the
    test suite hardcodes WS = "/workspace" (the real MicroVM mount path,
    plan-mandated — kept as-is). Outside the MicroVM image this directory
    doesn't exist by default (e.g. a bare CI runner), which would otherwise
    fail every `run()` test with FileNotFoundError before the test body even
    starts. Create it so `cwd=` is valid; skip cleanly (rather than error) if
    we can't (e.g. read-only root)."""
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
    except PermissionError as exc:
        pytest.skip(f"cannot create {WORKSPACE}: {exc}")


@pytest.fixture
def stub_claude(tmp_path):
    """Write an executable `claude` that ignores its args and prints the named
    jsonl fixture line-by-line, then exits per `exit_code`. Optionally writes
    `stderr_bytes` of filler to stderr first (to exercise stderr-pipe
    draining without deadlock). Returns a builder."""
    def _make(fixture: str = "basic_turn.jsonl", exit_code: int = 0, stderr_bytes: int = 0) -> str:
        payload = (FIXTURES / fixture).read_text() if fixture else ""
        script = tmp_path / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys
            if {stderr_bytes}:
                sys.stderr.write("E" * {stderr_bytes})
                sys.stderr.flush()
            sys.stdout.write({payload!r})
            sys.exit({exit_code})
        """))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)
    return _make


@pytest.fixture
def hanging_stub_claude(tmp_path):
    """Write an executable `claude` that emits one stream-json line, then
    blocks indefinitely (simulating a long-running turn) instead of exiting.
    Lets a test abandon the driver mid-turn and assert the subprocess is
    actually killed and reaped, not left running."""
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, time
        sys.stdout.write('{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}\\n')
        sys.stdout.flush()
        time.sleep(60)
        sys.stdout.write('{"type":"result","subtype":"success"}\\n')
        sys.exit(0)
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)
```

- [ ] **Step 2: Write the failing test**

Create `harness/tests/test_driver.py`:

```python
import asyncio
import pytest
from claude_driver import AgentEvent, translate, ClaudeDriver

WS = "/workspace"


def test_translate_assistant_text_to_message():
    obj = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert translate(obj, WS) == [AgentEvent(kind="message", text="hi")]


def test_translate_write_tool_to_file_changed_relative():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/audit.md", "content": "x"}}]}}
    assert translate(obj, WS) == [AgentEvent(kind="file_changed", path="aiplc-docs/audit.md")]


def test_translate_other_tool_to_status_with_name():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}
    assert translate(obj, WS) == [AgentEvent(kind="status", text="Bash")]


def test_translate_result_to_done():
    assert translate({"type": "result", "subtype": "success"}, WS) == [AgentEvent(kind="done")]


def test_translate_system_framing_is_none():
    assert translate({"type": "system", "subtype": "init"}, WS) == []


def test_translate_write_tool_absolute_traversal_escapes_workspace():
    # /workspace/../etc/passwd: PurePosixPath.relative_to doesn't normalize,
    # so a naive relativize would yield "../etc/passwd" — an escape. Must be
    # rejected: no file_changed, no path echo.
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/../etc/passwd", "content": "x"}}]}}
    events = translate(obj, WS)
    assert events == [AgentEvent(kind="status", text="file outside workspace ignored")]
    assert events[0].path is None


def test_translate_write_tool_embedded_dotdot_escapes_workspace():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/../../etc/passwd", "content": "x"}}]}}
    events = translate(obj, WS)
    assert events == [AgentEvent(kind="status", text="file outside workspace ignored")]
    assert events[0].path is None


def test_translate_text_and_tool_use_in_one_message_both_emitted():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "작성 중"},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/notes.md", "content": "y"}}]}}
    events = translate(obj, WS)
    assert events == [
        AgentEvent(kind="message", text="작성 중"),
        AgentEvent(kind="file_changed", path="aiplc-docs/notes.md"),
    ]


def test_translate_parallel_tool_use_blocks_both_emitted():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}}]}}
    events = translate(obj, WS)
    assert events == [
        AgentEvent(kind="status", text="Bash"),
        AgentEvent(kind="status", text="Bash"),
    ]


async def test_run_yields_events_ending_in_done(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("basic_turn.jsonl"))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert [e.kind for e in events] == ["message", "file_changed", "status", "done"]
    assert events[1].path == "aiplc-docs/audit.md"


async def test_run_translates_all_blocks_of_multi_block_fixture(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("multi_block_turn.jsonl"))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert [e.kind for e in events] == ["message", "file_changed", "status", "status", "done"]
    assert events[1].path == "aiplc-docs/notes.md"


async def test_run_nonzero_exit_yields_error(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("", exit_code=3))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert events[-1].kind == "error"


async def test_run_passes_continue_flag(stub_claude, monkeypatch):
    captured = {}
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude())

    orig = ClaudeDriver._argv
    def spy(self, text, continue_session):
        argv = orig(self, text, continue_session)
        captured["argv"] = argv
        return argv
    monkeypatch.setattr(ClaudeDriver, "_argv", spy)

    _ = [e async for e in driver.run("go", continue_session=True)]
    assert "--continue" in captured["argv"]
    _ = [e async for e in driver.run("go", continue_session=False)]
    assert "--continue" not in captured["argv"]


async def test_run_large_stderr_does_not_deadlock(stub_claude):
    # >64KB (a typical OS pipe buffer size) written to stderr before any
    # stdout: if stderr isn't drained concurrently, the child blocks on the
    # stderr write syscall and stdout (and therefore run()) never completes.
    driver = ClaudeDriver(
        workspace=WS,
        claude_bin=stub_claude("basic_turn.jsonl", stderr_bytes=70_000),
    )
    events = await asyncio.wait_for(
        _collect(driver.run("go", continue_session=False)), timeout=10
    )
    assert [e.kind for e in events] == ["message", "file_changed", "status", "done"]
    # None of the discarded stderr filler leaks into any event's text.
    assert all(e.text is None or "E" * 100 not in e.text for e in events)


async def _collect(aiter):
    return [e async for e in aiter]


async def test_run_abandoned_generator_kills_and_reaps_subprocess(
    hanging_stub_claude, monkeypatch
):
    # Reliable technique: intercept asyncio.create_subprocess_exec to capture
    # the real asyncio.subprocess.Process the driver spawns, so the test can
    # assert on it directly rather than guessing at OS-level process
    # liveness. The stub script prints one line then sleeps for 60s instead
    # of exiting -- if run()'s cleanup didn't kill it, awaiting proc.wait()
    # here would hang for the full 60s (caught by the outer wait_for).
    captured = {}
    orig_create = asyncio.create_subprocess_exec

    async def spy_create(*args, **kwargs):
        proc = await orig_create(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_create)

    driver = ClaudeDriver(workspace=WS, claude_bin=hanging_stub_claude)
    gen = driver.run("go", continue_session=False)
    first = await gen.__anext__()
    assert first.kind == "message"

    await gen.aclose()

    proc = captured["proc"]
    # If cleanup killed+reaped it, this returns immediately with a non-None
    # (killed) returncode instead of hanging until the stub's 60s sleep ends.
    await asyncio.wait_for(proc.wait(), timeout=5)
    assert proc.returncode is not None
    assert proc.returncode != 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd harness && python -m pytest tests/test_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_driver'`.

- [ ] **Step 4: Write minimal implementation**

Create `harness/pyproject.toml`:

```toml
[project]
name = "pathfinder-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["starlette>=0.37", "sse-starlette>=2.0", "httpx>=0.27", "uvicorn>=0.30", "pydantic>=2.6"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

Create `harness/__init__.py` (empty). Create `harness/claude_driver.py`:

```python
# harness/claude_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import contextlib
import json
from pathlib import PurePosixPath
from typing import AsyncIterator, Literal
from pydantic import BaseModel

# Mirror of backend/pathfinder/sandbox/base.py AgentEvent. The harness is a
# separate deployable and cannot import the backend package; these fields MUST
# stay identical to the backend model (kind/text/path) or the SSE contract breaks.
class AgentEvent(BaseModel):
    kind: Literal["message", "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_STDERR_CHUNK = 65536


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; leave already-relative
    paths untouched. Returns None if the result would escape the workspace.

    `PurePosixPath.relative_to` does NOT normalize `..` segments: relativizing
    "/workspace/../etc/passwd" against "/workspace" yields "../etc/passwd" —
    syntactically "relative" but still an escape once a caller joins it back
    onto the workspace root. Any relativized result containing a `..`
    segment, or that is still absolute, is therefore rejected as an escape
    (None) rather than forwarded as a path.
    """
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


def translate(obj: dict, workspace: str) -> list[AgentEvent]:
    """Map one Claude Code stream-json object to zero or more AgentEvents,
    in block order. Real assistant messages can carry several content
    blocks together (e.g. text + tool_use, or multiple parallel tool_use
    blocks) — every block must be translated, not just the first."""
    typ = obj.get("type")
    if typ == "result":
        return [AgentEvent(kind="done")]
    events: list[AgentEvent] = []
    if typ == "assistant":
        for block in obj.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                events.append(AgentEvent(kind="message", text=block.get("text")))
            elif btype == "tool_use":
                name = block.get("name", "")
                if name in _FILE_TOOLS:
                    fp = block.get("input", {}).get("file_path", "")
                    rel = _rel(fp, workspace)
                    if rel is None:
                        events.append(AgentEvent(
                            kind="status", text="file outside workspace ignored"))
                    else:
                        events.append(AgentEvent(kind="file_changed", path=rel))
                else:
                    events.append(AgentEvent(kind="status", text=name))
    return events


async def _drain_stderr(stream: asyncio.StreamReader) -> None:
    """Continuously read and discard the child's stderr. If nobody reads a
    subprocess's stderr pipe, the OS pipe buffer fills and the child blocks
    on its next stderr write — which, for a CLI that writes stderr before or
    interleaved with stdout, silently deadlocks stdout production too. We
    intentionally never surface this content in an AgentEvent: the only
    thing that reaches the event stream is a bounded, credential-free
    "claude exited N" message built from the exit code alone."""
    while True:
        chunk = await stream.read(_STDERR_CHUNK)
        if not chunk:
            return


class ClaudeDriver:
    """Spawns the Claude Code CLI and yields AgentEvents. First turn falls back
    to a new session (no --continue); subsequent turns pass --continue."""

    def __init__(self, workspace: str, claude_bin: str = "claude"):
        self._workspace = workspace
        self._claude = claude_bin

    def _argv(self, text: str, continue_session: bool) -> list[str]:
        argv = [self._claude, "-p", text,
                "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions"]
        if continue_session:
            argv.append("--continue")
        return argv

    async def run(self, text: str, *, continue_session: bool) -> AsyncIterator[AgentEvent]:
        proc = await asyncio.create_subprocess_exec(
            *self._argv(text, continue_session),
            cwd=self._workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        # Drain stderr concurrently so the child never blocks on a full pipe
        # buffer (see _drain_stderr docstring). Its content is discarded —
        # only the exit code, never raw stderr bytes, reaches an AgentEvent.
        stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
        try:
            saw_done = False
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    yield AgentEvent(kind="error", text="unparseable stream-json line")
                    return
                for ev in translate(obj, self._workspace):
                    if ev.kind == "done":
                        saw_done = True
                    yield ev
            rc = await proc.wait()
            if rc != 0:
                yield AgentEvent(kind="error", text=f"claude exited {rc}")
            elif not saw_done:
                yield AgentEvent(kind="done")
        finally:
            # Reached on normal completion, on an unparseable-line early
            # return, AND on the generator being closed/cancelled mid-turn
            # (e.g. a caller stops iterating after the first event). In the
            # latter case the subprocess would otherwise keep running
            # unsupervised; kill it and reap it so no orphan `claude`
            # process survives the driver call.
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd harness && python -m pytest tests/test_driver.py -v`
Expected: PASS — all 15 tests green (5 original translate cases + 2 escape-guard cases + 2 multi-block cases + 6 `ClaudeDriver.run()` cases, including the stderr-deadlock and abandoned-generator-cleanup regression tests added per reviewer directive).

- [ ] **Step 6: Commit**

```bash
git add harness/pyproject.toml harness/__init__.py harness/claude_driver.py \
        harness/tests/conftest.py harness/tests/test_driver.py \
        harness/tests/fixtures/basic_turn.jsonl harness/tests/fixtures/multi_block_turn.jsonl
git commit -m "fix(harness): path-escape guard, multi-block translate, subprocess hygiene, CI workspace fixture"
```

---

### Task 3: Harness servers — `app.py` (8080) + `hooks.py` (9000) + `serve.py`

The in-VM HTTP surface. `app.py` must speak the EXACT protocol `HarnessClient` consumes and `backend/tests/fakes/harness_app.py` fakes: POST `/message` → SSE of `AgentEvent` JSON with a terminal `done`/`error`; GET/PUT `/files/{path}` (workspace-relative, 404 on missing read); GET `/files?glob=`; GET `/health`. It owns turn continuity: the first turn calls the driver with `continue_session=False`, every later turn `True`. `hooks.py` serves the image lifecycle on 9000. `serve.py` runs both.

**Files:**
- Create: `harness/app.py`, `harness/hooks.py`, `harness/serve.py`, `harness/requirements.txt`
- Test: `harness/tests/test_app.py`, `harness/tests/test_hooks.py`

**Interfaces:**
- Consumes: `AgentEvent`, `ClaudeDriver` (Task 2). A "driver" here is any object with `run(text, *, continue_session) -> AsyncIterator[AgentEvent]` (tests inject a fake). Note: Task 2's `translate(obj, workspace) -> list[AgentEvent]` (not a single `AgentEvent | None` — reviewer-mandated fix, folded in before this task started) is an internal-to-`ClaudeDriver.run()` implementation detail; `app.py` only ever consumes `run()`'s already-flattened `AsyncIterator[AgentEvent]` and never calls `translate()` directly, so this signature change does not affect `build_app`'s contract with the fake/real driver.
- Produces:
  - `build_app(driver, workspace: str) -> Starlette` — routes `/message`(POST), `/files`(GET list), `/files/{path:path}`(GET/PUT), `/health`(GET). Tracks a `_turn_seen` flag to pass `continue_session`.
  - `build_hooks_app(*, version_check: Callable[[], bool], rules_present: Callable[[], bool], health_check: Callable[[], bool]) -> Starlette` — `/ready`(GET), `/validate`(GET).
  - `serve.py`: `async def main()` running both `uvicorn.Server`s via `asyncio.gather`.

- [ ] **Step 1: Write the failing test for the app**

Create `harness/tests/test_app.py`:

```python
import httpx
import pytest
from app import build_app
from claude_driver import AgentEvent


class FakeDriver:
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls: list[bool] = []
        self.files: dict[str, str] = {}

    async def run(self, text, *, continue_session):
        self.calls.append(continue_session)
        yield AgentEvent(kind="message", text=f"echo:{text}")
        yield AgentEvent(kind="done")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")


async def test_message_streams_sse_events():
    drv = FakeDriver("/workspace")
    async with _client(build_app(drv, "/workspace")) as http:
        lines = []
        async with http.stream("POST", "/message", json={"text": "go"}) as r:
            assert r.status_code == 200
            async for ln in r.aiter_lines():
                if ln.startswith("data:"):
                    lines.append(ln)
    assert any('"kind": "message"' in l or '"kind":"message"' in l for l in lines)
    assert any('"done"' in l for l in lines)


async def test_first_turn_no_continue_then_continue():
    drv = FakeDriver("/workspace")
    async with _client(build_app(drv, "/workspace")) as http:
        for _ in range(2):
            async with http.stream("POST", "/message", json={"text": "go"}) as r:
                async for _ln in r.aiter_lines():
                    pass
    assert drv.calls == [False, True]


async def test_files_put_get_roundtrip_and_404(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        assert (await http.get("/files/aiplc-docs/missing.md")).status_code == 404
        assert (await http.put("/files/aiplc-docs/a.md", content=b"hello")).status_code in (200, 204)
        got = await http.get("/files/aiplc-docs/a.md")
        assert got.status_code == 200 and got.text == "hello"


async def test_files_list_glob(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        await http.put("/files/aiplc-docs/a-questions.md", content=b"x")
        await http.put("/files/aiplc-docs/audit.md", content=b"y")
        r = await http.get("/files", params={"glob": "aiplc-docs/*-questions.md"})
    assert r.json() == ["aiplc-docs/a-questions.md"]


async def test_health_ok(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd harness && python -m pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Write minimal implementation of the app**

Create `harness/app.py`:

```python
# harness/app.py  (port 8080 inside the MicroVM)
from __future__ import annotations
import fnmatch
import json
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse


def build_app(driver, workspace: str) -> Starlette:
    ws = Path(workspace)
    state = {"turn_seen": False}

    async def message(request):
        body = await request.json()
        text = body["text"]
        continue_session = state["turn_seen"]
        state["turn_seen"] = True

        async def gen():
            async for ev in driver.run(text, continue_session=continue_session):
                yield {"data": ev.model_dump_json()}
        return EventSourceResponse(gen())

    def _resolve(rel: str) -> Path:
        # Trust the caller for path-safety (MicroVMSandbox rejects unsafe paths
        # before it ever reaches the harness); still confine under workspace.
        return ws / rel

    async def get_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        if not p.is_file():
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(p.read_text("utf-8"))

    async def put_file(request):
        rel = request.path_params["path"]
        p = _resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(await request.body())
        return Response(status_code=204)

    async def list_files(request):
        glob = request.query_params.get("glob", "*")
        out = []
        for f in ws.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ws).as_posix()
                if fnmatch.fnmatch(rel, glob):
                    out.append(rel)
        return JSONResponse(sorted(out))

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

> **Note — glob semantics vs the fake.** The fake uses flat `fnmatch` over an in-memory dict; the real app walks the workspace tree and `fnmatch`-matches POSIX-relative keys. `fnmatch` treats `*` as matching `/` too, so `aiplc-docs/**/*` and `aiplc-docs/*-questions.md` both behave. Drill `50-glob-parity.sh` proves this matches `backend/.../globmatch.matches_glob` expectations on real nested + top-level files (the honest fixture-vs-reality check for globbing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd harness && python -m pytest tests/test_app.py -v`
Expected: PASS — all 5 tests green.

- [ ] **Step 5: Write the failing test for hooks**

Create `harness/tests/test_hooks.py`:

```python
import httpx
from hooks import build_hooks_app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")


async def test_ready_200_when_version_ok():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/ready")).status_code == 200


async def test_ready_503_when_version_fails():
    app = build_hooks_app(version_check=lambda: False,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/ready")).status_code == 503


async def test_validate_200_when_health_and_rules_ok():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: True, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/validate")).status_code == 200


async def test_validate_503_when_rules_missing():
    app = build_hooks_app(version_check=lambda: True,
                          rules_present=lambda: False, health_check=lambda: True)
    async with _client(app) as http:
        assert (await http.get("/validate")).status_code == 503
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd harness && python -m pytest tests/test_hooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hooks'`.

- [ ] **Step 7: Write hooks + serve implementation**

Create `harness/hooks.py`:

```python
# harness/hooks.py  (port 9000 — image build/resume lifecycle)
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

WORKSPACE = "/workspace"


def default_version_check() -> bool:
    exe = shutil.which("claude")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--version"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def default_rules_present() -> bool:
    core = Path(WORKSPACE) / "aiplc-rules" / "aws-aiplc-rules" / "core-workflow.md"
    return core.is_file()


def build_hooks_app(*, version_check: Callable[[], bool],
                    rules_present: Callable[[], bool],
                    health_check: Callable[[], bool]) -> Starlette:
    async def ready(request):
        # Build-time snapshot gate: 200 only once the app process is up and the
        # Claude Code CLI is installed & runnable. The platform snapshots on 200.
        ok = health_check() and version_check()
        return PlainTextResponse("ready" if ok else "not-ready", status_code=200 if ok else 503)

    async def validate(request):
        # Resume-from-snapshot gate. Tradeoff: a real `claude -p` smoke turn
        # would be the strongest signal but is slow + costs a Bedrock call on
        # EVERY resume, so instead we cheaply re-confirm the server is healthy
        # and the baked rules are present. The platform samples pages to
        # prefetch after 200.
        ok = health_check() and rules_present()
        return PlainTextResponse("valid" if ok else "invalid", status_code=200 if ok else 503)

    return Starlette(routes=[
        Route("/ready", ready, methods=["GET"]),
        Route("/validate", validate, methods=["GET"]),
    ])
```

Create `harness/serve.py`:

```python
# harness/serve.py  — container CMD: run both servers.
from __future__ import annotations
import asyncio
import httpx
import uvicorn
from app import build_app
from hooks import build_hooks_app, default_version_check, default_rules_present
from claude_driver import ClaudeDriver

WORKSPACE = "/workspace"


def _health_check() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8080/health", timeout=2).is_success
    except httpx.HTTPError:
        return False


async def main() -> None:
    driver = ClaudeDriver(workspace=WORKSPACE)
    app = build_app(driver, WORKSPACE)
    hooks = build_hooks_app(
        version_check=default_version_check,
        rules_present=default_rules_present,
        health_check=_health_check,
    )
    app_server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info"))
    hooks_server = uvicorn.Server(uvicorn.Config(hooks, host="0.0.0.0", port=9000, log_level="info"))
    await asyncio.gather(app_server.serve(), hooks_server.serve())


if __name__ == "__main__":
    asyncio.run(main())
```

Create `harness/requirements.txt`:

```
starlette>=0.37
sse-starlette>=2.0
httpx>=0.27
uvicorn>=0.30
pydantic>=2.6
```

- [ ] **Step 8: Run all harness tests to verify they pass**

Run: `cd harness && python -m pytest -v`
Expected: PASS — `test_driver.py`, `test_app.py`, `test_hooks.py` all green (16 tests).

- [ ] **Step 9: Commit**

```bash
git add harness/app.py harness/hooks.py harness/serve.py harness/requirements.txt \
        harness/tests/test_app.py harness/tests/test_hooks.py
git commit -m "feat(harness): Starlette /message+/files+/health app, /ready+/validate hooks, dual-server entrypoint"
```

---

### Task 4: `LambdaMicroVMController` boto3 binding + `mint_harness_token`

Fill the five `NotImplementedError` methods with a boto3 `lambda-microvms` client (injected for tests, default constructed). `boot()` calls `run_microvm` then POLLS `get_microvm` until `RUNNING` before returning a `VMHandle(status="ready")` — this is the resolution of the Part-2 "booting falls-through `_ensure_ready`" checklist item: cached handles are only ever created ready, so `_ensure_ready` never sees `"booting"` on a handle it just booted. `status()` maps `get_microvm` states per the table. Also add `mint_harness_token()` for the JWE header (used by Task 5). Sync boto3 calls wrapped in `asyncio.to_thread`, exactly like `S3Store`.

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm_control_aws.py` (whole file)
- Modify: `backend/pyproject.toml:5` (boto3 floor bump)
- Test: `backend/tests/test_microvm_control_aws.py` (new)

**Interfaces:**
- Consumes: `MicroVMController`, `BootSpec` (`.idle_policy()`, `image_id`, `exec_role_arn`), `VMHandle`, `VMStatus`.
- Produces:
  - `LambdaMicroVMController(region: str = "ap-northeast-1", client=None, boot_timeout_seconds: float = 120.0, poll_interval_seconds: float = 3.0)`; `client` defaults to `boto3.client("lambda-microvms", region_name=region)` (lazy, so tests inject a Stubbed client).
  - `boot/resume/suspend/stop/status` per the ABC.
  - `_map_status(raw: str) -> VMStatus` (module-level or static): PENDING/STARTING→"booting", RUNNING→"ready", SUSPENDED→"suspended", TERMINATED/EXPIRED→"expired", else→"stopped".
  - `mint_harness_token(vm_id: str, region: str = "ap-northeast-1", client=None, port: int = 8080, minutes: int = 30) -> dict[str, str]` — returns `{"X-aws-proxy-auth": <token>}`.

- [ ] **Step 1: Bump the boto3 floor (the `lambda-microvms` model must exist)**

Edit `backend/pyproject.toml` line 5:

```toml
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.40"]
```

Then install and confirm the service model is present:

```bash
cd backend && pip install -e '.[dev]'
python -c "import boto3; print('lambda-microvms' in boto3.session.Session().get_available_services())"
```
Expected: `True`. If `False`, the installed botocore predates the GA `lambda-microvms` model — raise the floor until it prints `True` and record the exact version (this closes the boto3-floor Open Question). Do NOT proceed to Step 3 until this prints `True`, or the Stubber tests cannot construct the client.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_microvm_control_aws.py`:

```python
import boto3
import pytest
from botocore.stub import Stubber, ANY
from pathfinder.sandbox.microvm_control import BootSpec
from pathfinder.sandbox.microvm_control_aws import (
    LambdaMicroVMController, mint_harness_token, _map_status,
)

REGION = "ap-northeast-1"


def _client():
    return boto3.client("lambda-microvms", region_name=REGION)


def test_map_status_table():
    assert _map_status("PENDING") == "booting"
    assert _map_status("STARTING") == "booting"
    assert _map_status("RUNNING") == "ready"
    assert _map_status("SUSPENDED") == "suspended"
    assert _map_status("TERMINATED") == "expired"
    assert _map_status("EXPIRED") == "expired"
    assert _map_status("WAT") == "stopped"


async def test_boot_sends_spec_params_and_polls_until_running():
    client = _client()
    stub = Stubber(client)
    spec = BootSpec(image_id="img-arn", exec_role_arn="role-arn",
                    anthropic_model="global.anthropic.claude-sonnet-5")
    stub.add_response(
        "run_microvm",
        {"microvmId": "vm-1", "endpoint": "https://vm-1.microvm.aws"},
        {"imageIdentifier": "img-arn", "executionRoleArn": "role-arn",
         "idlePolicy": {"maxIdleDurationSeconds": 300,
                        "suspendedDurationSeconds": 1800,
                        "autoResumeEnabled": True}},
    )
    stub.add_response("get_microvm", {"microvm": {"status": "PENDING"}},
                      {"microvmIdentifier": "vm-1"})
    stub.add_response("get_microvm", {"microvm": {"status": "RUNNING"}},
                      {"microvmIdentifier": "vm-1"})
    ctrl = LambdaMicroVMController(region=REGION, client=client, poll_interval_seconds=0)
    with stub:
        handle = await ctrl.boot("proj-1", spec)
    assert handle.vm_id == "vm-1"
    assert handle.base_url == "https://vm-1.microvm.aws"
    assert handle.status == "ready"
    stub.assert_no_pending_responses()


async def test_boot_times_out_raises_runtimeerror():
    client = _client()
    stub = Stubber(client)
    spec = BootSpec(image_id="img", exec_role_arn="role")
    stub.add_response("run_microvm", {"microvmId": "vm-2", "endpoint": "https://x"},
                      {"imageIdentifier": "img", "executionRoleArn": "role",
                       "idlePolicy": ANY})
    # Every poll returns PENDING; with a 0s timeout the first check fails fast.
    stub.add_response("get_microvm", {"microvm": {"status": "PENDING"}},
                      {"microvmIdentifier": "vm-2"})
    ctrl = LambdaMicroVMController(region=REGION, client=client,
                                   boot_timeout_seconds=0, poll_interval_seconds=0)
    with stub:
        with pytest.raises(RuntimeError, match="did not reach RUNNING"):
            await ctrl.boot("proj-1", spec)


async def test_status_maps_get_microvm():
    client = _client()
    stub = Stubber(client)
    stub.add_response("get_microvm", {"microvm": {"status": "SUSPENDED"}},
                      {"microvmIdentifier": "vm-9"})
    ctrl = LambdaMicroVMController(region=REGION, client=client)
    from pathfinder.sandbox.microvm_control import VMHandle
    with stub:
        assert await ctrl.status(VMHandle(vm_id="vm-9", base_url="u", status="ready")) == "suspended"


async def test_suspend_and_stop_call_api():
    client = _client()
    stub = Stubber(client)
    stub.add_response("suspend_microvm", {}, {"microvmIdentifier": "vm-3"})
    stub.add_response("terminate_microvm", {}, {"microvmIdentifier": "vm-3"})
    ctrl = LambdaMicroVMController(region=REGION, client=client)
    from pathfinder.sandbox.microvm_control import VMHandle
    h = VMHandle(vm_id="vm-3", base_url="u", status="ready")
    with stub:
        await ctrl.suspend(h)
        await ctrl.stop(h)
    stub.assert_no_pending_responses()


async def test_resume_polls_until_running():
    client = _client()
    stub = Stubber(client)
    stub.add_response("resume_microvm", {"endpoint": "https://vm-4.new"},
                      {"microvmIdentifier": "vm-4"})
    stub.add_response("get_microvm", {"microvm": {"status": "RUNNING"}},
                      {"microvmIdentifier": "vm-4"})
    ctrl = LambdaMicroVMController(region=REGION, client=client, poll_interval_seconds=0)
    from pathfinder.sandbox.microvm_control import VMHandle
    with stub:
        h = await ctrl.resume(VMHandle(vm_id="vm-4", base_url="old", status="suspended"))
    assert h.status == "ready" and h.base_url == "https://vm-4.new"


async def test_mint_harness_token_returns_proxy_auth_header():
    client = _client()
    stub = Stubber(client)
    stub.add_response("create_microvm_auth_token", {"token": "jwe-abc"},
                      {"microvmIdentifier": "vm-5", "expirationInMinutes": 30,
                       "allowedPorts": [{"port": 8080}]})
    with stub:
        hdr = mint_harness_token("vm-5", region=REGION, client=client)
    assert hdr == {"X-aws-proxy-auth": "jwe-abc"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_microvm_control_aws.py -v`
Expected: FAIL — `ImportError: cannot import name 'mint_harness_token'` / `_map_status`, and the methods raise `NotImplementedError`.

- [ ] **Step 4: Write the implementation**

Replace the whole body of `backend/pathfinder/sandbox/microvm_control_aws.py`:

```python
# backend/pathfinder/sandbox/microvm_control_aws.py
from __future__ import annotations
import asyncio
import time
import boto3
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle, VMStatus

_STATUS_MAP: dict[str, VMStatus] = {
    "PENDING": "booting",
    "STARTING": "booting",
    "RUNNING": "ready",
    "SUSPENDED": "suspended",
    "TERMINATED": "expired",
    "EXPIRED": "expired",
}


def _map_status(raw: str) -> VMStatus:
    """get-microvm status string -> our VMStatus. Unknown -> 'stopped' (the
    conservative reboot-worthy state). Exact enum strings are drill-confirmed;
    this table is the design mapping (see Open Questions)."""
    return _STATUS_MAP.get(raw, "stopped")


class LambdaMicroVMController(MicroVMController):
    """AWS Lambda MicroVMs control-plane binding (ap-northeast-1, GA 2026-06-22).

    boot()/resume() poll get-microvm until RUNNING before returning, so a
    cached VMHandle is only ever created in the 'ready' state — MicroVMSandbox's
    _ensure_ready therefore never observes a transient 'booting' on a handle it
    just created (Part-2 'booting falls-through' resolution). boto3 is sync, so
    each call is wrapped in asyncio.to_thread (same pattern as S3Store)."""

    def __init__(self, region: str = "ap-northeast-1", client=None,
                 boot_timeout_seconds: float = 120.0, poll_interval_seconds: float = 3.0):
        self.region = region
        self._client = client
        self._boot_timeout = boot_timeout_seconds
        self._poll = poll_interval_seconds

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("lambda-microvms", region_name=self.region)
        return self._client

    async def _get_status_raw(self, vm_id: str) -> str:
        resp = await asyncio.to_thread(self.client.get_microvm, microvmIdentifier=vm_id)
        return resp["microvm"]["status"]

    async def _poll_until_running(self, vm_id: str) -> None:
        deadline = time.monotonic() + self._boot_timeout
        while True:
            raw = await self._get_status_raw(vm_id)
            if _map_status(raw) == "ready":
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"microvm {vm_id} did not reach RUNNING (last status {raw})")
            await asyncio.sleep(self._poll)

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        resp = await asyncio.to_thread(
            self.client.run_microvm,
            imageIdentifier=spec.image_id,
            executionRoleArn=spec.exec_role_arn,
            idlePolicy=spec.idle_policy(),
        )
        vm_id = resp["microvmId"]
        base_url = resp["endpoint"]
        await self._poll_until_running(vm_id)
        return VMHandle(vm_id=vm_id, base_url=base_url, status="ready")

    async def resume(self, handle: VMHandle) -> VMHandle:
        resp = await asyncio.to_thread(
            self.client.resume_microvm, microvmIdentifier=handle.vm_id
        )
        base_url = resp.get("endpoint", handle.base_url)
        await self._poll_until_running(handle.vm_id)
        return VMHandle(vm_id=handle.vm_id, base_url=base_url, status="ready")

    async def suspend(self, handle: VMHandle) -> None:
        await asyncio.to_thread(self.client.suspend_microvm, microvmIdentifier=handle.vm_id)

    async def stop(self, handle: VMHandle) -> None:
        await asyncio.to_thread(self.client.terminate_microvm, microvmIdentifier=handle.vm_id)

    async def status(self, handle: VMHandle) -> VMStatus:
        return _map_status(await self._get_status_raw(handle.vm_id))


def mint_harness_token(vm_id: str, region: str = "ap-northeast-1", client=None,
                       port: int = 8080, minutes: int = 30) -> dict[str, str]:
    """Mint a short-lived JWE via CreateMicrovmAuthToken and return it as the
    harness auth header. Called per handle transition (mint-on-resume) by
    app.py's harness_factory. Max TTL is 60 min; we use 30."""
    c = client if client is not None else boto3.client("lambda-microvms", region_name=region)
    resp = c.create_microvm_auth_token(
        microvmIdentifier=vm_id,
        expirationInMinutes=minutes,
        allowedPorts=[{"port": port}],
    )
    return {"X-aws-proxy-auth": resp["token"]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_microvm_control_aws.py -v`
Expected: PASS — all 7 tests green.

> **Note on Stubber operation/param names.** botocore's `Stubber` validates against the *real* `lambda-microvms` service model (installed in Step 1). If a param name differs from this plan's guess (e.g. `microvmIdentifier` vs `microvmId`, or `get_microvm` response shape `{"microvm":{"status":...}}` vs top-level `{"status":...}`), the Stubber raises `ParamValidationError` at test-authoring time — treat that as the model telling you the truth and adjust BOTH the test and the implementation to the real names. This is the intended CI-testable seam; the drill (Task 6+) confirms runtime behavior.

- [ ] **Step 6: Run the full backend suite (no regression)**

Run: `cd backend && python -m pytest -q`
Expected: 136 pre-existing + new tests pass; 0 failures.

- [ ] **Step 7: Commit**

```bash
git add backend/pathfinder/sandbox/microvm_control_aws.py backend/pyproject.toml \
        backend/tests/test_microvm_control_aws.py
git commit -m "feat(sandbox): bind LambdaMicroVMController to boto3 lambda-microvms + mint_harness_token"
```

---

### Task 5: Wire JWE mint-on-resume into `app.py` `harness_factory`

`harness_factory` is re-invoked by `MicroVMSandbox._ensure_ready` on every handle transition (boot/resume/reboot) — that IS the mint-on-resume point (Part-2 Task-5 seam). Make it mint a fresh token via `mint_harness_token(handle.vm_id, region)` and attach it as `headers=` on the per-handle `HarnessClient`, while keeping the ONE shared `httpx.AsyncClient` (auth is per-`HarnessClient`, not on the shared client, so the shared client's `on_stop=aclose` stays correct). Guard minting so unit tests (which inject a `FakeMicroVMController` with `fake-…` vm ids and no AWS) don't call AWS: mint only when a real token helper is wired — implement via an injectable module-level `_token_provider` that defaults to `mint_harness_token` and is monkeypatched to a no-op in tests.

**Files:**
- Modify: `backend/pathfinder/app.py:44-61` (the `_make_microvm_sandbox`/`harness_factory` block) + a new module-level token-provider seam
- Test: `backend/tests/test_app_harness_factory.py` (new)

**Interfaces:**
- Consumes: `mint_harness_token` (Task 4), `HarnessClient(base_url, http, headers=...)` (Task 1), `VMHandle`.
- Produces:
  - Module-level `def _harness_token_provider(vm_id: str, region: str) -> dict[str, str] | None` (monkeypatchable; default delegates to `mint_harness_token`).
  - `harness_factory(handle)` builds `HarnessClient(base_url=handle.base_url, http=shared_http, headers=_harness_token_provider(handle.vm_id, region))`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_app_harness_factory.py`:

```python
import httpx
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.microvm_control import VMHandle


async def test_harness_factory_attaches_minted_header(monkeypatch):
    calls = {}

    def fake_provider(vm_id, region):
        calls["vm_id"] = vm_id
        calls["region"] = region
        return {"X-aws-proxy-auth": f"tok-for-{vm_id}"}

    monkeypatch.setattr(app_module, "_harness_token_provider", fake_provider)

    hc = app_module._build_harness_for_test(
        VMHandle(vm_id="vm-77", base_url="https://vm", status="ready"),
        httpx.AsyncClient(),
        region="ap-northeast-1",
    )
    assert hc._headers == {"X-aws-proxy-auth": "tok-for-vm-77"}
    assert calls == {"vm_id": "vm-77", "region": "ap-northeast-1"}


async def test_token_provider_none_leaves_headers_unset(monkeypatch):
    monkeypatch.setattr(app_module, "_harness_token_provider", lambda vm_id, region: None)
    hc = app_module._build_harness_for_test(
        VMHandle(vm_id="fake-x", base_url="https://vm", status="ready"),
        httpx.AsyncClient(),
        region="ap-northeast-1",
    )
    assert hc._headers is None


def test_default_provider_delegates_to_mint(monkeypatch):
    import pathfinder.sandbox.microvm_control_aws as aws
    monkeypatch.setattr(aws, "mint_harness_token",
                        lambda vm_id, region=None, **k: {"X-aws-proxy-auth": "z"})
    # app imports the symbol; patch where it is looked up.
    monkeypatch.setattr(app_module, "mint_harness_token",
                        lambda vm_id, region: {"X-aws-proxy-auth": "z"})
    assert app_module._harness_token_provider("vm-1", "ap-northeast-1") == {"X-aws-proxy-auth": "z"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_app_harness_factory.py -v`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute '_build_harness_for_test'` / `_harness_token_provider`.

- [ ] **Step 3: Write the implementation**

In `backend/pathfinder/app.py`, add the import and the token-provider seam, and rewrite `_make_microvm_sandbox`. Add to the imports near line 16:

```python
from pathfinder.sandbox.microvm_control_aws import LambdaMicroVMController, mint_harness_token
```

Add a module-level seam (after `s3_store_factory`, before `_boot_spec`):

```python
# Monkeypatchable in tests so unit tests never call AWS. Returns the auth header
# dict for a HarnessClient, or None to attach no auth (local/fake controllers).
def _harness_token_provider(vm_id: str, region: str) -> dict[str, str] | None:
    if vm_id.startswith("fake-"):   # FakeMicroVMController handles: never mint.
        return None
    return mint_harness_token(vm_id, region)


def _build_harness_for_test(handle: VMHandle, shared_http: httpx.AsyncClient, region: str) -> HarnessClient:
    """Extracted so the header-minting wiring is unit-testable without booting."""
    return HarnessClient(
        base_url=handle.base_url,
        http=shared_http,
        headers=_harness_token_provider(handle.vm_id, region),
    )
```

Rewrite the `harness_factory` inside `_make_microvm_sandbox` to use it:

```python
async def _make_microvm_sandbox(project_id: str) -> Sandbox:
    controller = microvm_controller_factory(project_id)
    s3 = s3_store_factory(project_id)
    region = os.environ.get("PATHFINDER_VM_REGION", "ap-northeast-1")
    shared_http = httpx.AsyncClient(timeout=None)  # streaming SSE: no read timeout
    def harness_factory(handle: VMHandle) -> HarnessClient:
        # mint-on-resume (Part-2 Task 5): a fresh CreateMicrovmAuthToken JWE is
        # minted on every boot/resume/reboot and attached per HarnessClient.
        # The shared AsyncClient is reused (headers live on the HarnessClient,
        # not the client), so on_stop=shared_http.aclose stays correct.
        return _build_harness_for_test(handle, shared_http, region)
    sb = MicroVMSandbox(
        project_id=project_id,
        controller=controller,
        spec=_boot_spec(),
        harness_factory=harness_factory,
        s3=s3,
        on_stop=shared_http.aclose,
    )
    await sb.start()
    return sb
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_app_harness_factory.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Run the full backend suite (no regression)**

Run: `cd backend && python -m pytest -q`
Expected: 136 pre-existing + Task-1/4/5 tests pass; 0 failures. In particular `test_make_sandbox.py` and any MicroVMSandbox tests (which inject `FakeMicroVMController` with `fake-…` ids) still pass because `_harness_token_provider` returns `None` for fake handles.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/app.py backend/tests/test_app_harness_factory.py
git commit -m "feat(app): mint X-aws-proxy-auth JWE per handle transition in harness_factory"
```

---

### Task 6: CDK Tokyo infra — `PathfinderDrillStack` (image + roles + bucket)

A TypeScript CDK v2 app pinned to `ap-northeast-1`. One stack builds the MicroVM image from the packaged harness asset (Dockerfile at zip root + managed al2023 base via `BaseImageArn`), the build role (S3 asset read + CW logs, confused-deputy `SourceAccount` condition), the execution role (Bedrock-only, NO S3), and outputs the four env values the backend/drills consume. NO infra tests beyond `cdk synth` succeeding (documented: synth-in-CI later, drill scope now). `infra/package-harness.sh` stages `harness/` + `files/aiplc-rules/` into the asset dir.

**Files:**
- Create: `infra/package-harness.sh`, `infra/bin/app.ts`, `infra/lib/pathfinder-drill-stack.ts`, `infra/cdk.json`, `infra/package.json`, `infra/tsconfig.json`, `infra/README.md`
- Test (manual): `cd infra && npm ci && ./package-harness.sh && npx cdk synth`

**Interfaces:**
- Consumes: `harness/` (Dockerfile + code, Task 2/3), `files/aiplc-rules/` (baked into the image), `BootSpec.env()` values (`CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION=ap-northeast-1`, `ANTHROPIC_MODEL=global.anthropic.claude-sonnet-5`).
- Produces (CfnOutputs, consumed by drill env in Task 7): `ImageArn` (→ `PATHFINDER_VM_IMAGE_ID`), `ExecutionRoleArn` (→ `PATHFINDER_VM_ROLE_ARN`), `ArtifactsBucketName` (→ `PATHFINDER_S3_BUCKET`), `Region` (`ap-northeast-1`).

- [ ] **Step 1: Write the packaging script**

Create `infra/package-harness.sh`:

```bash
#!/usr/bin/env bash
# Stage harness/ + files/aiplc-rules/ into infra/build/harness/ with the
# Dockerfile at the root, so the CDK aws_s3_assets.Asset zips exactly what
# MicrovmImage's CodeArtifact expects (Dockerfile at zip root).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build/harness"

rm -rf "$BUILD"
mkdir -p "$BUILD"

# harness code (excluding tests / caches) at the build root
cp "$REPO/harness/Dockerfile" "$BUILD/Dockerfile"
cp "$REPO/harness/requirements.txt" "$BUILD/requirements.txt"
cp "$REPO/harness/"*.py "$BUILD/"

# rules baked into the image (repo files/ is gitignored reference material —
# the drill machine MUST have files/aiplc-rules/ present, else this fails).
if [ ! -f "$REPO/files/aiplc-rules/aws-aiplc-rules/core-workflow.md" ]; then
  echo "ERROR: files/aiplc-rules/ missing (gitignored reference material). Populate it on the drill machine." >&2
  exit 1
fi
mkdir -p "$BUILD/aiplc-rules"
cp -R "$REPO/files/aiplc-rules/." "$BUILD/aiplc-rules/"

echo "EXPECTED: $BUILD contains Dockerfile, *.py, aiplc-rules/"
ls -1 "$BUILD"
```

- [ ] **Step 2: Write the Dockerfile (in `harness/`, referenced by the asset)**

Create `harness/Dockerfile`:

```dockerfile
# harness/Dockerfile — layered over the managed al2023 base (supplied at build
# time via MicrovmImage BaseImageArn; this Dockerfile only adds our app layer).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023
RUN dnf install -y python3.11 python3.11-pip nodejs npm && dnf clean all
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
# Rules baked into the workspace the harness serves from.
COPY aiplc-rules /workspace/aiplc-rules
EXPOSE 8080 9000
CMD ["python3.11", "-m", "serve"]
```

> **Note:** `BaseImageArn` = `arn:aws:lambda:ap-northeast-1:aws:microvm-image:al2023-1` supplies the managed Firecracker base; the platform layers this Dockerfile on top. The `FROM` line is the local-build fallback for `docker build` sanity checks; the MicrovmImage build uses the managed base. Exact base ARN/version is an Open Question the preflight drill confirms.

- [ ] **Step 3: Write the CDK stack**

Create `infra/lib/pathfinder-drill-stack.ts`:

```typescript
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';

const REGION = 'ap-northeast-1';
const MODEL = 'global.anthropic.claude-sonnet-5';
const BASE_IMAGE_ARN = `arn:aws:lambda:${REGION}:aws:microvm-image:al2023-1`;

export class PathfinderDrillStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const account = cdk.Stack.of(this).account;

    // Artifacts bucket — drill scope: destroyed with the stack.
    const bucket = new s3.Bucket(this, 'Artifacts', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // Harness code asset (infra/build/harness produced by package-harness.sh:
    // Dockerfile at root). aws_s3_assets zips it into an S3 object.
    const harnessAsset = new s3assets.Asset(this, 'HarnessCode', {
      path: path.join(__dirname, '..', 'build', 'harness'),
    });

    // Build role: read the code artifact + write build logs. Confused-deputy
    // guard: only the MicroVM image-build service in THIS account may assume it.
    const buildRole = new iam.Role(this, 'BuildRole', {
      assumedBy: new iam.ServicePrincipal('microvms.lambda.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': account } },
      }),
    });
    harnessAsset.grantRead(buildRole);
    buildRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:aws:logs:${REGION}:${account}:log-group:/pathfinder/microvm/*`],
    }));

    // Execution role: assumed by the RUNNING VM. Bedrock invoke ONLY, NO S3 —
    // preserving the security boundary (the VM cannot reach durable storage).
    // pilot1-validated shape: inference-profile ARN + foundation-model wildcard.
    const execRole = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('microvms.lambda.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': account } },
      }),
    });
    execRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:aws:bedrock:*:${account}:inference-profile/${MODEL}`,
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5*`,
      ],
    }));

    const logGroup = new logs.LogGroup(this, 'MicrovmLogs', {
      logGroupName: '/pathfinder/microvm/harness',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      retention: logs.RetentionDays.ONE_WEEK,
    });

    // The MicroVM image (L1 CfnMicrovmImage; L2 does not exist yet). Hooks are
    // served by harness/hooks.py on port 9000: /ready gates the build snapshot,
    // /validate gates resume-from-snapshot. Env is baked = BootSpec.env().
    const image = new lambda.CfnMicrovmImage(this, 'HarnessImage', {
      name: 'pathfinder-harness',
      baseImageArn: BASE_IMAGE_ARN,
      buildRoleArn: buildRole.roleArn,
      codeArtifact: { uri: harnessAsset.s3ObjectUrl },
      environmentVariables: {
        CLAUDE_CODE_USE_BEDROCK: '1',
        AWS_REGION: REGION,
        ANTHROPIC_MODEL: MODEL,
      },
      cpuConfigurations: [{ architecture: 'ARM_64' }],
      resources: [{ minimumMemoryInMiB: 2048 }],
      hooks: {
        port: 9000,
        microvmImageHooks: {
          ready: { path: '/ready', timeoutSeconds: 300 },
          validate: { path: '/validate', timeoutSeconds: 60 },
        },
      },
      logging: { cloudWatch: { logGroup: logGroup.logGroupName } },
    });
    // Runtime hooks (run/resume/suspend/terminate) are fast-notification and
    // OPTIONAL; skipped for now (YAGNI) — our lifecycle is driven by the
    // controller polling get-microvm, not by in-VM runtime-hook callbacks.

    new cdk.CfnOutput(this, 'ImageArn', { value: image.attrArn });
    new cdk.CfnOutput(this, 'ExecutionRoleArn', { value: execRole.roleArn });
    new cdk.CfnOutput(this, 'ArtifactsBucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'Region', { value: REGION });
  }
}
```

> **Note — L1 property names are best-effort against the 2026-06-22 schema.** `CfnMicrovmImage`'s exact property casing (`codeArtifact.uri`, `hooks.microvmImageHooks.ready/validate`, `attrArn`) is confirmed by `cdk synth` (Step 6): synth fails loudly on an unknown property, which is the authoring-time signal to correct against the installed `aws-cdk-lib`'s generated types. Fix names to what synth accepts; the shape here follows the documented schema.

- [ ] **Step 4: Write the CDK app entry + config files**

Create `infra/bin/app.ts`:

```typescript
#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { PathfinderDrillStack } from '../lib/pathfinder-drill-stack';

const app = new cdk.App();
new PathfinderDrillStack(app, 'PathfinderDrillStack', {
  env: { region: 'ap-northeast-1', account: process.env.CDK_DEFAULT_ACCOUNT },
});
```

Create `infra/cdk.json`:

```json
{
  "app": "npx ts-node --prefer-ts-exts bin/app.ts",
  "context": {
    "@aws-cdk/core:newStyleStackSynthesis": true
  }
}
```

Create `infra/package.json`:

```json
{
  "name": "pathfinder-infra",
  "version": "0.1.0",
  "private": true,
  "bin": { "app": "bin/app.ts" },
  "scripts": {
    "synth": "cdk synth",
    "deploy": "cdk deploy"
  },
  "devDependencies": {
    "aws-cdk": "^2.150.0",
    "ts-node": "^10.9.2",
    "typescript": "^5.4.0"
  },
  "dependencies": {
    "aws-cdk-lib": "^2.150.0",
    "constructs": "^10.3.0"
  }
}
```

Create `infra/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "lib": ["ES2020"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "types": ["node"]
  },
  "include": ["bin/**/*.ts", "lib/**/*.ts"]
}
```

Create `infra/README.md`:

```markdown
# Pathfinder Drill Infra (CDK, ap-northeast-1)

Single stack `PathfinderDrillStack`: MicroVM image (harness + aiplc-rules baked),
build/execution IAM roles (execution role = Bedrock-only, no S3), artifacts bucket.

## Synth / deploy
```bash
npm ci
./package-harness.sh          # stages harness/ + files/aiplc-rules/ into build/harness/
npx cdk synth                 # validates the stack (no AWS creds needed)
npx cdk deploy                # creates the image (async CREATING->CREATED) + roles + bucket
```
Feed the CfnOutputs into the drill env: `ImageArn`→`PATHFINDER_VM_IMAGE_ID`,
`ExecutionRoleArn`→`PATHFINDER_VM_ROLE_ARN`, `ArtifactsBucketName`→`PATHFINDER_S3_BUCKET`.
Image versions cost storage — clean up old versions after drills (see `99-teardown.sh`).
```

- [ ] **Step 5: Package the harness asset**

Run:
```bash
chmod +x infra/package-harness.sh && ./infra/package-harness.sh
```
Expected: prints `EXPECTED: .../build/harness contains Dockerfile, *.py, aiplc-rules/` and lists `Dockerfile`, `app.py`, `claude_driver.py`, `hooks.py`, `serve.py`, `requirements.txt`, `aiplc-rules`.

- [ ] **Step 6: Synth the stack (the only infra "test")**

Run:
```bash
cd infra && npm ci && npx cdk synth
```
Expected: prints the synthesized CloudFormation template (YAML) with an `AWS::Lambda::MicrovmImage`, two `AWS::IAM::Role`s, an `AWS::S3::Bucket`, a `AWS::Logs::LogGroup`, and four `Outputs`. NO synth errors. If synth reports an unknown property on `CfnMicrovmImage`, correct the property name to what the installed `aws-cdk-lib` accepts (see the Step-3 note) and re-synth until clean.

- [ ] **Step 7: Commit**

```bash
git add infra/ harness/Dockerfile
git commit -m "feat(infra): CDK PathfinderDrillStack — MicrovmImage + build/exec roles + artifacts bucket (Tokyo)"
```

---

## Phase B — MicroVM Part 2 Task 8 drills (INTEGRATION — REQUIRES AWS)

### Task 7: AWS drill scripts (scripted-manual, NEVER pytest)

> **INTEGRATION — REQUIRES AWS.** These are scripted-manual verifications, NOT pytest. They need real credentials, the deployed `PathfinderDrillStack` (Task 6), Bedrock in `ap-northeast-1`, and `files/aiplc-rules/` present. NEVER collected by pytest. Each script is `set -euo pipefail`, echoes an EXPECTED block, exits nonzero on automatable failed expectations, and records outputs under `scripts/aws-drills/out/`. **Region policy:** every drill exports `PATHFINDER_S3_REGION=ap-northeast-1` (Tokyo-unified, drill scope, synthetic data) — this override lives ONLY in the drill env; code defaults stay Seoul.

**Files:**
- Create: `scripts/aws-drills/00-preflight.sh`, `10-smoke-turn.sh`, `20-s3-roundtrip.sh`, `30-recovery-drill.sh`, `40-reconcile-drill.sh`, `50-glob-parity.sh`, `99-teardown.sh`, `README.md`
- Commit at the end (no TDD cycle — these are manual harnesses).

**Interfaces:**
- Consumes: the deployed stack outputs, `LambdaMicroVMController` behavior (Task 4), the running backend (`PATHFINDER_SANDBOX=microvm`), the AWS CLI `lambda-microvms` + `bedrock` + `s3` commands.
- Produces: recorded evidence files under `scripts/aws-drills/out/` for the PR description.

- [ ] **Step 1: Write `scripts/aws-drills/README.md`**

```markdown
# Pathfinder AWS Drills (INTEGRATION — REQUIRES AWS)

Scripted-manual verification of the real MicroVM path. NOT pytest. Run in order.
All resources are Tokyo (ap-northeast-1), synthetic data only (drill scope).

## Env (export before running)
| var | value | source |
|-----|-------|--------|
| `AWS_REGION` | `ap-northeast-1` | fixed |
| `PATHFINDER_SANDBOX` | `microvm` | fixed |
| `PATHFINDER_VM_REGION` | `ap-northeast-1` | fixed |
| `PATHFINDER_S3_REGION` | `ap-northeast-1` | drill override (code default = Seoul) |
| `ANTHROPIC_MODEL` | `global.anthropic.claude-sonnet-5` | fixed |
| `PATHFINDER_VM_IMAGE_ID` | stack output `ImageArn` | `cdk deploy` |
| `PATHFINDER_VM_ROLE_ARN` | stack output `ExecutionRoleArn` | `cdk deploy` |
| `PATHFINDER_S3_BUCKET` | stack output `ArtifactsBucketName` | `cdk deploy` |
| `BACKEND` | `http://localhost:8000` | local backend |

## Order
00-preflight → 10-smoke-turn → 20-s3-roundtrip → 30-recovery-drill →
40-reconcile-drill → 50-glob-parity → 99-teardown

Outputs land in `out/`. Paste them into the PR description as drill evidence.
```

- [ ] **Step 2: Write `00-preflight.sh` (env + model re-verify + boto3 model + image listing)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
echo "EXPECTED: all env set; sonnet-5 ACTIVE in ap-northeast-1; lambda-microvms model present; image CREATED"

: "${PATHFINDER_VM_IMAGE_ID:?set from stack output ImageArn}"
: "${PATHFINDER_VM_ROLE_ARN:?set from stack output ExecutionRoleArn}"
: "${PATHFINDER_S3_BUCKET:?set from stack output ArtifactsBucketName}"
export AWS_REGION=ap-northeast-1 PATHFINDER_VM_REGION=ap-northeast-1 \
       PATHFINDER_S3_REGION=ap-northeast-1 PATHFINDER_SANDBOX=microvm \
       ANTHROPIC_MODEL=global.anthropic.claude-sonnet-5

# 1) Bedrock model pin re-verify (the id can be renamed; re-check every deploy).
aws bedrock list-inference-profiles --region ap-northeast-1 \
  --query "inferenceProfileSummaries[?inferenceProfileId=='global.anthropic.claude-sonnet-5'].[inferenceProfileId,status]" \
  --output text | tee out/00-model.txt
grep -q "ACTIVE" out/00-model.txt || { echo "FAIL: sonnet-5 not ACTIVE"; exit 1; }

# 2) boto3 has the lambda-microvms service model (the floor-bump verification).
python -c "import boto3,sys; sys.exit(0 if 'lambda-microvms' in boto3.session.Session().get_available_services() else 1)" \
  || { echo "FAIL: boto3 lacks lambda-microvms model — bump boto3 floor"; exit 1; }

# 3) The deployed image is built (async CREATING->CREATED) and listable.
aws lambda-microvms list-managed-microvm-images --region ap-northeast-1 \
  | tee out/00-managed-images.json
aws lambda-microvms get-microvm-image --image-identifier "$PATHFINDER_VM_IMAGE_ID" \
  --region ap-northeast-1 --query "status" --output text | tee out/00-image-status.txt
grep -q "CREATED" out/00-image-status.txt || { echo "FAIL: image not CREATED"; exit 1; }
echo "PASS: preflight"
```

- [ ] **Step 3: Write `10-smoke-turn.sh` (boot → health → rules → one Korean Sonnet turn → credential grep)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
: "${BACKEND:=http://localhost:8000}"
echo "EXPECTED: /health 200; rules glob non-empty; SSE ends in done; NO credential markers in stream"

# Create a project and drive one turn (Korean). The backend boots the Tokyo VM.
curl -fsS -X POST "$BACKEND/projects" -H 'content-type: application/json' \
  -d '{"project_id":"aws-smoke"}' >/dev/null

# One real Sonnet-5 turn: "ai-plc를 시작하고 싶어" url-encoded.
curl -N -sS "$BACKEND/projects/aws-smoke/events?text=ai-plc%EB%A5%BC%20%EC%8B%9C%EC%9E%91%ED%95%98%EA%B3%A0%20%EC%8B%B6%EC%96%B4" \
  | tee out/10-turn.sse
grep -q '"kind": *"done"' out/10-turn.sse || { echo "FAIL: no done frame (Bedrock/boot failed?)"; exit 1; }

# Rules baked into the image are present in the workspace (proves image bake).
# The VM is up from the turn; confirm via the harness /files list surfaced by
# a route that lists workspace files, OR directly with a minted token+curl:
#   token=$(aws lambda-microvms create-microvm-auth-token --microvm-identifier <id> \
#     --expiration-in-minutes 30 --allowed-ports port=8080 --query token --output text)
#   curl -fsS -H "X-aws-proxy-auth: $token" "<endpoint>/files?glob=aiplc-rules/%2A%2A/%2A.md"
# Expected: a non-empty JSON list including aws-aiplc-rules/core-workflow.md and
# aws-aiplc-rule-details/common/session-continuity.md.

# Redaction VERIFICATION on the streamed answer (route-seam redaction holds).
if grep -Eq 'AKIA[0-9A-Z]{12,}|sk-[A-Za-z0-9-]{10,}|bedrock-api-key-|AWS_BEARER_TOKEN=' out/10-turn.sse; then
  echo "FAIL: credential marker present in stream (redaction regression)"; exit 1
fi
echo "PASS: smoke turn (record out/10-turn.sse + rules listing in the PR)"
```

> **Multi-block real-stream capture (reviewer's fixture-vs-reality directive, folded into Task 2's hardening).** `translate()`'s unit tests are asserted against RECORDED-SHAPE fixtures (`basic_turn.jsonl`, `multi_block_turn.jsonl`) that assume real Claude Code stream-json assistant messages can bundle multiple content blocks in one object (text + tool_use together, or several parallel tool_use blocks) — a shape a first-match-return `translate()` would have silently truncated. `out/10-turn.sse` from THIS drill is the authoritative check that this assumption holds against the real CLI: inspect it for any single `"type":"assistant"` line whose `message.content` array has length > 1, and confirm every block in that array produced a corresponding SSE event (not just the first). If the real stream never bundles blocks this way, `multi_block_turn.jsonl` remains a defensive-but-unexercised-in-production fixture; if it does but in a different combination/order than recorded, update `multi_block_turn.jsonl` (and `translate()` if the shape itself differs) to match — same "update BOTH the fixture and `translate()`" discipline as Open Question 3.

- [ ] **Step 4: Write `20-s3-roundtrip.sh` (no-boot laziness proof)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
: "${BACKEND:=http://localhost:8000}"
: "${PATHFINDER_S3_BUCKET:?}"
echo "EXPECTED: artifact lands in S3; list-microvms shows NO VM for s3-smoke (file ops never boot)"

curl -fsS -X POST "$BACKEND/projects" -H 'content-type: application/json' \
  -d '{"project_id":"s3-smoke"}' >/dev/null
# A route that calls write_file (answers/artifacts) — file ops go straight to S3.
curl -fsS -X PUT "$BACKEND/projects/s3-smoke/answers/discovery-mode-selection" \
  -H 'content-type: application/json' -d '{"answers":{"q1":"synthetic"}}' >/dev/null || true

aws s3 ls "s3://$PATHFINDER_S3_BUCKET/projects/s3-smoke/" --recursive \
  --region ap-northeast-1 | tee out/20-s3-ls.txt
test -s out/20-s3-ls.txt || { echo "FAIL: nothing written to S3"; exit 1; }

aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId, 's3-smoke')]" | tee out/20-vms.json
# Expect an empty list [] — true laziness: file ops did not boot a VM.
python -c "import json,sys; sys.exit(0 if json.load(open('out/20-vms.json'))==[] else 1)" \
  || { echo "FAIL: a VM was booted for a pure file op (laziness broken)"; exit 1; }
echo "PASS: S3 round-trip, no VM booted"
```

- [ ] **Step 5: Write `30-recovery-drill.sh` (kill VM → next turn recovers)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
: "${BACKEND:=http://localhost:8000}"
echo "EXPECTED: after terminate, next turn boots a NEW microvmId, restores state from S3, self-resumes"

# Drive a turn so a VM exists, capture its id.
curl -N -sS "$BACKEND/projects/aws-smoke/events?text=%EA%B3%84%EC%86%8D" >/dev/null  # "계속"
OLD=$(aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId,'aws-smoke')].microvmId | [0]" --output text)
echo "old microvmId=$OLD" | tee out/30-old.txt
[ "$OLD" != "None" ] && [ -n "$OLD" ] || { echo "FAIL: no VM to kill"; exit 1; }

# Kill it out-of-band (simulate expiry/crash), confirm terminal status maps.
aws lambda-microvms terminate-microvm --region ap-northeast-1 --microvm-identifier "$OLD"
aws lambda-microvms get-microvm --region ap-northeast-1 --microvm-identifier "$OLD" \
  --query "microvm.status" --output text | tee out/30-terminated-status.txt
# (confirms the exact terminal enum string -> feeds the get-microvm status Open Question)

# Next turn on the SAME project: _ensure_ready maps expired -> reboots fresh + restores.
curl -N -sS "$BACKEND/projects/aws-smoke/events?text=%EA%B3%84%EC%86%8D" | tee out/30-recovery.sse
grep -q '"kind": *"done"' out/30-recovery.sse || { echo "FAIL: recovery turn had no done"; exit 1; }

NEW=$(aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId,'aws-smoke')].microvmId | [0]" --output text)
echo "new microvmId=$NEW" | tee out/30-new.txt
[ "$NEW" != "$OLD" ] || { echo "FAIL: same microvmId — no fresh boot"; exit 1; }
# Confirm restored state: the resumed stage should match pre-termination.
curl -fsS "$BACKEND/projects/aws-smoke/state" | tee out/30-state.json
echo "PASS: recovery — new VM $NEW restored from S3 and self-resumed (record all out/30-*)"
```

- [ ] **Step 6: Write `40-reconcile-drill.sh` (suspend → write to S3 → resume sees it)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
: "${BACKEND:=http://localhost:8000}"
echo "EXPECTED: write during suspend lands in S3; next turn resumes + reconciles S3->VM; answer present"

VM=$(aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId,'aws-smoke')].microvmId | [0]" --output text)
[ -n "$VM" ] && [ "$VM" != "None" ] || { echo "FAIL: no VM to suspend"; exit 1; }
aws lambda-microvms suspend-microvm --region ap-northeast-1 --microvm-identifier "$VM"
aws lambda-microvms get-microvm --region ap-northeast-1 --microvm-identifier "$VM" \
  --query "microvm.status" --output text | tee out/40-suspended-status.txt
grep -qi "SUSPENDED" out/40-suspended-status.txt || { echo "FAIL: not suspended"; exit 1; }

# While suspended, write an answer via a route — lands in S3 ONLY (VM is asleep).
curl -fsS -X PUT "$BACKEND/projects/aws-smoke/answers/discovery-mode-selection" \
  -H 'content-type: application/json' -d '{"answers":{"marker":"WRITTEN_WHILE_SUSPENDED"}}' >/dev/null

# Next turn: _ensure_ready sees SUSPENDED -> resume -> reconcile pushes S3->VM.
curl -N -sS "$BACKEND/projects/aws-smoke/events?text=%EC%99%84%EB%A3%8C" >/dev/null  # "완료"
# Confirm the resumed VM now holds the answer written during suspend (read back
# through the durable store, which the reconcile made authoritative in the VM).
curl -fsS "$BACKEND/projects/aws-smoke/answers/discovery-mode-selection" | tee out/40-answer.json
grep -q "WRITTEN_WHILE_SUSPENDED" out/40-answer.json || { echo "FAIL: reconcile lost the suspend-time write"; exit 1; }
echo "PASS: suspend/resume reconcile"
```

- [ ] **Step 7: Write `50-glob-parity.sh` (real harness /files?glob= vs globmatch expectations)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
echo "EXPECTED: real harness /files?glob= returns the SAME set as globmatch.matches_glob on seeded files"

# Seed nested + top-level files in the VM workspace via minted-token PUTs, then
# compare the harness glob result to the backend's globmatch expectation.
: "${VM_ENDPOINT:?set to the VM endpoint from run-microvm/list-microvms}"
: "${VM_ID:?set to the microvmId}"
TOKEN=$(aws lambda-microvms create-microvm-auth-token --region ap-northeast-1 \
  --microvm-identifier "$VM_ID" --expiration-in-minutes 30 \
  --allowed-ports port=8080 --query token --output text)
AUTH=(-H "X-aws-proxy-auth: $TOKEN")

for p in aiplc-docs/audit.md aiplc-docs/a-questions.md aiplc-docs/sub/b-questions.md prototype/app.py; do
  curl -fsS "${AUTH[@]}" -X PUT "$VM_ENDPOINT/files/$p" --data-binary "seed" >/dev/null
done

# Real harness result for a recursive glob:
curl -fsS "${AUTH[@]}" "$VM_ENDPOINT/files?glob=aiplc-docs/%2A%2A/%2A-questions.md" \
  | python -m json.tool | tee out/50-harness-glob.json

# Backend expectation over the SAME seeded keys (identical globmatch the sandbox uses):
python - <<'PY' | tee out/50-expected-glob.json
import json
from pathfinder.sandbox.globmatch import matches_glob
keys = ["aiplc-docs/audit.md","aiplc-docs/a-questions.md","aiplc-docs/sub/b-questions.md","prototype/app.py"]
print(json.dumps(sorted(k for k in keys if matches_glob(k, "aiplc-docs/**/*-questions.md"))))
PY

diff <(python -c "import json;print(sorted(json.load(open('out/50-harness-glob.json'))))") \
     <(python -c "import json;print(sorted(json.load(open('out/50-expected-glob.json'))))") \
  && echo "PASS: glob parity (real harness == globmatch)" \
  || { echo "FAIL: glob parity mismatch (fixture-vs-reality seam)"; exit 1; }
```

- [ ] **Step 8: Write `99-teardown.sh` (terminate VMs + cleanup note)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"; mkdir -p out
echo "EXPECTED: all drill VMs terminated; image versions + stack cleaned up manually"

for VM in $(aws lambda-microvms list-microvms --region ap-northeast-1 \
    --query "microvms[?contains(tags.projectId,'smoke')].microvmId" --output text); do
  echo "terminating $VM"
  aws lambda-microvms terminate-microvm --region ap-northeast-1 --microvm-identifier "$VM" || true
done
aws lambda-microvms list-microvms --region ap-northeast-1 | tee out/99-remaining.json
echo "NOTE: image versions cost storage — delete old MicrovmImage versions, then:"
echo "  cd infra && npx cdk destroy PathfinderDrillStack"
echo "PASS: teardown (confirm out/99-remaining.json shows no drill VMs)"
```

- [ ] **Step 9: Make executable, confirm NOT pytest-collected, and commit**

```bash
chmod +x scripts/aws-drills/*.sh
# Prove pytest never collects the drills (they are bash, no test_*.py):
cd backend && python -m pytest -q --collect-only 2>/dev/null | grep -c "aws-drills" && echo "UNEXPECTED" || echo "OK: no drill collected"
cd .. && git add scripts/aws-drills/
git commit -m "test(drills): INTEGRATION AWS drills — preflight, smoke, s3-roundtrip, recovery, reconcile, glob-parity, teardown"
```
Expected: `OK: no drill collected`; commit succeeds.

---

## Deferred (YAGNI — encoded, not built)

- **Runtime hooks (run/resume/suspend/terminate).** Fast-notification (1-60s), optional. Skipped: our lifecycle is driven by the controller POLLING `get-microvm` (Task 4), not by in-VM runtime-hook callbacks — adding them now would duplicate the poll signal with no consumer. Revisit only if we need sub-second suspend/resume notification.
- **`/validate` as a real Sonnet turn.** Rejected: a paid Bedrock call on EVERY resume is slow + costly. `/validate` cheaply re-checks `/health` + rules presence (Task 3). Tradeoff documented in `hooks.py`.
- **Per-turn sync cost / content-hash skip.** `_sync_workspace_to_s3` full-copies each turn (already shipped in Part 2). If a drill measures unacceptable latency on a large `prototype/**` tree, add a content-hash skip — localized, no interface change. Not built now.
- **CDK/synth in CI.** No infra unit tests beyond `cdk synth` succeeding (Task 6 Step 6). Wiring synth into CI is deferred to post-drill (drill scope now).
- **`NetworkConnector` (`AWS::Lambda::NetworkConnector`).** New resource; not needed for the drill (default networking suffices for Bedrock egress). Note: suspend→resume cannot switch network connectors, so if we later add one, the mint-on-resume path is unaffected but connector changes require a fresh boot.

## Open Questions (confirmed/closed by drills, not code blockers)

1. **Exact `get-microvm` status enum strings.** The design maps PENDING/STARTING→"booting", RUNNING→"ready", SUSPENDED→"suspended", TERMINATED/EXPIRED→"expired", unknown→"stopped" (`_map_status`, Task 4). The real strings are captured by `30-recovery-drill.sh` (`out/30-terminated-status.txt`) and `40-reconcile-drill.sh` (`out/40-suspended-status.txt`). If they differ (e.g. `STOPPED` vs `TERMINATED`), extend `_STATUS_MAP` — the `else→"stopped"` default keeps unknown states reboot-worthy, so a wrong guess degrades to a safe reboot, never a stuck handle.
2. **`boto3` minimum version for the `lambda-microvms` model.** Verified 2026-07-18 that the repo's installed botocore (1.34.162) does NOT ship the model (`get_available_services()` omits `lambda-microvms`). Task 4 pins `boto3>=1.40`; the preflight (`00-preflight.sh` step 2) asserts the model is present. Confirm the exact first-shipping version and tighten the floor if `1.40` proves insufficient.
3. **stream-json schema fixture-vs-reality.** `harness/tests/fixtures/basic_turn.jsonl` and `multi_block_turn.jsonl` are recorded-SHAPE samples; `translate()` (Task 2, returns `list[AgentEvent]` so multi-block assistant messages translate every block) is asserted against them. `10-smoke-turn.sh` captures a REAL stream (`out/10-turn.sse`); if the real assistant/tool_use/result shape — including whether/how blocks are bundled together — differs from the fixtures, update BOTH the fixtures and `translate()`. This is the honestly-flagged seam.
4. **Managed base image exact ARN/version.** The plan uses `arn:aws:lambda:ap-northeast-1:aws:microvm-image:al2023-1`. `00-preflight.sh` step 3 (`list-managed-microvm-images`) confirms the exact ARN + latest minor; correct `BASE_IMAGE_ARN` in the stack if it differs.
5. **Does the endpoint serve 8080 by default for our harness, or need `X-aws-proxy-port`?** Default proxy port is 8080 and our harness listens there, so the header should be unnecessary. `50-glob-parity.sh` / `10-smoke-turn.sh` reach the harness via the endpoint with only `X-aws-proxy-auth`; if a request 502s, add `-H "X-aws-proxy-port: 8080"` and record which was needed.
6. **`CfnMicrovmImage` L1 property casing.** Best-effort against the 2026-06-22 schema; `cdk synth` (Task 6 Step 6) is authoring-time truth — correct any unknown-property error to the installed `aws-cdk-lib` generated type names.
7. **`create-microvm-auth-token` CLI param shape for `allowedPorts`.** Boto3 (Stubber, Task 4) uses `allowedPorts=[{"port":8080}]`; the CLI form in drills is `--allowed-ports port=8080`. If the CLI shorthand rejects it, use `--allowed-ports '[{"port":8080}]'` (JSON) — recorded when first run.

## Self-Review

**1. Spec coverage.**

*Phase A (original Part-1 Task 7 text — `docs/superpowers/plans/2026-07-17-pathfinder-microvm-sandbox.md`):*
- Re-verify Sonnet-5 profile + IAM scope note → `00-preflight.sh` step 1 + Global Constraints IAM shape + Task 6 exec-role policy. ✔
- Complete `LambdaMicroVMController` against real API → **Task 4** (boto3 binding, all 5 methods, Stubber tests). ✔ (upgrade over the old text: it's now CI-tested, not just manual.)
- `/health` reachable with auth → Task 1 header seam + `10-smoke-turn.sh`. Corrected the old text's `Authorization: Bearer` guess to `X-aws-proxy-auth` (Global Constraints + Task 1). ✔
- `aiplc-rules` present in workspace → baked by Task 6 Dockerfile/CDK + verified in `10-smoke-turn.sh` (rules glob) + `hooks.py rules_present`. ✔
- One real Sonnet-5 turn end-to-end + credential grep → `10-smoke-turn.sh` (SSE `done` + marker grep = redaction verification). ✔
- REAL in-VM harness (only a test fake existed) → **Tasks 2+3** (`harness/` production package speaking the identical protocol). ✔
- CDK Tokyo infra → **Task 6**. ✔
- JWE auth wiring → **Tasks 1+4+5** (`X-aws-proxy-auth`, `mint_harness_token`, `harness_factory`). ✔

*Phase B (Part-2 Task 8 text + ledger checklist — `docs/superpowers/plans/2026-07-18-pathfinder-microvm-persistence-recovery.md`):*
- S3 round-trip, no VM boot → `20-s3-roundtrip.sh` (list-microvms empty). ✔
- Post-turn sync + redaction-at-rest → verified in `10`/`30` SSE + Global Constraints (redaction already implemented → now a VERIFICATION, not "empirical grep decides"). ✔
- Kill-VM/recovery drill → `30-recovery-drill.sh` (new microvmId + restored state + self-resume). ✔
- Suspend/write/resume-reconcile → `40-reconcile-drill.sh`. ✔
- Real-harness glob-parity → `50-glob-parity.sh` (real `/files?glob=` vs `matches_glob`). ✔
- **Booting semantics** (Task-8 "booting falls-through" checklist item) → RESOLVED in **Task 4**: `boot()`/`resume()` poll `get-microvm` until RUNNING before returning, so cached handles are only ever `ready`; `_ensure_ready` never sees transient `booting` on a handle it just created. Documented in the controller docstring + Open Question 1. ✔
- **Glob parity** checklist item → `50-glob-parity.sh`. ✔
- **Cross-region note** checklist item → Global Constraints (code defaults stay Seoul; drills Tokyo-unified; production re-decided pre-workshop) + `README.md` env table. ✔
- **JWE mint** checklist item → mint-on-resume via `harness_factory` re-invocation (Task 5), real `create_microvm_auth_token` (Task 4). ✔

**2. Placeholder scan.** No TBD/TODO/"implement later"/"similar to Task N". Every code step shows complete code; every drill shows the full script with EXPECTED blocks. The remaining uncertainties (L1 property casing, exact enum strings, exact boto3 floor, base-image ARN, CLI param shorthand) are named in Open Questions with a concrete authoring-time confirmation step (cdk synth / Stubber ParamValidationError / preflight assertion) and a safe-default fallback — they are honest reality-seams, not lazy placeholders. The only `NotImplementedError` in the repo (`LambdaMicroVMController`) is REMOVED by Task 4.

**3. Type consistency.**
- `HarnessClient(base_url, http, headers=None)` — the `headers` name/type used identically in Task 1 (definition), Task 5 (`_build_harness_for_test`), and asserted via `hc._headers` in both tests. ✔
- `AgentEvent{kind,text,path}` — harness's local model (Task 2) mirrors `backend/pathfinder/sandbox/base.py` field-for-field; `translate()` and `build_app` return the same shape the backend `HarnessClient` parses. ✔
- `LambdaMicroVMController(region, client=None, boot_timeout_seconds=120.0, poll_interval_seconds=3.0)` — same signature in Task 4 definition and all its tests; `boot/resume` return `VMHandle(status="ready")` matching the ABC (`microvm_control.py`). `_map_status`/`mint_harness_token` names used identically in Task 4 and Task 5. ✔
- `mint_harness_token(vm_id, region, client=None, port=8080, minutes=30) -> {"X-aws-proxy-auth": token}` — same in Task 4 and consumed by Task 5's `_harness_token_provider`. ✔
- CDK CfnOutputs `ImageArn`/`ExecutionRoleArn`/`ArtifactsBucketName`/`Region` (Task 6) map to `PATHFINDER_VM_IMAGE_ID`/`PATHFINDER_VM_ROLE_ARN`/`PATHFINDER_S3_BUCKET` consumed by `_boot_spec`/`s3_store_factory` (existing `app.py`) and the drill README env table (Task 7) — names consistent end-to-end. ✔
- `BootSpec.idle_policy()` shape `{maxIdleDurationSeconds,suspendedDurationSeconds,autoResumeEnabled}` (existing) is exactly what Task 4's `run_microvm` Stubber asserts and what the CDK env comment references. ✔

**4. Scope-guard audit.** No task edits routes, parsers, the `Sandbox` ABC, `LocalSandbox`, or `MicroVMSandbox`. `HarnessClient` change is additive (optional param; `FakeHarness`/contract untouched). `app.py` change is confined to `_make_microvm_sandbox` + two new module-level helpers. Backend suite re-run after Tasks 4 and 5 (Step 6 / Step 5). Frontend untouched (123 stays green — not imported by any task). `harness/` and `infra/` are new top-level dirs, each with its own manifest and README line in File Structure.

**5. CI-runnable unit-test count promised.** Task 1 = 3 (header seam), Task 2 = 8 (`translate` ×5 + `ClaudeDriver.run` ×3), Task 3 = 9 (app ×5 + hooks ×4), Task 4 = 7 (control-aws Stubber), Task 5 = 3 (`harness_factory` wiring) → **30 new CI-runnable unit tests**, all no-AWS (botocore `Stubber` + a stub `claude` executable). All 7 Phase-B drill scripts are INTEGRATION and are NEVER pytest-collected.
