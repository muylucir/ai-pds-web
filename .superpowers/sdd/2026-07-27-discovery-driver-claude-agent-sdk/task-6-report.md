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

---

# Fix round 1 — five spec findings from review

All five were reproduced independently before any code changed, with throwaway
probes (not committed). Each fix is verified by re-running the same probe plus a
new committed test.

## CRITICAL — `_pump` permanently lost the assistant message before a question

**Confirmed.** Built a faithful `Query` replica (anyio memory stream + a read
loop that writes the assistant message and dispatches the `control_request`
`gap` seconds later) and drove the real driver through it:

```
gap=0:     [('questions', None), ('done', None)]                       -> PROSE LOST: True
gap=0.001: [('message', '왜 묻는지 설명'), ('questions', ...), ('done', ...)] -> False
gap=0.06:  [('message', '왜 묻는지 설명'), ('questions', ...), ('done', ...)] -> False
```

Mechanism, as reported: `asyncio.wait` returns `done=∅` on timeout, but
`next_msg` can resolve in that same tick. The old loop drained the queue, saw
`asked`, and returned without ever calling `next_msg.result()`; the `finally`
then cancelled it. anyio's `send_nowait` hands an item **directly to a parked
receiver without buffering it** (`anyio/streams/memory.py:210-217`), so the
message is destroyed — it is not waiting in the buffer for the fresh iterator
`_continue_after_answers` opens. And it is the common case, not an edge: the CLI
writes the assistant message and the `control_request` in one read-loop pass
(`query.py:250-322`) with no model latency between them, and
`driver.py:_CONTACT_ADDENDUM:44-45` *mandates* the model explain itself first.
`builder.py` never hit it because it never returns on `asked`.

**Fix.** `_pump` now runs a `ready_events()` sweep on every loop pass, *before*
draining the callback queue: it consumes every already-delivered message and
re-arms the receive, so nothing the cancel could discard is ever left unread.
Loop termination moved to an `ended` flag set by the sweep on
`StopAsyncIteration`.

I did **not** keep the belt-and-braces second sweep I first wrote after the
`asked` check: instrumenting it showed it never fires (the CLI cannot send more
messages until the permission request is answered, so nothing can arrive between
the first sweep and the return). Removed it rather than commit unreachable code,
and the comment now states that reasoning.

**Test:** `test_the_prose_before_a_question_is_not_lost`. The fake needed a new
`preface` mode — the reviewer's diagnosis of why nothing caught this was exactly
right: `AskingSdkClient` spawned the callback *before* yielding anything, so its
question turns never had a message in flight. `preface` now spawns the callback
and yields the assistant message with **no await in between**, reproducing the
CLI's single-pass delivery. It is on by default for the `questions` script,
because real turns always have the prose.

Probe after the fix: `PROSE LOST: False` at all three gaps.

## IMPORTANT 1 — restart answers turn ran with no `CLAUDE.md`

**Confirmed:** `CLAUDE.md after run_answers: False`. `place_rules` was only in
`run()`. **Fix:** extracted `_place_rules()` and call it unconditionally at the
top of `run_answers` too. This is the one path where the workspace is
*guaranteed* cold — no-future means a redeploy, and `runner.py:36` restores only
`aiplc-docs/`, `prototype/`, `uploads/`, never the rules. Idempotent and cheap,
so unconditional rather than branch-dependent.
**Test:** `test_places_the_rules_on_the_restart_answers_path_too` (asserts the
workspace starts cold, then that `CLAUDE.md` exists after).

## IMPORTANT 2 — restart path had no stale-`interrupt_id` guard

**Confirmed**, reproducing the reviewer's exact scenario: seeded `i-CURRENT` /
"NEW question", submitted `i-STALE-FROM-OLD-TAB` → prompt sent was
`- NEW question → 진행` and `pending record destroyed: True`.

**Fix:** `_resume_with_answers` now compares `data["interrupt_id"]` against the
caller's and returns the contract string `"no pending questions"` on mismatch,
matching the live path. After the fix:

```
stale submit -> [('error', 'no pending questions')]
client ever built: False          # the model is never called
real pending record survived: True
pending() still serves the live form: True
correct id -> ['message', 'done'] # prompt: - NEW question → 진행
```

**Test:** `test_a_stale_interrupt_id_is_refused_on_the_restart_path_too`.

## IMPORTANT 3 — `_turn_active` outlived an abandoned generator

**Confirmed:** `_turn_active` still `True` immediately after `await
agen.aclose()`, cleared only after 2 bare `await asyncio.sleep(0)`, and the
user's retry came back `[('error', 'turn already in progress')]`.

Root cause: `aclose()` runs only the **outermost** generator's `finally`
synchronously (probed: nested-generator `finally` needs 2 extra ticks for
`GeneratorExit` to propagate). The flag was released in `_stream`, which is
nested inside `run`. `runner.py:144-152` takes the abandon path routinely and
clears its own `_turn_active` synchronously, so the two disagreed.

**Fix:** turn-slot ownership moved to `run`/`run_answers` — the two contract
entry points, always the outermost generator — via `_acquire_turn()` /
`_release_turn(token)`. The token means a rejected caller cannot release the
slot the live turn holds. After: `_turn_active` is `False` immediately after
`aclose()`, and the retry succeeds.

One behaviour change this forced, and I judged it the correct order: the
concurrency guard now precedes the pending-question short-circuit in `run()`. A
test caught the conflict. If a turn is genuinely still streaming,
`"turn already in progress"` is the accurate report; re-surfacing the question
would invite the user to answer a form whose turn someone else is still
consuming. A question parked by a turn that already *returned* leaves the slot
free, so that case still reaches the short-circuit.
**Tests:** `test_an_abandoned_turn_frees_the_slot_immediately`,
`test_a_concurrent_turn_is_still_rejected`.

## IMPORTANT 4 — `echo_answers` covered `interrupt_id` but not `answers`

**Confirmed** by mutation: hardcoding `"answers": {"1": "A"}` left all tests
green, because the contract only ever passes that one value.

This is a test gap, not a driver defect, so the driver is unchanged. Two changes
instead:

- The contract adapter's seeded `interrupt_id` had to become `"i-42"` (the value
  the contract passes), because the driver now validates the round on *both*
  paths — the old `"seeded-not-the-callers-id"` trick would now be legitimately
  refused. So the adapter can no longer prove echo honesty, and the docstring
  says so and points at the test that does.
- New `test_the_answer_record_echoes_the_received_values_not_stored_ones` uses
  values that differ from the stored script in every respect: two questions, and
  answers `{"2": "B", "1": "A"}` — different keys, different values, different
  order from the contract's `{"1": "A"}`. It asserts the record echoes exactly
  what was submitted, and that the human-readable lines translate against the
  stored questions (`둘째 질문 → 라`, `첫 질문 → 가`).

## MINOR 1 — non-UUID `session_id` (fixed here, not deferred)

**Confirmed against the bundled binary:**
`claude --session-id=pilot1 -p hi` → `Error: Invalid session ID. Must be a valid
UUID.`

I fixed this in the driver rather than deferring to Task 8, for two reasons.
First, the constraint belongs to the SDK boundary this file owns — `app.py`'s
session dict is a *descriptor*, and `_default_client_factory` is the only place
that knows the CLI's UUID requirement. Second, `_validate_permission_mode`
already establishes exactly this precedent in this file: reject/normalize at the
boundary rather than let an invalid value reach the CLI and surface as an opaque
`"agent turn failed"`.

New `_sdk_session_id(session) -> (str, bool)`, carrying over
`proto/session.py:124-138`'s judgment. One deliberate difference: the derived id
is `uuid5(NAMESPACE_URL, "pathfinder:<raw>")`, not `uuid4`, because it must be
**stable across restarts** or `--resume` could never find the transcript. An
already-valid UUID passes through untouched; a missing id returns
`resume=False`, since there is nothing to resume.
**Tests:** three — stable derivation, pass-through, and missing-id.

Task 8 still needs to know `app.py:255` passes a non-UUID and has no `resume`
key (carried in the concerns below).

## MINOR 2 — no `disconnect()`

Added `ClaudeDriver.disconnect()`, mirroring `builder.py:395-404`: idempotent,
swallows-and-logs teardown errors, and additionally cancels a parked question
future and clears pending state (a question cannot survive the subprocess, and
leaving `_pending_payload` set would make `pending()` advertise an unanswerable
question — the same reasoning as `builder.interrupt()`). **Not wired** — that is
Task 8's, since `runner.py` is off-limits here.
**Test:** `test_disconnect_tears_down_the_subprocess_and_clears_pending`.

## MINOR 3 — inaccurate comments

Both fixed alongside their bugs: `_pump`'s `finally` comment now states that the
cancel *discards* whatever the receive was handed (which is why `ready_events()`
must run first), and `run_answers`'s docstring now leads with "Two paths, and
BOTH validate `interrupt_id`".

## Commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
24 passed in 0.29s
```
(was 14; +10 tests)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.24s
```

```
$ cd backend && .venv/bin/python -m pytest -q
600 passed, 1 warning in 12.50s
```
590 → 600, exactly the 10 new tests. `git diff HEAD` confirms zero changes to
`driver.py`, `strands_tools.py`, `proto/builder.py`, `runner.py`,
`driver_contract.py`.

## Mutation re-run

| # | Mutation | Result |
|---|---|---|
| N1 | **The `answers`-hardcoding mutant that survived review** | `test_the_answer_record_echoes_...` fails ✓ |
| N2d | Restore the ORIGINAL `_pump` ordering (drain queue before reading `next_msg`) | `test_the_prose_before_a_question_is_not_lost` fails ✓ |
| N3 | `_place_rules` removed from `run_answers` | `test_places_the_rules_on_the_restart_answers_path_too` fails ✓ |

Also re-verified the earlier round's M1–M8 still hold. Two variants I tried
(`if done_set:` guarding the sweep) are semantically equivalent to the fix, not
defects — the probe passes either way, which is why N2d (the true original
ordering) is the mutant that matters.

## Concerns after this round

1. **Concern 2 from the first round stands unchanged** — `_continue_after_answers`
   relies on `Query`'s anyio buffer owning unread messages. This round makes the
   reliance *narrower* and better understood (the Critical was precisely the case
   where the buffer does NOT own the message, because `send_nowait` bypasses it
   for a parked receiver), but it is still SDK-internal. A real question round
   trip against the live CLI remains the only way to close this.
2. **The concurrency-guard reordering in `run()`** is a small behaviour change
   the review did not ask for; it fell out of IMPORTANT 3 and a test caught it.
   Reasoning is in the code and above — flagging it explicitly in case the
   preferred precedence is the other way.
3. **`_sdk_session_id` changes which transcript a project resumes** for any
   project whose id is not already a UUID: `project_id` no longer *is* the
   session id. Nothing has shipped, so there is no transcript to orphan, but if
   any environment already has Discovery transcripts keyed by raw project id
   they will not be found. Worth a word at deploy time.
4. **`disconnect()` is unwired**, so the subprocess leak is still live until
   Task 8 calls it from `runner.stop()`.
