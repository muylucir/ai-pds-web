# Pathfinder MicroVM Sandbox — Durable Persistence + Recovery (Part 2 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the durable-storage layer that makes `MicroVMSandbox` production-safe. Introduce an injectable **`S3Store`** (Seoul), **reroute not-booted file ops to S3** so early Discovery runs with NO live MicroVM (true laziness), **sync `aiplc-docs/` + prototype source to S3 after every turn**, and add **crash/expiry recovery** — boot fresh + restore the workspace from S3 and let the methodology's `session-continuity` rule resume itself (no backend resume logic). This closes the Part 1 caveat ("a MicroVM expiry/crash loses in-flight workspace state; not production-safe"). `MicroVMSandbox` still passes the shared `sandbox_contract`; routes/parsers do NOT change.

**Architecture:** Part 1 gave us three injectable seams (`HarnessClient`, `MicroVMController`, `MicroVMSandbox`) unit-testable without AWS. Part 2 adds a fourth — **`S3Store`** (a small async blob store: `get`/`put`/`list`), injected into `MicroVMSandbox`. Two consequences ripple out from "durable S3 is the source of truth between turns": (1) `read_file`/`write_file`/`list_files` become **pure S3 operations** (they no longer boot the VM); the harness's file methods survive only as *internal* restore/sync primitives; (2) `_ensure_ready` gains a **status-refresh** step (`controller.status(handle)`) so an auto-suspended VM is resumed and a terminated/expired VM is re-booted-and-restored — fixing the Part 1 dead resume branch (Finding A). A **`FakeS3Store`** (in-memory) covers `MicroVMSandbox` logic in CI; **moto** covers the real `S3Store`'s boto3 wiring; the `FakeMicroVMController` gains **simulated auto-suspend/expiry** so recovery is unit-testable without AWS.

**Tech Stack:** Python 3.11 (`str | None`, `from __future__ import annotations`), FastAPI/Starlette, Pydantic v2, `httpx`, `sse-starlette`, pytest + pytest-asyncio (auto mode). **New dependencies (justified in Task 1):** `boto3` promoted to a declared runtime dependency (already importable at 1.34.34 but undeclared), and `moto[s3]` added as a dev dependency so the real `S3Store`'s data-plane calls are exercised in CI (unlike the control-plane `LambdaMicroVMController`, which genuinely cannot be unit-tested and stays `NotImplementedError` until the AWS drill).

## Scope — this is Part 2 of a 2-part split

Part 1 (`docs/superpowers/plans/2026-07-17-pathfinder-microvm-sandbox.md`) delivered the compute relay: `MicroVMSandbox` boots a Claude Code MicroVM, injects `aiplc-rules`, relays a real turn over the harness, serializes turns, and swaps into `make_sandbox` behind `PATHFINDER_SANDBOX=microvm` — verified against the shared `Sandbox` contract. It has **no durable storage**, so a MicroVM expiry (max 8h) or crash loses in-flight workspace state. Part 1 is a verifiable engineering milestone, **not** production-safe; do not run real customer workshops on it.

**Part 2 (this plan) — durable persistence + recovery.** It is drafted **whole** (not split further): persistence and recovery are co-dependent — recovery needs the S3 store that persistence provides, and the status-refresh/reconcile path needs the "S3-newer" writes that lazy-S3 file ops produce. Splitting them would ship "persistence without recovery," which is exactly Part 1's not-production-safe trap. If execution proves too large, the natural internal seam is "does the VM lifecycle change?" — Tasks 1–4 (storage + lazy file ops + post-turn sync) vs Tasks 5–6 (status-refresh + reconcile + recovery) — but keep them in one branch so production-safety lands atomically.

Confirmed Part-2 scope (from Part 1's Scope section + design §4/§6), each mapped to a task below:
- `S3Store` — injectable S3 client abstraction (fake for unit tests, moto for the real client, real S3 in prod). → **Task 1**
- `input_holder` hint lifted onto the `Sandbox` ABC (Finding B) before any route consumes it polymorphically. → **Task 2**
- Reroute **not-booted** file ops to S3 (true laziness): a project's `aiplc-docs` is read/written with no live MicroVM; the VM boots only when `send_message`/build needs it. → **Task 3**
- Sync `aiplc-docs/` + prototype source to S3 **after every turn** (the Part 1 "Part 2 hook"). → **Task 4**
- Status-refresh + resume + reconcile (Finding A): `_ensure_ready` refreshes VM status, resumes an auto-suspended VM, re-pushes S3-newer files after resume; `FakeMicroVMController` simulates auto-suspend/expiry. → **Task 5**
- Recovery on expiry (max 8h)/failure: boot fresh + full restore from S3 + let the `session-continuity` rule resume itself (no backend resume logic). → **Task 6**
- `make_sandbox` wiring (inject `S3Store`), full-suite green, cross-region governance note. → **Task 7**
- Integration drills (AWS-required, scripted manual, NOT pytest): real S3 round-trip + a recovery drill (kill a VM mid-session, confirm restore + self-resume). → **Task 8**

## Global Constraints

Binding project-wide rules. Every task implicitly includes these. (Carried from Part 1; Part-2-specific additions marked.)

- **The `Sandbox` ABC (`backend/pathfinder/sandbox/base.py`) is the fixed boundary.** `MicroVMSandbox` implements it exactly — `async start()`, `async read_file(rel_path: str) -> str`, `async write_file(rel_path: str, content: str) -> None`, `async list_files(glob: str) -> list[str]`, `send_message(text: str) -> AsyncIterator[AgentEvent]` (async-generator function, like `LocalSandbox`), `async stop()`. **Part 2 adds one member to the ABC** — a `set_input_holder`/`input_holder` hint with a concrete default (Task 2, Finding B). Routes and parsers do NOT change; `MicroVMSandbox` must still pass the shared `sandbox_contract` after Part 2 changes.
- **No path may escape the workspace root.** `reject_unsafe` (Part 1, `pathsafe.py`) still applies to **every** path/glob — including the S3-routed file ops Part 2 introduces — before it is used to build an S3 key or a harness path. Rejection raises `ValueError`.
- **Never log, persist, or echo credential-shaped strings.** The route seam (`turns.py`) redacts `AgentEvent.text` via `redact_credentials`; the audit parser (`parsers/audit.py`) redacts on read. Part 2's S3 sync must not defeat that. **OPEN DESIGN QUESTION (Task 4):** `audit.md` is redacted *at the route seam on read*, but the **raw** file synced to S3 contains whatever the agent wrote — decide whether S3-stored audit needs redaction-at-rest or is acceptable as source-of-truth. Flagged in Open Questions with a recommendation.
- **Auth is IAM-role only.** Bedrock via the MicroVM execution role (`CLAUDE_CODE_USE_BEDROCK`); the S3Store uses the EC2/host IAM role or the injected client — **no long-lived keys** anywhere. Harness endpoint auth uses a service-minted JWE token; Part 2's resume path mints a fresh token on resume (decision documented in Task 5).
- **No methodology/resume LOGIC in the backend.** Recovery = copy files (S3 → fresh VM) + boot; the `session-continuity` rule (which reads `aiplc-state.md`) resumes *itself*. `MicroVMSandbox` contains no stage lists, no question wording, and no resume state machine.
- **Cross-region data governance (Part 2, design §6).** Durable storage (`S3`, `DynamoDB`) is **Seoul (`ap-northeast-2`)**; MicroVMs run in **Tokyo (`ap-northeast-1`)** because the Lambda MicroVMs service is absent in Seoul (confirmed Part 1). Customer documents are therefore *processed* transiently in Tokyo and *persisted* in Seoul — this must be disclosed to customers at workshop start. Carried into Task 7 (config) and Task 8 (drill note).
- **Python 3.11**, `str | None` unions, `from __future__ import annotations` at the top of every module.
- **Unit-testable without AWS.** `FakeS3Store` + `FakeMicroVMController` (with simulated auto-suspend/expiry) cover all logic in CI; the real `S3Store` is covered by **moto**; the AWS-only drills (Task 8) are scripted manual, clearly labeled, and NOT collected as pytest.
- **Concurrency unchanged.** Single Claude Code session per project; turns serialized; the soft "busy" signal from Part 1 is unchanged (design §4 does not alter it). Noted here so it is a conscious non-change.

---

## File Structure

```
backend/
  pathfinder/
    sandbox/
      base.py                # MODIFY: add set_input_holder()/input_holder hint to the Sandbox ABC (concrete default) — Finding B
      s3store.py             # NEW: S3StoreLike Protocol + S3Store (boto3, Seoul) — the durable blob store
      microvm.py             # MODIFY: inject S3Store; file ops -> S3 (lazy); _ensure_ready status-refresh + reconcile + restore; post-turn sync; drop bespoke input_holder (inherits ABC)
      microvm_control.py     # MODIFY: VMStatus += "expired"; FakeMicroVMController gains simulate_auto_suspend()/simulate_expiry() (Finding A (b))
      microvm_control_aws.py # unchanged in unit scope; JWE mint-on-resume wiring noted for Task 8
      harness.py             # unchanged (its file methods become internal restore/sync primitives)
      local.py               # unchanged (inherits the ABC input_holder default)
      pathsafe.py            # unchanged (reused for S3-routed ops)
    app.py                   # MODIFY: make_sandbox injects an S3Store via a monkeypatchable s3_store_factory
    pyproject.toml           # MODIFY: declare boto3 (runtime); add moto[s3] (dev)  [file: backend/pyproject.toml]
  tests/
    fakes/
      in_memory_s3.py        # NEW: FakeS3Store — in-memory S3StoreLike for MicroVMSandbox tests
    sandbox_contract.py      # unchanged (both sandboxes still pass it; MicroVMSandbox now needs an S3Store at construction)
    test_s3store.py          # NEW: FakeS3Store behavior + real S3Store against moto (data-plane wiring)
    test_input_holder.py     # NEW: ABC default hint present on BOTH LocalSandbox and MicroVMSandbox (Finding B)
    test_microvm_persistence.py # NEW: lazy S3 file ops (no boot), post-turn sync, reconcile
    test_microvm_recovery.py    # NEW: status-refresh resume, auto-suspend, expiry -> reboot + restore (Finding A + recovery)
    test_microvm_sandbox.py  # MODIFY: update Part-1 tests whose semantics change (file ops no longer boot)
    test_sandbox_contract.py # MODIFY: pass an S3Store when constructing MicroVMSandbox
    test_make_sandbox.py     # MODIFY: inject FakeS3Store via s3_store_factory monkeypatch
```

Rationale: `S3Store` is a fourth seam (durable data-plane) separate from the three Part-1 seams because it changes for its own reason (storage backend) and has its own fake + moto test. File-op rerouting, post-turn sync, and the status-refresh/recovery path all live in `microvm.py` because they are one cohesive lifecycle change, but they are split across Tasks 3–6 so each lands as working, separately-tested software.

---

### Task 1: `S3Store` durable blob store + fake + moto-backed real-client test

**Files:**
- Create: `backend/pathfinder/sandbox/s3store.py`
- Create: `backend/tests/fakes/in_memory_s3.py`
- Test: `backend/tests/test_s3store.py`
- Modify: `backend/pyproject.toml`

**Dependency justification (why boto3 + moto arrive now):** Part 1 explicitly deferred boto3/moto because it touched no durable storage. Part 2 does. `boto3` is already importable in this environment (1.34.34) but is **not** declared in `pyproject.toml` — relying on an undeclared transitive is a latent break, so Part 2 promotes it to a first-class runtime dependency. `moto[s3]` is added as a **dev** dependency because — unlike the Lambda-MicroVMs control plane, which has no local emulator and stays `NotImplementedError` until the AWS drill — S3's data plane (`get_object`/`put_object`/`list_objects_v2`) is faithfully emulated by moto, so the real `S3Store`'s boto3 wiring (key layout, encoding, 404→`FileNotFoundError`, pagination) can and should be exercised in CI. The `FakeS3Store` still exists for fast `MicroVMSandbox` logic tests; moto is only for `S3Store` itself.

**Interfaces:**
- Produces:
  - `S3StoreLike` (Protocol): `async def get(self, key: str) -> str` (raises `FileNotFoundError` on a missing key), `async def put(self, key: str, content: str) -> None`, `async def list(self, prefix: str) -> list[str]` (returns keys under `prefix`, sorted). Text in/out (UTF-8); the store is a thin blob layer, path-safety and key composition are the caller's job.
  - `S3Store(bucket: str, prefix: str, client)` — real implementation over an injected boto3 S3 client (sync boto3 calls wrapped with `asyncio.to_thread`, so the async surface holds without an async AWS SDK). `prefix` namespaces a project (e.g. `projects/<project_id>/`); keys passed to `get/put/list` are workspace-relative (`aiplc-docs/audit.md`) and joined under `bucket/prefix`. `get` maps `botocore` `NoSuchKey`/404 to `FileNotFoundError`. `list(prefix)` paginates `list_objects_v2` and returns workspace-relative keys (prefix stripped), sorted.
  - `FakeS3Store()` — in-memory `S3StoreLike` (a `dict[str, str]`) for `MicroVMSandbox` unit tests; same `FileNotFoundError`/sorted-list semantics as `S3Store`.

- [ ] **Step 1: Declare dependencies**

Edit `backend/pyproject.toml`:

```toml
[project]
name = "pathfinder"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27", "boto3>=1.34"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "moto[s3]>=5.0"]
```

Install the dev extra so moto is importable:

```bash
cd backend && pip install -e ".[dev]"
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_s3store.py
import boto3
import pytest
from moto import mock_aws
from pathfinder.sandbox.s3store import S3Store
from fakes.in_memory_s3 import FakeS3Store

# ---- FakeS3Store (the in-memory double used by MicroVMSandbox tests) ----

async def test_fake_put_get_roundtrip():
    s3 = FakeS3Store()
    await s3.put("aiplc-docs/audit.md", "hello")
    assert await s3.get("aiplc-docs/audit.md") == "hello"

async def test_fake_get_missing_raises_filenotfound():
    s3 = FakeS3Store()
    with pytest.raises(FileNotFoundError):
        await s3.get("aiplc-docs/nope.md")

async def test_fake_list_returns_sorted_keys_under_prefix():
    s3 = FakeS3Store()
    await s3.put("aiplc-docs/b.md", "1")
    await s3.put("aiplc-docs/a.md", "2")
    await s3.put("prototype/app.py", "3")
    assert await s3.list("aiplc-docs/") == ["aiplc-docs/a.md", "aiplc-docs/b.md"]

# ---- Real S3Store against moto (proves the boto3 data-plane wiring) ----

@mock_aws
def _make_bucket(name: str, region: str = "ap-northeast-2"):
    client = boto3.client("s3", region_name=region)
    client.create_bucket(
        Bucket=name,
        CreateBucketConfiguration={"LocationConstraint": region},
    )
    return client

async def test_s3store_put_get_roundtrip_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/audit.md", "안녕하세요")  # non-ASCII round-trips
        assert await store.get("aiplc-docs/audit.md") == "안녕하세요"

async def test_s3store_get_missing_raises_filenotfound_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        with pytest.raises(FileNotFoundError):
            await store.get("aiplc-docs/missing.md")

async def test_s3store_list_strips_prefix_and_sorts_moto():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        store = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        await store.put("aiplc-docs/b.md", "1")
        await store.put("aiplc-docs/a.md", "2")
        assert await store.list("aiplc-docs/") == ["aiplc-docs/a.md", "aiplc-docs/b.md"]

async def test_s3store_keys_are_namespaced_by_prefix_moto():
    # Two projects share a bucket but must not see each other's keys.
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-2")
        client.create_bucket(
            Bucket="pf-bucket",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        p1 = S3Store(bucket="pf-bucket", prefix="projects/p1/", client=client)
        p2 = S3Store(bucket="pf-bucket", prefix="projects/p2/", client=client)
        await p1.put("aiplc-docs/x.md", "one")
        assert await p2.list("aiplc-docs/") == []
        with pytest.raises(FileNotFoundError):
            await p2.get("aiplc-docs/x.md")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_s3store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder.sandbox.s3store'` (and `fakes.in_memory_s3`).

- [ ] **Step 4: Write the implementations**

```python
# backend/tests/fakes/in_memory_s3.py
from __future__ import annotations

class FakeS3Store:
    """In-memory S3StoreLike for MicroVMSandbox unit tests (no boto3, no AWS).

    Same contract as S3Store: text in/out, get() raises FileNotFoundError on a
    missing key, list(prefix) returns sorted workspace-relative keys.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, str] = {}

    async def get(self, key: str) -> str:
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    async def put(self, key: str, content: str) -> None:
        self.blobs[key] = content

    async def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.blobs if k.startswith(prefix))
```

```python
# backend/pathfinder/sandbox/s3store.py
from __future__ import annotations
import asyncio
from typing import Protocol

from botocore.exceptions import ClientError


class S3StoreLike(Protocol):
    async def get(self, key: str) -> str: ...
    async def put(self, key: str, content: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...


class S3Store:
    """Durable blob store over S3 (Seoul, ap-northeast-2). Thin: text in/out,
    workspace-relative keys namespaced under `prefix`. Path-safety and key
    composition are the caller's (MicroVMSandbox) job. boto3 is synchronous, so
    each call is wrapped in asyncio.to_thread to keep the async surface without
    an async AWS SDK. Auth is the host IAM role — no keys are held here.
    """

    def __init__(self, bucket: str, prefix: str, client) -> None:
        self._bucket = bucket
        self._prefix = prefix if prefix.endswith("/") or prefix == "" else prefix + "/"
        self._client = client

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> str:
        def _get() -> str:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from e
                raise
            return resp["Body"].read().decode("utf-8")

        return await asyncio.to_thread(_get)

    async def put(self, key: str, content: str) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._full_key(key),
                Body=content.encode("utf-8"),
            )

        await asyncio.to_thread(_put)

    async def list(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            full = self._full_key(prefix)
            paginator = self._client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=full):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"][len(self._prefix):])  # strip namespace
            return sorted(keys)

        return await asyncio.to_thread(_list)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_s3store.py -v`
Expected: PASS (7 tests). The moto cases prove the real boto3 data-plane wiring; the `FakeS3Store` cases lock the double to the same contract.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/sandbox/s3store.py backend/tests/fakes/in_memory_s3.py backend/tests/test_s3store.py backend/pyproject.toml
git commit -m "feat: S3Store durable blob store (boto3/moto), fake, and dependency declarations"
```

---

### Task 2: Lift the `input_holder` hint onto the `Sandbox` ABC (Finding B)

**Files:**
- Modify: `backend/pathfinder/sandbox/base.py`
- Modify: `backend/pathfinder/sandbox/microvm.py` (drop the bespoke copy — inherit from the ABC)
- Test: `backend/tests/test_input_holder.py`

**Finding B (resolved here):** Part 1's `MicroVMSandbox` defines `set_input_holder()`/`input_holder`, but they are **not** on the `Sandbox` ABC (`base.py`) nor on `LocalSandbox`. Inert today (no route calls them), so parity is not yet broken — but the moment a Part 2 route consumes the hint polymorphically off `ws.sandbox`, `LocalSandbox` raises `AttributeError`. This task removes the asymmetry **before** anything consumes it: add the hint to the ABC with a **concrete no-op default** (so `LocalSandbox` inherits it for free and `MicroVMSandbox` drops its duplicate). This is a pure lift — no behavior changes, the hint stays a soft, unenforced marker (design §4 "input-holder": who currently holds the input turn in a facilitated session).

Why the ABC and not a route helper: the hint is *per-sandbox state*, and routes already reach it only through the `Sandbox` interface (`ws.sandbox`). Putting a default on the ABC is the minimal change that makes every implementation polymorphically safe. The `send_message` busy-signal (Part 1) is unchanged — the input-holder hint is orthogonal advisory metadata, not turn serialization.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_input_holder.py
from pathlib import Path
import pytest
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _microvm():
    harness = FakeHarness()
    return MicroVMSandbox(
        project_id="p1",
        controller=FakeMicroVMController(base_url="http://fake-vm"),
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=FakeS3Store(),
    )

async def test_local_sandbox_inherits_input_holder_default(tmp_path: Path):
    # The Finding-B fix: LocalSandbox must NOT raise AttributeError when a
    # route touches the hint polymorphically.
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    assert sb.input_holder is None          # concrete default from the ABC
    sb.set_input_holder("facilitator-1")
    assert sb.input_holder == "facilitator-1"

async def test_microvm_sandbox_still_supports_input_holder():
    sb = _microvm()
    await sb.start()
    assert sb.input_holder is None
    sb.set_input_holder("customer-pm")
    assert sb.input_holder == "customer-pm"

def test_both_share_one_definition():
    # The hint is defined once on the ABC; subclasses do not shadow it.
    from pathfinder.sandbox.base import Sandbox
    assert "set_input_holder" in vars(Sandbox)
    assert "set_input_holder" not in vars(MicroVMSandbox)   # inherited, not duplicated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_input_holder.py -v`
Expected: FAIL — `AttributeError: 'LocalSandbox' object has no attribute 'input_holder'` (and `test_both_share_one_definition` fails: `set_input_holder` is still in `vars(MicroVMSandbox)` and absent from `vars(Sandbox)`). Note this test also fails to import until Task 3 adds the `s3=` parameter — sequence Task 2's ABC edit and Task 3's constructor change together if executing strictly, or gate this test's MicroVM cases behind Task 3. (The `LocalSandbox` case and the ABC edit stand alone.)

- [ ] **Step 3: Add the hint to the ABC**

```python
# backend/pathfinder/sandbox/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncIterator, Literal
from pydantic import BaseModel

class AgentEvent(BaseModel):
    kind: Literal["message", "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None

class TurnResult(BaseModel):
    events: list[AgentEvent]

class Sandbox(ABC):
    # Soft "current input holder" hint (design §4): advisory metadata about who
    # holds the input turn in a facilitated session. Concrete no-op default so
    # every implementation (LocalSandbox, MicroVMSandbox) is polymorphically
    # safe — a route may read/set it off any Sandbox without AttributeError.
    # NOT enforcement and NOT turn serialization (that is send_message's busy
    # signal); purely advisory.
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
    async def stop(self) -> None: ...
```

- [ ] **Step 4: Drop `MicroVMSandbox`'s duplicate**

In `backend/pathfinder/sandbox/microvm.py`, remove the bespoke `set_input_holder` method and the `self.input_holder: str | None = None` line from `__init__` — both are now inherited from the ABC. (The class-level `input_holder: str | None = None` on the ABC provides the default; per-instance `set_input_holder` writes an instance attribute, so nothing else changes.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_input_holder.py tests/test_microvm_sandbox.py -v`
Expected: PASS. Part 1's `test_input_holder_hint_is_settable` (in `test_microvm_sandbox.py`) still passes unchanged — the behavior is identical, only its definition moved to the ABC.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/sandbox/base.py backend/pathfinder/sandbox/microvm.py backend/tests/test_input_holder.py
git commit -m "feat: lift input_holder hint onto Sandbox ABC with no-op default (Finding B)"
```

---

### Task 3: Reroute not-booted file ops to S3 (true laziness)

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm.py` (add `s3` param; file ops → S3)
- Modify: `backend/tests/test_microvm_sandbox.py` (update Part-1 tests whose semantics change)
- Modify: `backend/tests/test_sandbox_contract.py` (pass an `S3Store` when building `MicroVMSandbox`)
- Modify: `backend/tests/test_make_sandbox.py` (its microvm case constructs the sandbox — will be finished in Task 7; keep it importing)
- Test: `backend/tests/test_microvm_persistence.py` (new — lazy S3 file ops)

**Design decision — file ops are ALWAYS S3, not just "when not booted":** The Part-1 Scope says "use S3 when `_handle is None`." Part 2 **generalizes this to unconditional S3** for `read_file`/`write_file`/`list_files`, because a VM-only write is the exact Part-1 data-loss trap: if a route wrote an `[Answer]` into the booted VM's filesystem and the VM then **expired** (terminated after 8h) before a post-turn sync, that write is lost. Routing every file op through S3 makes S3 the durable source of truth at all times, which (a) satisfies and strengthens "no live VM needed for early Discovery" (the not-booted case is simply the one that proves laziness), and (b) makes the reconcile step well-defined — "push S3-newer files into the resumed VM" (Task 5) has a single authoritative side. The harness's `read_file`/`write_file`/`list_files` do **not** disappear; they become **internal primitives** used only by restore (S3 → VM, Task 5/6) and post-turn sync (VM → S3, Task 4). This holds consistency given the two invariants Part 1 already guarantees: turns are serialized, and file-as-contract reads happen *between* turns (routes read after a `done` event) — and Task 4 syncs VM → S3 on `done`, so S3 is current whenever a route reads.

**Constructor change:** `MicroVMSandbox.__init__` gains `s3: S3StoreLike` (injected, like `controller`/`harness_factory`). This ripples to every construction site (contract test, make_sandbox test, sandbox tests) — all pass `FakeS3Store()`.

**Interfaces (updated `MicroVMSandbox`):**
- `MicroVMSandbox(project_id: str, controller: MicroVMController, spec: BootSpec, harness_factory: Callable[[VMHandle], HarnessLike], s3: S3StoreLike)`.
- `read_file(rel_path) -> str` — `reject_unsafe(rel_path)`, then `await self._s3.get(rel_path)` (raises `FileNotFoundError` on miss, same as `LocalSandbox`/harness). **No boot.**
- `write_file(rel_path, content) -> None` — `reject_unsafe(rel_path)`, then `await self._s3.put(rel_path, content)`. **No boot.**
- `list_files(glob) -> list[str]` — `reject_unsafe(glob)`, derive the static S3 prefix from the glob, `await self._s3.list(prefix)`, then `fnmatch`-filter by the full glob (mirrors the Part-1 FakeHarness/harness `fnmatch` semantics so results match what the harness returned). **No boot.**
- `send_message`, `_ensure_ready`, `stop` unchanged in this task (Task 4 adds post-turn sync; Task 5 rewrites `_ensure_ready`).

- [ ] **Step 1: Write the failing tests (new persistence behavior)**

```python
# backend/tests/test_microvm_persistence.py
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_write_then_read_uses_s3_without_booting():
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "entry")
    assert await sb.read_file("aiplc-docs/audit.md") == "entry"
    assert ctrl.boot_calls == 0            # true laziness: NO VM for file ops
    assert s3.blobs["aiplc-docs/audit.md"] == "entry"   # landed in durable S3
    assert harness.files == {}             # harness NOT touched by file ops

async def test_list_files_globs_over_s3_without_booting():
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")   # must not match
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]
    assert ctrl.boot_calls == 0

async def test_read_missing_from_s3_raises_filenotfound():
    sb, _, _, _ = _sandbox()
    await sb.start()
    with pytest.raises(FileNotFoundError):
        await sb.read_file("aiplc-docs/missing.md")

async def test_path_safety_runs_before_s3():
    sb, ctrl, _, s3 = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.read_file("/etc/passwd")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert s3.blobs == {}                  # nothing written past the guard
    assert ctrl.boot_calls == 0
```

- [ ] **Step 2: Update the Part-1 tests whose semantics change**

Two `test_microvm_sandbox.py` cases asserted the *old* "file ops boot the VM" behavior and must be updated to the new "file ops never boot" contract. Also thread `s3=FakeS3Store()` through `_sandbox()`.

```python
# backend/tests/test_microvm_sandbox.py  (updated _sandbox helper + changed cases)
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    sb = MicroVMSandbox(
        project_id="p1",
        controller=ctrl,
        spec=BootSpec(),
        harness_factory=lambda handle: harness,
        s3=FakeS3Store(),
    )
    return sb, ctrl, harness

async def test_start_does_not_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    assert ctrl.boot_calls == 0
    assert sb._handle is None

async def test_file_ops_do_not_boot():          # was test_first_file_op_boots_once_and_reuses
    sb, ctrl, _ = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert await sb.read_file("aiplc-docs/x.md") == "hi"
    assert ctrl.boot_calls == 0                  # file ops are pure S3 now

async def test_path_safety_rejected_before_boot():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")
    with pytest.raises(ValueError):
        await sb.list_files("../*")
    assert ctrl.boot_calls == 0

async def test_send_message_relays_ordered_events():
    sb, _, _ = _sandbox()
    await sb.start()
    events = [e async for e in sb.send_message("승인")]
    assert [e.kind for e in events] == ["message", "done"]
    assert "승인" in events[0].text

async def test_send_message_boots_the_vm():      # NEW: a turn IS what boots
    sb, ctrl, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert ctrl.boot_calls == 1

async def test_concurrent_turn_gets_busy_signal():
    sb, _, _ = _sandbox()
    await sb.start()
    sb._turn_active = True
    events = [e async for e in sb.send_message("second")]
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "in progress" in events[0].text

async def test_stop_resets_to_not_booted():
    sb, ctrl, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("go")]   # boot via a turn (file ops no longer boot)
    await sb.stop()
    assert ctrl.stop_calls == 1
    assert sb._handle is None
```

(Part 1's `test_input_holder_hint_is_settable` stays as-is — still passes via the inherited ABC hint from Task 2.)

- [ ] **Step 3: Thread `s3=` through the contract test**

```python
# backend/tests/test_sandbox_contract.py  (MicroVMSandbox case, updated)
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store
from sandbox_contract import run_sandbox_contract

async def test_microvm_sandbox_satisfies_same_contract():
    sb = MicroVMSandbox(
        project_id="p1",
        controller=FakeMicroVMController(base_url="http://fake-vm"),
        spec=BootSpec(),
        harness_factory=lambda handle: FakeHarness(),
        s3=FakeS3Store(),
    )
    await sb.start()
    await run_sandbox_contract(sb)       # SAME assertions LocalSandbox passes
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_microvm_persistence.py tests/test_microvm_sandbox.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 's3'`.

- [ ] **Step 5: Rewrite `microvm.py` (constructor + S3-routed file ops)**

```python
# backend/pathfinder/sandbox/microvm.py
from __future__ import annotations
import asyncio
import fnmatch
from pathlib import PurePosixPath
from typing import AsyncIterator, Callable, Protocol
from pathfinder.sandbox.base import Sandbox, AgentEvent
from pathfinder.sandbox.pathsafe import reject_unsafe
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle
from pathfinder.sandbox.s3store import S3StoreLike


class HarnessLike(Protocol):
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def read_file(self, rel_path: str) -> str: ...
    async def write_file(self, rel_path: str, content: str) -> None: ...
    async def list_files(self, glob: str) -> list[str]: ...
    async def heartbeat(self) -> bool: ...


def _glob_prefix(glob: str) -> str:
    """The leading static (wildcard-free) directory portion of a glob, used as
    the S3 list prefix. e.g. 'aiplc-docs/**/*-questions.md' -> 'aiplc-docs/',
    'aiplc-docs/audit.md' -> 'aiplc-docs/audit.md', '*.md' -> ''."""
    parts = PurePosixPath(glob).parts
    static: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    prefix = "/".join(static)
    if not static:                       # glob starts with a wildcard, e.g. '*.md'
        return ""
    if len(static) == len(parts):        # no wildcard at all: a literal path key
        return prefix
    return prefix + "/"                   # static leading dirs before a wildcard


class MicroVMSandbox(Sandbox):
    """Real sandbox: boots a Claude Code MicroVM (aiplc-rules baked into the
    image) for turns, and uses a durable S3 store as the source of truth for
    all file-as-contract ops. File ops NEVER boot the VM (true laziness): a
    project's aiplc-docs is read/written against S3 with no live MicroVM. The
    VM boots only for send_message (a turn). After each turn the workspace is
    synced VM -> S3 (Task 4); on resume/recovery S3-newer files are pushed
    S3 -> VM (Tasks 5/6). No methodology/resume logic lives here — the
    session-continuity rule resumes itself by reading aiplc-state.md.
    """

    def __init__(
        self,
        project_id: str,
        controller: MicroVMController,
        spec: BootSpec,
        harness_factory: Callable[[VMHandle], HarnessLike],
        s3: S3StoreLike,
    ):
        self.project_id = project_id
        self._controller = controller
        self._spec = spec
        self._harness_factory = harness_factory
        self._s3 = s3
        self._handle: VMHandle | None = None
        self._harness: HarnessLike | None = None
        self._boot_lock = asyncio.Lock()
        self._turn_active = False

    async def start(self) -> None:
        # Lazy: do NOT boot. "Not yet booted" == self._handle is None.
        self._handle = None
        self._harness = None

    # ---- file-as-contract ops: ALWAYS durable S3, never boot ----

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        return await self._s3.get(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        await self._s3.put(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if fnmatch.fnmatch(k, glob))

    # ---- turn relay: boots the VM (Task 4 adds post-turn sync) ----

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

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            async for event in harness.send_message(text):
                yield event
            # Part 2 hook (Task 4): after the terminal event, sync workspace -> S3.
        finally:
            self._turn_active = False

    async def stop(self) -> None:
        if self._handle is not None:
            await self._controller.stop(self._handle)
        self._handle = None
        self._harness = None
```

> **Note on `_glob_prefix`:** keep it minimal and covered by the tests above (`aiplc-docs/*-questions.md` → prefix `aiplc-docs/`, `aiplc-docs/**/*` → `aiplc-docs/`). If the derivation proves fiddly under TDD, the safe fallback is to `await self._s3.list("")` (list all keys for the project — a project's key-space is small) and `fnmatch`-filter; the prefix is only a listing optimization, correctness comes from the `fnmatch` filter. Choose whichever passes `test_list_files_globs_over_s3_without_booting` cleanly.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_microvm_persistence.py tests/test_microvm_sandbox.py tests/test_sandbox_contract.py tests/test_input_holder.py -v`
Expected: PASS. `test_microvm_sandbox_satisfies_same_contract` still green — `MicroVMSandbox` remains a drop-in for `LocalSandbox` against the fixed `Sandbox` boundary, now with S3-backed file ops.

- [ ] **Step 7: Commit**

```bash
git add backend/pathfinder/sandbox/microvm.py backend/tests/test_microvm_persistence.py backend/tests/test_microvm_sandbox.py backend/tests/test_sandbox_contract.py
git commit -m "feat: route MicroVMSandbox file ops through durable S3 (true laziness, no boot)"
```

---

### Task 4: Sync `aiplc-docs/` + prototype source to S3 after every turn

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm.py` (fill the "Part 2 hook" — post-turn VM → S3 sync)
- Test: `backend/tests/test_microvm_persistence.py` (append sync cases)

**Why this is needed even though file ops are S3 (Task 3):** Route-driven file writes land in S3 directly (Task 3), but the **agent** writes files *inside the VM* during a turn — it runs Claude Code, which creates/edits `aiplc-docs/**` (state, audit, questions, discovery doc) and prototype source on the VM's own filesystem, not through our `write_file`. Those live only in the VM until we pull them out. Design §4: "the harness syncs `aiplc-docs/` + prototype source to S3 at the end of every agent turn." This task implements exactly that at the Part-1 hook location (after the terminal event, in `send_message`). After the sync, S3 holds everything the turn produced, so the very next route read (`get_state`/`get_audit`/`get_document`) sees current data — and an expiry/crash after this point loses nothing.

**Sync surface (the sync globs):** `aiplc-docs/**/*` (the whole methodology output subtree — matches `Workspace.list_artifacts`) plus `prototype/**/*` (prototype source). Enumerate via `harness.list_files(glob)` (the harness lists the VM's real filesystem), read each with `harness.read_file`, and `s3.put` it. Only these two subtrees are synced — never the whole VM FS (no `node_modules`, no rules, no secrets). The sync is best-effort-ordered but must complete before `send_message` returns its generator's `StopAsyncIteration`, so a route that reads after consuming the stream sees synced data.

**Interface addition:**
- `async def _sync_workspace_to_s3(self, harness: HarnessLike) -> None` — for each sync glob, `for key in await harness.list_files(glob): await self._s3.put(key, await harness.read_file(key))`. Called in `send_message`'s `finally`/post-loop, after the terminal event, before `_turn_active` is cleared is fine but simplest is: right after the `async for` loop completes (still inside `try`). Credentials: this copies **raw** file bytes; see the redaction-at-rest Open Question — this task keeps raw bytes (source-of-truth) and flags the decision rather than silently redacting.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_microvm_persistence.py`:

```python
from pathfinder.sandbox.base import AgentEvent

def _sandbox_with_agent_writes(files_written: dict[str, str]):
    """A FakeHarness whose 'turn' writes files into the VM FS (like Claude Code
    does), so we can assert the post-turn sync pulls them into S3."""
    harness = FakeHarness()

    async def _turn(text: str):
        for k, v in files_written.items():
            harness.files[k] = v            # agent writes to the VM FS
        yield AgentEvent(kind="message", text="worked")
        yield AgentEvent(kind="done")

    harness._events_for = None
    harness.send_message = _turn            # override the canned echo turn
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1", controller=ctrl, spec=BootSpec(),
        harness_factory=lambda handle: harness, s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_turn_syncs_agent_written_files_to_s3():
    sb, _, _, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/aiplc-state.md": "stage: Discovery",
        "aiplc-docs/audit.md": "entry 1",
        "prototype/app.py": "print('hi')",
    })
    await sb.start()
    _ = [e async for e in sb.send_message("start ai-plc")]
    # After the turn, S3 (durable) holds what the agent wrote in the VM.
    assert s3.blobs["aiplc-docs/aiplc-state.md"] == "stage: Discovery"
    assert s3.blobs["aiplc-docs/audit.md"] == "entry 1"
    assert s3.blobs["prototype/app.py"] == "print('hi')"

async def test_route_read_after_turn_sees_synced_state():
    sb, _, _, _ = _sandbox_with_agent_writes({"aiplc-docs/aiplc-state.md": "stage: Envision"})
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    # read_file goes to S3 (Task 3); it must reflect the just-synced turn output.
    assert await sb.read_file("aiplc-docs/aiplc-state.md") == "stage: Envision"

async def test_only_sync_subtrees_are_pushed():
    sb, _, harness, s3 = _sandbox_with_agent_writes({
        "aiplc-docs/audit.md": "keep",
        "node_modules/pkg/index.js": "DROP",   # outside the sync globs
    })
    await sb.start()
    _ = [e async for e in sb.send_message("go")]
    assert "aiplc-docs/audit.md" in s3.blobs
    assert "node_modules/pkg/index.js" not in s3.blobs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_microvm_persistence.py -v -k "sync or after"`
Expected: FAIL — `KeyError: 'aiplc-docs/aiplc-state.md'` in `s3.blobs` (nothing synced yet).

- [ ] **Step 3: Implement the post-turn sync**

In `backend/pathfinder/sandbox/microvm.py`, add the sync globs and method, and call it in `send_message`:

```python
    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*")

    async def _sync_workspace_to_s3(self, harness: HarnessLike) -> None:
        """Pull the turn's output out of the VM FS into durable S3. Only the
        methodology output + prototype source subtrees (never the whole FS).
        Raw bytes are stored (source-of-truth); see the redaction-at-rest
        Open Question."""
        for glob in self._SYNC_GLOBS:
            for key in await harness.list_files(glob):
                content = await harness.read_file(key)
                await self._s3.put(key, content)

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            async for event in harness.send_message(text):
                yield event
            # Durable persistence: after the turn's terminal event, sync the
            # workspace out of the VM into S3 so expiry/crash loses nothing and
            # the next route read sees current data.
            await self._sync_workspace_to_s3(harness)
        finally:
            self._turn_active = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_microvm_persistence.py -v`
Expected: PASS (all persistence cases, incl. Task 3's). The contract test's `assert_send_message_ordered_and_terminates` still passes — the sync happens after the last yielded event, so the event stream shape is unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/microvm.py backend/tests/test_microvm_persistence.py
git commit -m "feat: sync aiplc-docs + prototype source to S3 after every turn"
```

---

### Task 5: Status-refresh + resume + reconcile; simulated auto-suspend/expiry (Finding A)

> **This is Finding A — the linchpin task.** Part 1's `_ensure_ready` had a resume branch that is **currently unreachable**: nothing ever calls `controller.status()`, so the cached `VMHandle.status` is never refreshed, and nothing calls `suspend()`. When a real AWS MicroVM auto-suspends after `BootSpec.max_idle_seconds`, Part 1's `_ensure_ready` still believes the stale cached handle is `"ready"` and forwards ops to a dead/suspended endpoint. This task fixes it end-to-end.

**Files:**
- Modify: `backend/pathfinder/sandbox/microvm_control.py` (`VMStatus += "expired"`; `FakeMicroVMController.simulate_auto_suspend()`/`simulate_expiry()`)
- Modify: `backend/pathfinder/sandbox/microvm.py` (`_ensure_ready` status-refresh; `_restore_workspace_from_s3`; `_boot_and_restore`)
- Test: `backend/tests/test_microvm_recovery.py` (new — resume-on-auto-suspend + reconcile)
- Modify: `backend/tests/test_microvm_control.py` (add simulate-method cases)

**Finding A, resolved in three parts (matching the brief):**
- **(a) Refresh status before deciding.** `_ensure_ready` calls `controller.status(handle)` on the cached handle and branches on the *fresh* status, not the stale cached one: `ready` → use as-is; `suspended` → `resume` + reconcile; `expired`/`stopped` → the VM is gone, reboot fresh + full restore (Task 6's scenario, same `_boot_and_restore` path).
- **(b) Make it unit-testable without AWS.** `FakeMicroVMController` gains `simulate_auto_suspend(handle)` (control plane now reports `suspended` though the cached handle still says `ready` — the exact stale-handle condition) and `simulate_expiry(handle)` (reports `expired`; VM + FS gone). `VMStatus` gains `"expired"`.
- **(c) Tie into reconcile.** After `resume`, `_restore_workspace_from_s3` re-pushes the S3 sync-subtree files into the VM, so writes that landed in S3 while suspended (route `[Answer]` write-backs during early Discovery, Task 3) are not stale in the resumed VM. Because Task 3 made S3 the single source of truth, reconcile is well-defined: S3 unconditionally wins and the push is idempotent (no per-file timestamp bookkeeping needed — the project key-space is small).

**JWE harness auth-token decision (Part 1 Open Question #1, resolved here):** Part 1 recommended **mint-per-boot**. Part 2's resume path needs its own answer because a long suspend (e.g. a lunch break) can outlive a 30-min JWE token. **Decision: mint-on-resume.** The `harness_factory` is re-invoked with the (re)booted/(resumed) handle on every `_ensure_ready` transition that changes the handle (boot, resume, reboot) — so the token is freshly minted exactly when the endpoint changes or a suspend may have elapsed. This is strictly simpler than refresh-on-401 (no retry/refresh state machine in `HarnessClient`, which stays pure transport) and is correct because a token is only ever needed for an *active* turn, which always passes through `_ensure_ready` first. The real `CreateMicrovmAuthToken` call lives in `harness_factory` (wired in Task 7/8); the seam is already in place — `_ensure_ready` calling `self._harness_factory(self._handle)` on each transition *is* the mint-on-resume point.

**Interfaces:**
- `MicroVMController`/`VMStatus`: `VMStatus = Literal["booting", "ready", "suspended", "stopped", "expired"]`.
- `FakeMicroVMController.simulate_auto_suspend(self, handle: VMHandle) -> None` and `.simulate_expiry(self, handle: VMHandle) -> None` (sync test helpers that mutate the recorded status).
- `MicroVMSandbox._restore_workspace_from_s3(self, harness: HarnessLike) -> None` — for each sync-subtree prefix, `for key in await self._s3.list(prefix): await harness.write_file(key, await self._s3.get(key))`.
- `MicroVMSandbox._boot_and_restore(self) -> HarnessLike` — `boot`, build harness, `_restore_workspace_from_s3`, return the harness.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_microvm_recovery.py
import pytest
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController
from pathfinder.sandbox.base import AgentEvent
from fakes.in_memory_harness import FakeHarness
from fakes.in_memory_s3 import FakeS3Store

def _sandbox():
    harness = FakeHarness()
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    s3 = FakeS3Store()
    sb = MicroVMSandbox(
        project_id="p1", controller=ctrl, spec=BootSpec(),
        harness_factory=lambda handle: harness, s3=s3,
    )
    return sb, ctrl, harness, s3

async def test_ensure_ready_refreshes_status_not_trusting_stale_ready():
    # FINDING A: cached handle says "ready" but AWS auto-suspended it.
    sb, ctrl, harness, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("boot")]     # boots; caches handle="ready"
    assert ctrl.boot_calls == 1
    ctrl.simulate_auto_suspend(sb._handle)             # AWS suspends; cache stale
    assert sb._handle.status == "ready"                # cache is INDEED stale
    _ = [e async for e in sb.send_message("continue")] # must refresh -> resume
    assert ctrl.resume_calls == 1                      # resumed, not treated as ready
    assert ctrl.boot_calls == 1                        # NOT re-booted (only suspended)

async def test_resume_reconciles_s3_newer_writes_into_vm():
    # A write that landed in S3 while suspended must be pushed into the resumed VM.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("boot")]
    ctrl.simulate_auto_suspend(sb._handle)
    await sb.write_file("aiplc-docs/answer.md", "[Answer]: B")  # S3 only, VM stale
    assert "aiplc-docs/answer.md" not in harness.files
    _ = [e async for e in sb.send_message("continue")]          # resume -> reconcile
    assert harness.files["aiplc-docs/answer.md"] == "[Answer]: B"

async def test_ready_vm_is_reused_without_resume_or_reboot():
    sb, ctrl, _, _ = _sandbox()
    await sb.start()
    _ = [e async for e in sb.send_message("one")]
    _ = [e async for e in sb.send_message("two")]      # still ready between turns
    assert ctrl.boot_calls == 1
    assert ctrl.resume_calls == 0
```

```python
# append to backend/tests/test_microvm_control.py
from pathfinder.sandbox.microvm_control import VMHandle

async def test_simulate_auto_suspend_reports_suspended():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("p1", BootSpec())
    ctrl.simulate_auto_suspend(handle)
    assert await ctrl.status(handle) == "suspended"   # even though handle.status == "ready"
    assert handle.status == "ready"                   # the cached copy is stale by design

async def test_simulate_expiry_reports_expired():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("p1", BootSpec())
    ctrl.simulate_expiry(handle)
    assert await ctrl.status(handle) == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_microvm_recovery.py tests/test_microvm_control.py -v`
Expected: FAIL — `AttributeError: 'FakeMicroVMController' object has no attribute 'simulate_auto_suspend'`, and the recovery cases fail because `_ensure_ready` never calls `status()` (stale `"ready"` is trusted, `resume_calls == 0`).

- [ ] **Step 3: Extend `FakeMicroVMController` + `VMStatus`**

In `backend/pathfinder/sandbox/microvm_control.py`:

```python
VMStatus = Literal["booting", "ready", "suspended", "stopped", "expired"]
```

Add to `FakeMicroVMController` (test-only simulation helpers; sync — they just mutate recorded status):

```python
    def simulate_auto_suspend(self, handle: VMHandle) -> None:
        """Emulate AWS auto-suspend after max_idle_seconds: the control plane
        now reports 'suspended' while the caller's cached VMHandle still says
        'ready'. This is the exact stale-handle condition Finding A targets."""
        self._status[handle.vm_id] = "suspended"

    def simulate_expiry(self, handle: VMHandle) -> None:
        """Emulate MicroVM expiry (max 8h) / crash: control plane reports
        'expired'; the VM and its filesystem are gone."""
        self._status[handle.vm_id] = "expired"
```

- [ ] **Step 4: Rewrite `_ensure_ready` with status-refresh + reconcile + restore-on-boot**

In `backend/pathfinder/sandbox/microvm.py`, replace `_ensure_ready` and add the two helpers:

```python
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/")

    async def _restore_workspace_from_s3(self, harness: HarnessLike) -> None:
        """Copy the durable workspace (S3 = source of truth) into the VM FS.
        Used to reconcile after resume (re-push writes that landed in S3 while
        suspended) AND to fully restore a freshly-booted VM after expiry/crash.
        S3 unconditionally wins; the push is idempotent. No methodology/resume
        logic here — we only copy files; the session-continuity rule reads
        aiplc-state.md and resumes itself once the VM is running."""
        for prefix in self._RESTORE_PREFIXES:
            for key in await self._s3.list(prefix):
                await harness.write_file(key, await self._s3.get(key))

    async def _boot_and_restore(self) -> HarnessLike:
        self._handle = await self._controller.boot(self.project_id, self._spec)
        self._harness = self._harness_factory(self._handle)   # mint-on-boot (JWE)
        await self._restore_workspace_from_s3(self._harness)
        return self._harness

    async def _ensure_ready(self) -> HarnessLike:
        async with self._boot_lock:
            if self._handle is None:
                return await self._boot_and_restore()
            # Finding A (a): refresh the LIVE status before trusting the cache.
            current = await self._controller.status(self._handle)
            if current == "ready":
                assert self._harness is not None
                return self._harness
            if current == "suspended":
                self._handle = await self._controller.resume(self._handle)
                self._harness = self._harness_factory(self._handle)   # mint-on-resume (JWE)
                await self._restore_workspace_from_s3(self._harness)  # (c) reconcile
                return self._harness
            # "expired"/"stopped": the VM (and its FS) are gone — reboot fresh
            # and fully restore from S3 (Task 6's recovery scenario).
            self._handle = None
            return await self._boot_and_restore()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_microvm_recovery.py tests/test_microvm_control.py tests/test_microvm_sandbox.py tests/test_microvm_persistence.py tests/test_sandbox_contract.py -v`
Expected: PASS. The linchpin `test_ensure_ready_refreshes_status_not_trusting_stale_ready` now goes green (was structurally impossible in Part 1). Part-1 reuse tests still pass — a `ready` VM between turns is reused with no `resume`/`boot`.

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/sandbox/microvm_control.py backend/pathfinder/sandbox/microvm.py backend/tests/test_microvm_recovery.py backend/tests/test_microvm_control.py
git commit -m "fix: refresh VM status before ready/resume decision + reconcile from S3 (Finding A)"
```

---

### Task 6: Recovery on expiry/failure — reboot + restore from S3 + rule self-resume

**Files:**
- Test: `backend/tests/test_microvm_recovery.py` (append the expiry/crash recovery scenario)
- Modify: `backend/pathfinder/sandbox/microvm.py` only if the tests surface a gap (the `_boot_and_restore` path from Task 5 should already cover it — this task proves the *end-to-end* recovery contract and the "no backend resume logic" invariant)

**What this task proves (design §4):** "MicroVM expiry (max 8h)/failure → boot a fresh MicroVM → restore the workspace from S3 → the methodology's `session-continuity` rule reads `aiplc-state.md` and resumes itself." Task 5 wired the mechanism (`expired`/`stopped` → `_boot_and_restore`); this task adds the **scenario-level** tests that (a) a mid-session expiry transparently recovers on the next turn with **full** workspace restoration, (b) the fresh VM starts from the restored `aiplc-state.md` (which the rule reads — the backend does NOT parse it or drive resume), and (c) a new `vm_id` is issued (proving it is genuinely a fresh VM, not the dead one). This is the task that discharges the Part-1 "not production-safe" caveat.

**Explicitly NO backend resume logic:** the assertions verify only that the files are present in the fresh VM and that the backend issued no methodology-specific calls. There is no code that reads `aiplc-state.md`, no stage machine, no "continue from stage N" — restoration is a blind file copy (`_restore_workspace_from_s3`), and self-resume is the rule's job once the agent runs in the fresh VM. A test asserts the sandbox exposes no such method, to lock the invariant.

- [ ] **Step 1: Write the failing/scenario tests**

Append to `backend/tests/test_microvm_recovery.py`:

```python
async def test_expiry_midsession_recovers_with_full_restore():
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    # A turn produced durable state (synced to S3 by Task 4).
    _ = [e async for e in sb.send_message("boot")]
    await sb.write_file("aiplc-docs/aiplc-state.md", "stage: Solution Analysis")
    await sb.write_file("aiplc-docs/audit.md", "40 entries")
    first_vm = sb._handle.vm_id
    # The VM expires (8h cap) or crashes.
    ctrl.simulate_expiry(sb._handle)
    # Next turn: transparent recovery — fresh boot + full restore from S3.
    _ = [e async for e in sb.send_message("계속 진행")]
    assert ctrl.boot_calls == 2                     # a NEW VM was booted
    assert sb._handle.vm_id != first_vm             # genuinely fresh, not the dead one
    # The fresh VM's FS was fully restored from durable S3:
    assert harness.files["aiplc-docs/aiplc-state.md"] == "stage: Solution Analysis"
    assert harness.files["aiplc-docs/audit.md"] == "40 entries"

async def test_recovery_restores_state_file_for_the_rule_to_resume():
    # The backend restores aiplc-state.md verbatim; the session-continuity RULE
    # (running in the fresh VM) reads it and resumes. Backend does NOT parse it.
    sb, ctrl, harness, s3 = _sandbox()
    await sb.start()
    await sb.write_file("aiplc-docs/aiplc-state.md", "stage: Envision\nnext: PR/FAQ")
    _ = [e async for e in sb.send_message("boot")]  # boot -> restore pushes state in
    assert harness.files["aiplc-docs/aiplc-state.md"] == "stage: Envision\nnext: PR/FAQ"

def test_backend_has_no_methodology_resume_logic():
    # Lock the "no resume logic in the backend" invariant: the sandbox exposes
    # no state-machine/resume entry points — recovery is a blind file copy.
    for forbidden in ("resume_from_state", "parse_state", "advance_stage", "_continue_session"):
        assert not hasattr(MicroVMSandbox, forbidden)
```

- [ ] **Step 2: Run tests**

Run: `cd backend && python -m pytest tests/test_microvm_recovery.py -v`
Expected: PASS if Task 5's `_boot_and_restore` + `expired` branch are correct. If `test_expiry_midsession_recovers_with_full_restore` fails (e.g. the fresh boot did not restore, or the dead `vm_id` was reused), fix `_ensure_ready`'s `expired`/`stopped` branch — it must set `self._handle = None` then `_boot_and_restore()` (issuing a new `vm_id`), not resume the dead handle. No new production code should be needed beyond Task 5; this task is the recovery *proof*.

- [ ] **Step 3: Full regression pass**

Run: `cd backend && python -m pytest -v`
Expected: PASS — all Phase 1 tests + Part 1 tests + Part 2 Tasks 1–6. Confirms recovery did not regress the contract or the compute-relay behavior.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_microvm_recovery.py
git commit -m "test: end-to-end MicroVM expiry recovery — fresh boot + full S3 restore + rule self-resume"
```

---

### Task 7: `make_sandbox` injects the `S3Store`; full-suite green; cross-region note

**Files:**
- Modify: `backend/pathfinder/app.py` (`make_sandbox` builds + injects an `S3Store`; add a monkeypatchable `s3_store_factory`)
- Modify: `backend/tests/test_make_sandbox.py` (inject `FakeS3Store` via the factory)

**Interfaces:**
- `app.s3_store_factory(project_id: str) -> S3StoreLike` — module-level hook (defaults to a real `S3Store` over a boto3 client in `PATHFINDER_S3_REGION`, default `ap-northeast-2` / Seoul, bucket `PATHFINDER_S3_BUCKET`, prefix `projects/<project_id>/`). Monkeypatched in tests to return `FakeS3Store()` so no AWS is touched — mirrors the Part-1 `microvm_controller_factory` pattern.
- `make_sandbox(project_id) -> Sandbox` — **signature unchanged**. The microvm branch now also builds `s3 = s3_store_factory(project_id)` and passes `s3=s3` to `MicroVMSandbox`. The local branch is byte-for-byte unchanged (`LocalSandbox` needs no S3 and inherits the ABC input_holder default from Task 2).

- [ ] **Step 1: Update the test**

```python
# backend/tests/test_make_sandbox.py
import inspect
import pytest
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.microvm import MicroVMSandbox
from pathfinder.sandbox.microvm_control import FakeMicroVMController
from fakes.in_memory_s3 import FakeS3Store

async def test_default_is_local_sandbox(monkeypatch):
    monkeypatch.delenv("PATHFINDER_SANDBOX", raising=False)
    sb = await app_module.make_sandbox("proj-local")
    assert isinstance(sb, LocalSandbox)

async def test_microvm_flag_builds_microvm_sandbox_with_s3(monkeypatch):
    monkeypatch.setenv("PATHFINDER_SANDBOX", "microvm")
    monkeypatch.setattr(
        app_module, "microvm_controller_factory",
        lambda project_id: FakeMicroVMController(base_url="http://fake-vm"),
    )
    monkeypatch.setattr(
        app_module, "s3_store_factory",
        lambda project_id: FakeS3Store(),
    )
    sb = await app_module.make_sandbox("proj-vm")
    assert isinstance(sb, MicroVMSandbox)
    await sb.start()
    assert sb._handle is None            # lazy: no boot at creation
    # File ops work against injected S3 with no AWS and no boot (Task 3):
    await sb.write_file("aiplc-docs/x.md", "hi")
    assert await sb.read_file("aiplc-docs/x.md") == "hi"

def test_make_sandbox_signature_unchanged():
    sig = inspect.signature(app_module.make_sandbox)
    assert list(sig.parameters) == ["project_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_make_sandbox.py -v`
Expected: FAIL — `AttributeError: module 'pathfinder.app' has no attribute 's3_store_factory'` (and the microvm branch does not yet pass `s3=`).

- [ ] **Step 3: Wire `s3_store_factory` + inject into the microvm branch**

Edit `backend/pathfinder/app.py` — add the import, the factory, and the `s3=` injection (everything else, incl. the local branch and router includes, is unchanged):

```python
import boto3
from pathfinder.sandbox.s3store import S3Store, S3StoreLike

# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store is
# Seoul (ap-northeast-2); see the cross-region governance note below.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)

async def _make_microvm_sandbox(project_id: str) -> Sandbox:
    controller = microvm_controller_factory(project_id)
    s3 = s3_store_factory(project_id)
    shared_http = httpx.AsyncClient(timeout=None)  # streaming SSE: no read timeout
    def harness_factory(handle: VMHandle) -> HarnessClient:
        # mint-on-resume (Task 5): a fresh HarnessClient (and, in prod, a fresh
        # CreateMicrovmAuthToken JWE header) is built on every boot/resume.
        return HarnessClient(base_url=handle.base_url, http=shared_http)
    sb = MicroVMSandbox(
        project_id=project_id,
        controller=controller,
        spec=_boot_spec(),
        harness_factory=harness_factory,
        s3=s3,
    )
    await sb.start()
    return sb
```

> **Cross-region data-governance disclosure (design §6) — carry this into deploy docs and the workshop-open script.** Durable storage (`S3Store`, DynamoDB) is **Seoul (`ap-northeast-2`)**; MicroVMs run in **Tokyo (`ap-northeast-1`)** because the Lambda MicroVMs service is absent in Seoul (confirmed Part 1: `list-microvm-images` 200 in Tokyo, 403 in Seoul). Therefore customer documents are **processed transiently in Tokyo** and **persisted in Seoul**. This cross-border processing must be **disclosed to the customer at workshop start** (per §6). The split is intentional and load-bearing (the service simply does not exist in Seoul), so it is a disclosure item, not a fixable config.

- [ ] **Step 4: Run the test + full suite**

Run: `cd backend && python -m pytest tests/test_make_sandbox.py -v`
Expected: PASS (3 tests).

Run: `cd backend && python -m pytest -v`
Expected: PASS — full suite green. The env flag still defaults to `LocalSandbox`, so all Phase 1 route/parser tests are byte-for-byte unaffected; the seam swap remains zero-route-change.

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/app.py backend/tests/test_make_sandbox.py
git commit -m "feat: inject S3Store into make_sandbox microvm branch (Seoul durable store)"
```

---

### Task 8: Integration drills — real S3 round-trip + recovery drill (requires AWS)

> **INTEGRATION — REQUIRES AWS. Scripted manual verification, NOT pytest.** Same pattern as Part 1 Task 7: do not add these as CI tests; they need real credentials, a real Seoul S3 bucket, a real Tokyo Lambda MicroVM, and Bedrock. Record observed outputs in the PR description. This task also completes the prod wiring that cannot be unit-tested: the real S3 bucket/IAM setup and the `CreateMicrovmAuthToken` JWE mint-on-resume in `harness_factory`.

**Files:**
- Modify: `backend/pathfinder/app.py` (only if the drill surfaces a prod-wiring gap in `s3_store_factory`/`harness_factory` JWE minting)
- Modify: `backend/pathfinder/sandbox/microvm_control_aws.py` (only if the recovery drill requires the AWS `status`→`VMStatus` mapping to include the terminal/expired states — map `get-microvm` `TERMINATED`/`EXPIRED` → `"expired"`, `SUSPENDED` → `"suspended"`, `RUNNING` → `"ready"`)

**Prerequisites:** Part 1 Task 7 completed (`LambdaMicroVMController` bound to real `run/resume/suspend/terminate/get-microvm`, Sonnet-5 profile confirmed, one real turn drove green). Export the Part-1 env (`ANTHROPIC_MODEL`, `PATHFINDER_VM_IMAGE_ID`, `PATHFINDER_VM_ROLE_ARN`) plus Part-2 S3 env.

- [ ] **Step 1: Provision + confirm the Seoul S3 bucket and IAM**

```bash
export PATHFINDER_S3_REGION=ap-northeast-2
export PATHFINDER_S3_BUCKET=pathfinder-artifacts-seoul   # or the real bucket name
aws s3api head-bucket --bucket "$PATHFINDER_S3_BUCKET" --region "$PATHFINDER_S3_REGION"
```
Expected: exit 0 (bucket exists in Seoul). Confirm the EC2/host role has `s3:GetObject`/`s3:PutObject`/`s3:ListBucket` scoped to `arn:aws:s3:::$PATHFINDER_S3_BUCKET/projects/*`. Confirm the bucket's region is genuinely `ap-northeast-2`:
```bash
aws s3api get-bucket-location --bucket "$PATHFINDER_S3_BUCKET"
```
Expected: `"LocationConstraint": "ap-northeast-2"`. This is the durable-store-in-Seoul half of the cross-region governance note.

- [ ] **Step 2: Real S3 round-trip drill (no MicroVM needed — proves true laziness end-to-end)**

With `PATHFINDER_SANDBOX=microvm` and the S3 env, drive a file op against a project WITHOUT ever booting a VM:
```bash
curl -fsS -X POST localhost:8000/projects -H 'content-type: application/json' \
  -d '{"project_id":"s3-smoke"}'
# write + read back an artifact via the normal routes (answers/artifacts)
curl -fsS -X PUT "localhost:8000/projects/s3-smoke/..."   # a route that calls write_file
```
Then confirm the object landed in Seoul S3 and no VM was booted:
```bash
aws s3 ls "s3://$PATHFINDER_S3_BUCKET/projects/s3-smoke/aiplc-docs/" --region ap-northeast-2
aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId, 's3-smoke')]"
```
Expected: the artifact key is listed under `projects/s3-smoke/aiplc-docs/` in Seoul; the MicroVM list shows **no** VM for `s3-smoke` (file ops did not boot — true laziness holds on real infra).

- [ ] **Step 3: Post-turn sync drill (VM → Seoul S3)**

Drive one real Sonnet-5 turn (boots a Tokyo VM), then confirm the turn's output synced to Seoul:
```bash
curl -N -sS "localhost:8000/projects/s3-smoke/events?text=ai-plc%EB%A5%BC%20%EC%8B%9C%EC%9E%91"
# after the SSE stream ends in `done`:
aws s3 ls "s3://$PATHFINDER_S3_BUCKET/projects/s3-smoke/aiplc-docs/" --recursive --region ap-northeast-2
```
Expected: `aiplc-state.md` (and any `*-questions.md`/`audit.md` the turn wrote) now present in Seoul S3 — the post-turn sync pulled them out of the Tokyo VM. Grep the synced `audit.md` for credential markers (`AKIA`, `sk-`, `bedrock-api-key-`, `AWS_BEARER_TOKEN=`) and **record the result** — this is the empirical input to the redaction-at-rest Open Question (if markers appear, redaction-at-rest is required; if never, source-of-truth raw storage is acceptable).

- [ ] **Step 4: Recovery drill — kill a VM mid-session, confirm restore + self-resume**

This is the headline drill (the Part-1 caveat closer). Mid-session, forcibly terminate the VM and confirm the next turn transparently recovers:
```bash
# capture the current VM id, then terminate it out-of-band (simulating expiry/crash)
VM_ID=$(aws lambda-microvms list-microvms --region ap-northeast-1 \
  --query "microvms[?contains(tags.projectId,'s3-smoke')].microvmId | [0]" --output text)
aws lambda-microvms terminate-microvm --region ap-northeast-1 --microvm-identifier "$VM_ID"
# next turn on the same project:
curl -N -sS "localhost:8000/projects/s3-smoke/events?text=%EA%B3%84%EC%86%8D"   # "계속"
```
Expected observations (record all in the PR):
- `get-microvm "$VM_ID"` reports `TERMINATED`/`EXPIRED` → `_ensure_ready` maps it to `"expired"` → a **new** MicroVM is booted (different `microvmId`).
- The fresh VM's workspace was restored from Seoul S3 (confirm via `GET /projects/s3-smoke/state` returning the pre-termination stage, and `/health`+`/files?glob=aiplc-docs/**` on the new VM listing the restored files).
- The turn's SSE stream ends in `done` — the `session-continuity` rule read the restored `aiplc-state.md` and resumed **itself** (no backend resume logic; the backend only copied files). Confirm the agent's first response references the correct resumed stage.

- [ ] **Step 5: Suspend/resume reconcile drill (writes during suspend)**

```bash
# suspend the VM (workshop break)
aws lambda-microvms suspend-microvm --region ap-northeast-1 --microvm-identifier "$VM_ID2"
# while suspended, write an answer via a route (lands in Seoul S3 only)
curl -fsS -X PUT "localhost:8000/projects/s3-smoke/answers/..."   # a route calling write_file
# resume by driving the next turn:
curl -N -sS "localhost:8000/projects/s3-smoke/events?text=%EC%99%84%EB%A3%8C"   # "완료"
```
Expected: `_ensure_ready` sees `SUSPENDED` → resumes → reconcile pushes the S3-newer answer into the resumed VM, so the agent's turn operates on the up-to-date answer (not stale VM-snapshot state). Confirm the resumed VM's `aiplc-docs/` contains the answer written during suspend.

- [ ] **Step 6: Record results and commit any prod-wiring completion**

```bash
git add backend/pathfinder/app.py backend/pathfinder/sandbox/microvm_control_aws.py
git commit -m "feat: complete S3 + JWE mint-on-resume prod wiring; record persistence/recovery drills"
```
Paste into the PR description: bucket-location output, the S3 round-trip listing (no VM booted), the post-turn sync listing, the credential-marker grep result (redaction-at-rest evidence), the recovery drill's old/new `microvmId` + restored-state confirmation, and the reconcile drill result.

---

---

## Self-Review

**Confirmed-scope coverage (every Part-2 item mapped to a task):**
- `S3Store` injectable client (fake + moto + prod), boto3/moto dependency justified → **Task 1**.
- `input_holder` lifted onto the `Sandbox` ABC with a no-op default before any route consumes it (**Finding B**) → **Task 2** (first-class task).
- Reroute not-booted file ops to S3 / true laziness (VM boots only for turns) → **Task 3** (generalized to *always* S3, with rationale: a VM-only write is the Part-1 data-loss trap).
- Sync `aiplc-docs/` + prototype source to S3 after every turn (the Part-1 hook location) → **Task 4**.
- Status-refresh so an auto-suspended VM resumes / expired VM reboots+restores; `FakeMicroVMController` simulated auto-suspend/expiry; suspend/resume reconcile of S3-newer writes (**Finding A**, all three parts a/b/c) → **Task 5** (first-class linchpin task).
- Recovery on expiry (max 8h)/failure: fresh boot + restore-from-S3 + methodology self-resume, no backend resume logic → **Task 6** (with an invariant-lock test that no resume state machine exists).
- Cross-region governance (S3 Seoul / MicroVM Tokyo) disclosure note → carried in Global Constraints + **Task 7** (config) + **Task 8** (drill, bucket-location proof).
- Integration drills (AWS-required, scripted manual, not pytest): real S3 round-trip + recovery drill + reconcile drill → **Task 8** (same pattern as Part 1 Task 7).
- JWE auth-token refresh across suspend/resume → **resolved as mint-on-resume** (Task 5 rationale; wiring in Task 7 `harness_factory` / Task 8 real `CreateMicrovmAuthToken`).
- Busy-vs-wait → **unchanged** (soft busy signal from Part 1; noted in Global Constraints as a conscious non-change since design §4 does not alter it).

**Findings A & B are each a first-class task:** Finding A = Task 5 (status-refresh + resume + reconcile + simulated suspend/expiry), extended by Task 6's recovery scenarios. Finding B = Task 2 (input_holder onto the ABC).

**Placeholder scan:** No TBD/TODO/"similar to Task N". Every code step shows complete code or an exact edit against a quoted Part-1 baseline. The only intentional `NotImplementedError` is the pre-existing `LambdaMicroVMController` (Part 1 Task 6 skeleton), honestly deferred to the AWS-only Task 8; Part 2 adds no new fake-passing stubs.

**Type/signature consistency with Part-1 interfaces:**
- `Sandbox` ABC: Part 2 adds only `input_holder: str | None = None` + `set_input_holder(self, holder: str | None) -> None` (concrete, non-abstract), preserving all six abstract methods verbatim. `MicroVMSandbox` and `LocalSandbox` both satisfy it; `MicroVMSandbox` still passes `sandbox_contract`.
- `MicroVMSandbox.__init__` gains exactly one param `s3: S3StoreLike` (keyword, after `harness_factory`); every construction site updated (contract test, make_sandbox test, sandbox/persistence/recovery tests) — all pass `FakeS3Store()`. Public `Sandbox` method signatures are byte-for-byte unchanged; only their *bodies* change (file ops → S3).
- `MicroVMController`/`VMStatus`: `VMStatus` gains `"expired"` (additive to the `Literal`); the ABC's five methods are unchanged. `FakeMicroVMController` gains two sync test-only helpers (`simulate_auto_suspend`, `simulate_expiry`) — not part of the ABC.
- `HarnessClient`/`HarnessLike`: unchanged surface (`send_message`/`read_file`/`write_file`/`list_files`/`heartbeat`); Part 2 uses `read_file`/`write_file`/`list_files` internally for sync/restore. `S3StoreLike` is a new, distinct 3-method Protocol (`get`/`put`/`list`) — deliberately *not* the harness surface (different semantics: `get` raises `FileNotFoundError`, `list` takes a prefix not a glob).
- `make_sandbox(project_id) -> Sandbox` signature preserved (`test_make_sandbox_signature_unchanged`); `registry`, `app`, router includes untouched; local branch byte-for-byte identical.

**Scope sizing:** 8 tasks (7 unit + 1 integration), comparable to Part 1's density (7). Drafted whole because persistence and recovery are co-dependent (see Scope). All logic is unit-testable without AWS (`FakeS3Store` + `FakeMicroVMController` with simulated suspend/expiry + moto for the real `S3Store`); the AWS-only surface is isolated to Task 8, clearly labeled and not CI-collected — matching the Part-1 discipline.

## Open Questions

**RESOLVED in this plan (decisions made, not deferred):**

- **~~JWE harness auth-token refresh across suspend/resume~~ — RESOLVED: mint-on-resume.** `harness_factory` is re-invoked on every `_ensure_ready` handle transition (boot/resume/reboot), which is exactly when a token could be stale or the endpoint changed. Simpler than refresh-on-401 (keeps `HarnessClient` pure transport) and sufficient because a token is only needed for an active turn, which always passes through `_ensure_ready`. Real `CreateMicrovmAuthToken` wiring lands in Task 7/8. (See Task 5.)
- **~~Reconcile semantics (S3-newer writes during suspend)~~ — RESOLVED: S3 unconditionally wins, idempotent re-push.** Because Task 3 makes S3 the single source of truth for all file ops, reconcile is a blind `_restore_workspace_from_s3` after resume — no per-file timestamp bookkeeping. (See Task 5.)

**STILL OPEN (decisions/flags, not code blockers):**

1. **S3 audit redaction-at-rest (the flagged governance question).** `audit.md` is redacted *at the route seam on read* (`parsers/audit.py` redacts `user_input`/`ai_response`/`context` fields via `redact_credentials`) and `AgentEvent.text` is redacted at the turn seam (`turns.py`). But Task 4's post-turn sync stores the **raw** file the agent wrote to S3 — so a credential-shaped string the agent emitted into `audit.md` would sit **unredacted at rest in Seoul S3**, even though every *read path* redacts it. **Question:** does S3-stored audit need redaction-at-rest, or is raw source-of-truth acceptable given (a) IAM-role-only auth means no long-lived key *should* ever appear, and (b) all read paths already redact? **Recommendation:** treat S3 as raw source-of-truth for now (redacting at rest would corrupt the audit trail's fidelity and fight the "no methodology logic in backend" principle if done selectively), BUT gate this on **Task 8 Step 3's empirical grep** — if the real Sonnet-5 turn's `audit.md` ever contains a credential marker, flip to redact-on-sync (apply `redact_credentials` in `_sync_workspace_to_s3` for `audit.md` specifically) before any real customer workshop. Escalate to the security reviewer with the Task 8 grep evidence attached. This is the one Global-Constraint item that Part 2 cannot fully close in code without the AWS drill's data.
2. **Sync granularity / cost.** Task 4 re-syncs the whole `aiplc-docs/**` + `prototype/**` subtree every turn (full copy, no diffing). For Discovery-sized workspaces this is trivial, but a large prototype source tree could make per-turn sync slow/costly. **Recommendation:** ship the full-copy version (simple, correct); if Task 8 measures unacceptable per-turn latency, add content-hash skip (`put` only when the S3 copy differs) — a localized `_sync_workspace_to_s3` change, no interface impact.
3. **`_glob_prefix` derivation robustness.** `list_files` derives an S3 list prefix from the glob for efficiency. The documented fallback (list the whole small project key-space and `fnmatch`-filter) is always correct; the prefix is only an optimization. **Recommendation:** if the prefix helper proves fragile under TDD, use the fallback — correctness is in the `fnmatch` filter, not the prefix. (Noted in Task 3.)
4. **Concurrent-project S3 client reuse.** Task 7 builds a fresh boto3 S3 client per `s3_store_factory` call (per project). boto3 clients are thread-safe and moderately expensive to create; for many concurrent projects a shared module-level client (with per-project `prefix`) would be cheaper. **Recommendation:** defer — the workshop model is "session-단위 소수 테넌트" (few concurrent tenants, design §배경), so per-project clients are fine; revisit only if tenant count grows.
