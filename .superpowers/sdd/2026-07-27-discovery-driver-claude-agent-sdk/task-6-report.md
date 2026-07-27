# Task 6 report — ClaudeDriver

## Status: DONE_WITH_CONCERNS

Concerns are design-boundary notes for review, not known defects. See the end.

---

## 1. Diagnosis of the three failures

All three failures had **one root cause**, and it was not in the driver — it
was in the **test adapter's fake**.

### The root cause

`questions` events are produced in exactly one place:
`ClaudeDriver._on_can_use_tool`. That is the `can_use_tool` callback the real
SDK invokes when the model calls `AskUserQuestion`. Nothing else in the driver
can ever emit `kind="questions"`.

The prior implementer's adapter scripted a question by putting an
`AskUserQuestion` **`ToolUseBlock` in the fake client's message script**:

```python
blocks.append(ToolUseBlock(id="q", name="AskUserQuestion", input={...}))
```

But `tests/fakes/fake_sdk.py:FakeSdkClient` just yields its script from
`receive_response()`. It never calls `can_use_tool` — only the real SDK does
(and the brief said so explicitly at line 95). So that block flowed through
`_translate` as an ordinary tool-use block and came out as
`AgentEvent(kind="status", text="AskUserQuestion")`. **No `questions` event was
ever created**, which is precisely what the three assertions reported:

- `driver_contract.py:88` → `AssertionError: questions 이벤트가 없다`
- `test_persists_pending_questions_to_s3` → nothing wrote to S3, because
  `save_pending` is called inside the never-invoked callback
- `test_a_pending_s3_failure_does_not_kill_the_turn` → `assert 'questions' in []`

I confirmed this by reading the fake rather than inferring it: `FakeSdkClient`
has no `can_use_tool` reference at all.

### The driver was NOT the problem — with one exception

The prior implementer's `_stream`/`_on_can_use_tool`/`_translate` were faithful
copies of `builder.py` and correct. The queue-polling loop (the hard-won detail
the task flagged) was carried across correctly, comment included. I kept all of
it.

The one place the prior implementer's design *was* broken is
`_drain_until_done`, and its own docstring says so out loud — it contains three
paragraphs arguing with itself ("but that generator holds the only reference to
`next_msg`/`agen`, so we cannot re-enter it from here…", "the safest and
simplest way… is to await the pending question's resolution effects
directly"). It polled `self._queue` and `self._turn_active` from *outside* the
turn. Since `_stream`'s generator was already abandoned, nothing was advancing
the SDK iterator, so no post-answer message could ever arrive; the loop's only
exit was `not self._turn_active`, which `_stream`'s `finally` had already set.
It returned promptly and silently, having relayed nothing. That gap was
invisible to the tests because the tests never got as far as raising a
question.

### The structural question the brief left open, and how I settled it

The brief's `run_answers` sketch assumed a waiting future can be resolved and
the *same* `run()` stream keeps yielding — which is exactly how `builder.py`
works. **That does not hold for Discovery**, and this is the one place I
deliberately diverged from `builder.py`. Evidence, all read from the code:

- `builder.py` + `proto/session.py`: `send_answers` is **not a stream** — it
  returns `bool` (`session.py:202-212`), and the original `run()` generator is
  still being iterated, so resolving the future is enough.
- Discovery: `runner.py:154-179` exposes `send_answers` as its **own async
  iterator**, and `routes/turns.py:50-53` opens a **separate SSE response** for
  it.
- The frontend refuses to submit answers while a stream is open:
  `frontend/lib/useWorkspaceStream.ts:230` — `if (stopRef.current) return`.
  `stopRef` is only cleared in `finish()`, which runs on `onDone`/`onError`.

So if `run()` parked forever on a pending question (builder.py's behaviour),
`stopRef.current` would stay set and **the user could never submit an answer** —
a deadlock. `runner.py:134-140` gives a second, independent reason: it syncs the
workspace to S3 **only** on `done`/`error`, so a turn parked mid-question would
leave everything the agent already wrote in the volatile local workspace.

Therefore `run()` must terminate on a question (`questions` → `done` → return),
and `run_answers` must resolve the future and then drain **the rest of the same
turn**. That is `_continue_after_answers`.

I verified the load-bearing SDK fact this depends on, rather than assuming it:
abandoning `receive_response()` does **not** lose buffered messages, because the
buffer belongs to `Query`'s anyio memory-object stream
(`claude_agent_sdk/_internal/query.py:121`), not to the generator. Probe:

```
after-cancel fresh iterator got: ['m1', 'm2']
buffered-item survived cancel: a1
```

So a fresh `receive_response()` over the same client resumes the turn without a
second `query()`, and without dropping anything.

---

## 2. Kept vs. rewritten

### Kept from the prior implementer (verbatim or near-verbatim)

Everything that was a faithful `builder.py` port, plus its comments:

- `_rel`, `_validate_permission_mode`, `_suppress_shadowed_callback_warning`
- `_default_client_factory` — including the `--session-id`/`--resume` conflict
  comment, the `CLAUDE_CONFIG_DIR` rationale, the `can_use_tool`-under-
  `bypassPermissions` probe note, and the `skills`-intentionally-unset note
- `_on_post_tool_use`, `_answer_to_sdk`, `_translate`
- `_on_can_use_tool`'s body and its `PermissionResultDeny` reasoning
- The queue-polling race loop and its comment (moved into `_pump`)
- `pending()` and `_resume_with_answers` largely as the brief specified

### Rewritten, and why

| What | Why |
|---|---|
| `_drain_until_done` → **deleted**, replaced by `_continue_after_answers` | It could not work by its own docstring's admission (see §1). Replaced with a fresh `receive_response()` over the same client — no re-`query()`. |
| `_stream`'s loop → extracted as `_pump(agen)` | `run()` and `run_answers` both need the identical poll-and-drain loop over *different* iterators. One copy, two callers. |
| `_pump` **stops the turn on a queued `questions` event** | The divergence from `builder.py` established in §1. Documented at length in the module header and the method docstring, since a future reader comparing the two files will notice and should not "fix" it back. |
| Added the pending-question short-circuit in `run()` | Mirrors `StrandsDriver`'s B1 guard (`driver.py:166-177`) with a Claude-specific cause: while a question is parked the CLI is blocked waiting for the permission response, so a `query()` would be accepted and never answered — the turn would poll to nowhere. Same user-facing wording as Strands, so the drivers stay indistinguishable. |
| Added a stale-`interrupt_id` guard in `run_answers` | The prior code checked `_pending_iid == interrupt_id` but fell through to the *resume* path on mismatch — which calls `query()` while the CLI is blocked, i.e. hangs. Now it degrades to `"no pending questions"` and leaves the live question intact. |
| `_interrupted` / `CancelledError` branch → **removed** | Copied from `builder.py`, but dead here: Discovery has no interrupt route (`builder.interrupt()` has no analog in `runner.py`). Keeping it would imply a stop button exists. The defensive `CancelledError` cleanup inside `_on_can_use_tool` is kept. |
| `_on_can_use_tool`'s S3 save → `_save_pending_quietly` | Also handles the empty-`session_id` case: `load_pending` validates `session_id` as a non-empty string, so a record written without one is unreadable. Skips with a log rather than writing junk. |
| `_resume_with_answers` prompt gained a trailing JSON record | See §3's `echo_answers` row — the honest, non-hardcoded way to satisfy the contract. |
| Dropped `self._pending_sdk_questions` | Written, never read. The coroutine holds `sdk_questions` in its own frame; the restart path reads it from S3. |

---

## 3. The six scripted keys — how each is implemented honestly

Translation lives in **`backend/tests/fakes/fake_sdk_asking.py`** (new).
`tests/fakes/fake_sdk.py` was **not** modified or extended — builder tests
depend on its exact shape. The new module is shared by both ClaudeDriver test
files rather than duplicated in each.

| Key | Implementation | Why it is not vacuous |
|---|---|---|
| `text` | `TextBlock`s in the fake's script → `_translate` → `message` | Contract asserts the exact scripted string comes back. |
| `tools` | `ToolUseBlock`s → `_translate`'s dedupe → `status` | Contract asserts `["A","A","B"] → ["A","B"]`; names are pass-through, never hardcoded. |
| `questions` | **`AskingSdkClient`** spawns `driver._on_can_use_tool` on a task the *client* owns, then yields nothing until it completes. `interrupt_id` is a fresh `uuid4` the driver mints. | This is the real path. The fake is wired to the driver's own callback at the same place the real factory wires it (`ClaudeAgentOptions(can_use_tool=...)`). Reproduces **both** real-SDK properties: the stream goes quiet (starving a plain `async for`), and the callback outlives the iterator. |
| `raise` | `RaisingSdkClient.receive_response` raises `RuntimeError("boom")` | Contract asserts the exact string `"agent turn failed"`, so the raw message cannot leak. |
| `echo_answers` | `EchoAnswersSdkClient` reads the **last prompt the driver actually passed to `query()`**, parses its final line, and echoes those values. | **Nothing is hardcoded.** To make the values observable at all, `_resume_with_answers` now appends a machine-readable `(답변 기록)` line to the prompt. That is not test scaffolding: after a restart the S3 pending record is deleted on the way out, so the transcript becomes the only durable trace of which interrupt a set of answers resolved. The seeded S3 record deliberately carries `interrupt_id: "seeded-not-the-callers-id"` — a driver echoing the *stored* id instead of the *caller's* fails. Mutation-verified (M3). |
| `followup_questions` | `AskingSdkClient` with `FOLLOWUP_SDK_QUESTIONS` — a **different** question text than the `questions` script | A driver that replayed the first question would not look correct. Exercises `run_answers` yielding `questions` (the `runner.py:170-172` re-capture path). |

Both `run_answers`-only scripts seed an S3 pending record. That is **not a
workaround** — it is the faithful reproduction of the state the contract puts
the driver in: a fresh driver with no in-memory future *is* the
backend-restarted case, which is exactly the path `_resume_with_answers`
exists for.

---

## 4. Copied from `builder.py` vs. written fresh

**Copied** (with comments carried across, per the human ruling — no shared
module extracted): `_rel`, `_validate_permission_mode`,
`_suppress_shadowed_callback_warning`, `_default_client_factory` (retargeted to
Discovery: `cwd`, `CLAUDE_CONFIG_DIR`, no `skills`, MCP server + `allowed_tools`
from Task 5), `_on_post_tool_use`, `_answer_to_sdk`, `_on_can_use_tool`,
`_translate`, `drain_queue`, `_ensure_client`, and the queue-polling race loop.

**Written fresh** (no reference implementation exists):

- `_pump`'s question-terminates-the-turn behaviour, and the module-header
  essay explaining why it differs from `builder.py`
- `_continue_after_answers` — the fresh-iterator resume
- `run` / `run_answers` / `pending` — the three-method contract
- `_save_pending_quietly`, `_clear_pending_quietly`, `_clear_pending_state`
- `_resume_with_answers`, incl. the answer record in the prompt
- The `run()` pending short-circuit and the stale-`interrupt_id` guard
- `tests/fakes/fake_sdk_asking.py`

---

## 5. Test commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
14 passed in 0.63s
```
(Before: `3 failed, 5 passed`. 8 → 14 tests: 6 new driver tests.)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.24s
```
(The production rollback path and `builder.py` are untouched.)

```
$ cd backend && .venv/bin/python -m pytest -q
590 passed, 1 warning in 12.99s
```
**No regression: 576 baseline + 14 new = 590.** The one warning is the
pre-existing httpx/starlette deprecation notice.

`git diff HEAD` confirms **zero** changes to `driver.py`, `strands_tools.py`,
`proto/builder.py`, `runner.py`, and `driver_contract.py`.

### Mutation testing — proving the tests have teeth

The brief warned that a vacuous `echo_answers` had already slipped through
review once, so I did not trust green. Eight mutants, each reverted after:

| # | Mutation | Result |
|---|---|---|
| M1 | `_pump` doesn't stop the turn on `questions` (i.e. builder.py's behaviour) | **HANGS** (timeout) — this is the deadlock §1 predicts, demonstrated |
| M2 | `save_pending` call removed | `test_persists_pending_questions_to_s3` fails |
| M3 | Answer record hardcoded instead of read from args | contract's echo check fails |
| M4 | `_answer_to_sdk` bypassed (raw letter sent) | `test_answers_reach_the_sdk_as_the_tool_result` fails |
| M5 | Stale-`interrupt_id` guard removed | `test_a_stale_interrupt_id_...` fails |
| M6 | `_continue_after_answers` re-`query()`s the client | initially **SURVIVED** → gap closed (below) |
| M7 | `run()`'s pending short-circuit removed | **HANGS** (timeout) — confirms the guard prevents a real hang |
| M8 | Fake `await`s the parked callback instead of polling | 3 tests fail — confirms the fake's fidelity property is load-bearing |

M6 exposed a genuine gap: nothing asserted that the answers path does *not*
send a second `query()`. `test_answers_reach_the_sdk_as_the_tool_result` now
snapshots `client.queries` before `run_answers` and asserts it is unchanged;
M6 fails after that. (An earlier M8 variant also survived, but only because
the driver returns before the fake reaches the mutated line — the sharper M8b
above fails 3 tests, so the property is covered.)

No `"Task was destroyed but it is pending!"` noise: an autouse fixture cancels
callbacks still parked at teardown (a question left unanswered is the normal
end state for several tests).

---

## 6. Concerns

1. **`run()` terminating the turn on a question is the one deliberate
   divergence from `builder.py`.** I am confident in it (three independent
   pieces of evidence in §1, plus M1/M7 hanging), but it is the thing a
   reviewer should check hardest, because the human ruling was "copy
   `builder.py`" and this is where I did not. Copying literally here would
   deadlock the Discovery UI.
2. **`_continue_after_answers` rests on an SDK-internal guarantee** — that
   `Query`'s anyio buffer, not the generator, owns unread messages. Probed and
   verified against the installed SDK (0.2.126), but it is not a documented
   public contract, so an SDK upgrade could invalidate it. The Task 9
   deployment checklist should include one real question round trip against
   the live CLI; no fake can substitute for that.
3. **`_resume_with_answers` adds a JSON answer record to the prompt.** It has
   a standalone justification (durable trace of which interrupt a set of
   answers resolved) and it is what makes the contract's `echo_answers` check
   honest, but the model now sees one extra line of machine-readable text. If
   that is unwanted, moving it will require a different honest echo mechanism —
   not simply deleting it.
4. **Duplication with `builder.py` is large and intentional** (~200 lines,
   human-ruled, deferred). `strands_tools.py` from Task 5 is in the same
   category. When the workshop rollback path is deleted, all three should be
   revisited together.
5. **New file not in the plan's File Structure:**
   `backend/tests/fakes/fake_sdk_asking.py`. Justified by the brief's own
   constraint (don't extend `FakeSdkClient`) plus DRY across the two test
   modules — but the plan document was not updated.
6. **Not covered by any test:** the `PermissionResultDeny` branch (a question
   with zero options) and the `_on_post_tool_use` hook. Both are verbatim
   `builder.py` copies with their own coverage in `test_proto_builder.py`, and
   both were out of the brief's scope, so I did not add tests.
7. **`resume` semantics.** `_default_client_factory` reads `resume` off the
   session dict, and `_stream(resume=True)` sets it — but the client is
   created once and cached, so `resume` only takes effect on the turn that
   first constructs it. That matches `builder.py`'s one-client-per-session
   model and is correct for the restart case (a fresh process resumes on its
   first turn). Task 8's `driver_factory` should be aware that `app.py:255`'s
   session dict has no `resume` key today.
