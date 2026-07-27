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

---

# Fix round 2 — two residuals on the CRITICAL

Both reproduced before changing anything, with a faithful `Query` replica
(anyio memory stream + a read loop that writes N assistant messages and then
dispatches the `control_request`).

## Residual 1 — `sweep()` drained one message per pass, not all

**Confirmed.** Probed the mechanism directly first:

```
done() immediately after ensure_future: False
re-armed done() synchronously (this is the `while` bug): False
  became done after 1 sleep(0)
```

A freshly created `ensure_future` is never synchronously `done()`, so
`while next_msg.done()` could not iterate twice. End to end, with N messages
delivered before the question in one read-loop pass:

```
n=1: got ['prose-1']              -> OK
n=2: got ['prose-1']              -> LOST {'prose-2'}
n=3: got ['prose-1']              -> LOST {'prose-2','prose-3'}
```

The extras die by the same `send_nowait`-to-a-parked-receiver mechanism as the
original bug: the `finally`'s cancel destroys them, and they are not in the
buffer for the fresh iterator. Also reproduced the reviewer's point that this is
routine, not hypothetical — at `gap=0.003` (real CLI inter-message gaps measured
at 3-4 ms, far inside the 50 ms poll) all N messages arrive in a single pass.

**Fix.** `sweep()` now gives the re-armed receive its scheduler turn with
`await asyncio.wait({next_msg}, timeout=0)` and keeps going until the receive
genuinely has nothing ready. Measured: each subsequent buffered message becomes
available after exactly one such turn. After the fix, n=1/2/3 all `OK`.

## Residual 2 — the dropped "second sweep" was reachable

**Confirmed, and my round-1 reasoning was wrong.** I had instrumented it and
concluded it never fires; the instrumentation was the problem, not the
conclusion's subject. The reviewer is right that each `yield` in the queue-drain
loop hands control to the scheduler, which is exactly the window the re-armed
receive needs.

Getting a test to actually hit that window took a turn-by-turn trace of the fake
against the real driver, because my first two attempts delivered the message too
early and were silently caught by the *pre*-drain sweep — the test passed with
the post-drain sweep removed, i.e. it was vacuous:

```
fake: yielded <preface>
fake: turn 1      <- driver yields `questions` here (drain has begun)
fake: turn 2      <- deliver here; only a POST-drain sweep can recover it
```

That constant is now named (`_DURING_DRAIN_TURNS`) and the trace is recorded in
the comment beside it, so the next person does not have to rediscover it.

**Fix.** Restored the sweep after the queue drain. Verified end to end: the
message is recovered, and it arrives *after* `questions` — the ordering is itself
the evidence that the post-drain sweep did the work, which is what the test now
asserts.

## The comment, per the reviewer's instruction

`_pump`'s docstring now leads with the invariant stated as an invariant —
**"No message the SDK has already handed us is ever discarded"** — followed by
the two concrete ways it was violated, and an explicit note that it is stated
this way *because reasoning about which interleavings are possible has been wrong
twice here*. The `asked` branch comment no longer claims anything is impossible;
it says we do not reason about whether a message arrived, we just look. The
false "nothing new can arrive between there and here" assertion is gone.

## Fake extension

`fake_sdk_asking.py` only (`fake_sdk.py` untouched, per the builder constraint):

- `preface` accepts a str **or a list**, for several messages in one read-loop
  pass.
- new `during_drain`, delivered from inside the generator after
  `_DURING_DRAIN_TURNS` scheduler turns.
- both reachable from the scripted dict via `preface_texts` / `during_drain`, so
  the shared contract script is unchanged.

## Revert-verification of each new test

| Reverted | Result |
|---|---|
| `sweep()`'s `wait(timeout=0)` removed (Residual 1) | `test_every_message_buffered_before_a_question_survives` **and** `test_a_message_arriving_during_the_queue_drain_survives` fail |
| post-drain `sweep()` removed (Residual 2) | `test_a_message_arriving_during_the_queue_drain_survives` fails (alone) |
| both restored | 26 passed |

Each new test therefore fails for its own fix, and the drain test additionally
depends on the first — which is correct, since recovering a mid-drain message
requires both sweeping after the drain and sweeping to exhaustion.

## Commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
26 passed in 0.28s
```
(was 24; +2)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.23s
```

```
$ cd backend && .venv/bin/python -m pytest -q
602 passed, 1 warning in 12.29s
```
600 -> 602, exactly the two new tests. Protected files show zero diff; no
"Task was destroyed but it is pending!" noise.

## Concerns after this round

1. **This function has now been wrong twice about interleavings, and both times
   the tests looked complete.** The invariant is stated and enforced by looking
   rather than reasoning, and the sweep is exhaustive on every exit path, which
   is the strongest form available without a real-CLI integration test. But
   `_pump` is the highest-risk code in this file and I would treat any future
   edit to it as requiring a fresh turn-by-turn trace, not just a green suite.
2. **First round's Concern 2 still stands** — the resume path relies on
   `Query`'s anyio buffer ownership, an SDK internal. This round sharpens why it
   matters (the whole bug class exists because `send_nowait` bypasses that
   buffer for a parked receiver). The reviewer's live-CLI round trip is
   reassuring, and Task 9 should keep it in the deploy checklist.
3. **Deferred, from the reviewer, not fixed here (noted so it is not lost):**
   `disconnect()` clears only in-memory pending state, so the S3 record survives
   and `pending()` still advertises an unanswerable question after teardown.
   Fixing it means an `await clear_pending` in `disconnect()`; I left it because
   the coordinator scoped it out, but it is a real user-visible defect once
   Task 8 wires `disconnect()` into `runner.stop()`.
4. **Also deferred:** in production this whole bug class was partly masked by S3
   latency inside `_save_pending_quietly` — which does **not** apply when
   `session_id` is empty, since that save is skipped entirely. The fix removes
   the dependence on that accident either way.

---

# Fix round 3 — the `yield done` window, double `done`, and terminal-event ordering

All three reproduced before changing code, on a real anyio memory-object stream
driving the real driver. A fourth defect — introduced by this round's own fix —
was found while trying to isolate the third; it is fixed and covered too.

## CRITICAL — `yield done` was an unswept window holding a parked receiver

**Confirmed, and worse than "sometimes": it lost the message at every timing
tried, including `s3_sync=0`.** Instrumenting the stream state on the send makes
the mechanism unambiguous:

```
waiting_receivers 1->0, buffer 0->0     <- handed straight to the parked receiver
answers turn=[('done', None)]           -> LOST
```

The post-drain sweep returns *because* it just gave `next_msg` its scheduler
turn — which is what registers it as an anyio `waiting_receiver`. `_pump` then
suspends on `yield done` with that receiver parked; the consumer
(runner.py:134-140) awaits a real S3 workspace sync before returning; the CLI's
next message is handed to the parked receiver, bypassing the buffer; the
`finally` cancels it and the message is gone — not in the buffer for
`_continue_after_answers`'s fresh iterator either.

**Fix** (the reviewer's, verified independently here): `retire()` — cancel the
receive *before* suspending. anyio skips a receiver with a pending cancellation
and buffers the item instead (`memory.py:223-231`, read to confirm). After:

```
waiting_receivers 1->0, buffer 0->1     <- buffered
answers turn=[('message','post-done prose'), ('done', None)] -> RECOVERED
```

at `s3_sync` = 0, 20, 50, 200 ms. The `finally` still calls `retire()`, which is
what closes the generator-abandoned exit by construction rather than by luck.

The docstring's false claim ("every path out of the loop sweeps first") is
replaced with the accurate statement: sweeping is what *arms* a receiver, so any
suspension between the last sweep and the cancel is a window, and the fix is
structural.

## IMPORTANT — double `done`

**Confirmed:** `['message', 'done', 'questions']` — the sweep translated a
ResultMessage into `done`, and the terminal yield added a second.

**Fix.** `sweep()` no longer yields the `done` that `_translate` produces; it
only sets `ended`. There is now a single exit that emits exactly one terminal
event, so the two cases (question raised / turn ended) cannot both contribute
one. That also removes the need for the reviewer's `if not ended:` guard — the
terminal event has one and only one origin.

## IMPORTANT — queue events pushed past `done`

**Confirmed:** `['message', 'done', 'stage']`. Since `sse.ts:29` closes the
EventSource on `done`, that `stage` never reaches `onEvent`, so
`useWorkspaceStream.ts:134-137` never appends it to the stage sidebar; a dropped
`document` would leave `setLastDocument`/`setActiveDoc` stale.

**Fix.** The terminal path drains the queue *before* the terminal event. Now
`['message', 'stage', 'done']`.

Isolating this one took real work, and it is where I nearly repeated a past
mistake. Instrumenting the pre-terminal drain showed it firing **zero** times
across the whole suite and my first two probes — the same evidence that led me
to wrongly delete the post-drain sweep in round 2. So this time I did not trust
it, and probed for reachability directly: with the terminal sweep yielding a
message (the `during_drain` shape) and a tool emitting during that yield, the
event is emitted correctly with the drain and **dropped entirely without it**.
Reachable and load-bearing. The lesson from round 2 held: absence of firing
under one scenario is not unreachability.

## Fourth defect, introduced by this round's own fix — the last message was
## translated twice

Found while probing the above. After `ended`, `next_msg` still holds the
consumed ResultMessage (it is deliberately not re-armed), so the terminal
sweep handed that same message to `_translate` a second time. Harmless for the
stock translation, which only yields `done` — but visible the moment a message
carries a side effect, which is exactly how I found it: a duplicated `stage`,
`['message','stage','stage','done']`. Guarded with an early `return` in
`sweep()` when `ended`. Covered by
`test_the_final_message_of_a_turn_is_translated_only_once`, which counts
`_translate` calls rather than asserting on output, so it catches the side-effect
class rather than one instance of it.

## `_DURING_DRAIN_TURNS` re-verification, and its removal

The reviewer flagged that the band was narrow [2,3] and would silently
re-vacuate if `_pump`'s await count changed — which this round changes. It did:
re-measuring after the restructure gave a band of exactly **{5}**, a single
value. The `drain_window_hit` self-check added this round caught that
immediately (the test failed loudly rather than passing vacuously), which is
what it was for.

Rather than re-tune a one-wide constant, the fake now finds the window by
**observing driver state** — the question is pending and no longer on the queue,
i.e. the driver is mid-drain — with a 50-turn bound only as a hang guard. The
constant is gone, so there is nothing left to re-tune. The test still asserts
`drain_window_hit` first, so an unreachable window fails loudly.

The fake also moved from a list to a **real anyio memory-object stream**, because
the entire bug class lives in anyio's delivery semantics (parked-receiver handoff
vs. buffering) and a list-based fake cannot exhibit either — it literally cannot
distinguish a correct driver from one that loses messages.

## Revert-verification (each fix, independently)

| Reverted | Failing test |
|---|---|
| `retire()` before the terminal yield | `test_a_message_arriving_during_the_post_done_sync_is_not_destroyed` |
| `sweep()` yields its own `done` again | `test_a_question_turn_yields_exactly_one_terminal_event` |
| pre-terminal queue drain removed | `test_queued_tool_events_are_emitted_before_the_terminal_event` |
| `ended` guard in `sweep()` removed | `test_the_final_message_of_a_turn_is_translated_only_once` |

Each fails alone for its own fix; all restored -> 30 passed.

## Commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
30 passed in 0.34s
```
(was 26; +4)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.25s
```

```
$ cd backend && .venv/bin/python -m pytest -q
606 passed, 1 warning in 12.21s
```
602 -> 606, exactly the four new tests. Protected files show zero diff; no
"Task was destroyed" / "never awaited" warnings. Busy-wait re-checked: a question
turn costs 3.1 ms CPU.

## Concerns after this round

1. **`_pump` has now produced a defect in three consecutive rounds, including
   one in this round's own fix.** Each was a different suspension point in the
   same class. The invariant is now enforced structurally rather than by
   reasoning about interleavings (`retire()` before suspending; a single terminal
   exit; queue before terminal), which is the strongest available without an
   integration test — but I would not treat any future edit here as safe on the
   strength of a green suite. The exit enumeration is written into the docstring
   so the next editor starts from the list rather than rediscovering it.
2. **"It never fires under instrumentation" is not evidence of unreachability.**
   That reasoning cost round 2 a real bug and nearly cost this round the
   ordering fix. Both times, probing for reachability directly (construct the
   interleaving; check whether removing the code changes behaviour) gave the
   right answer. Recorded here because it is the single most useful lesson from
   this task.
3. **First round's Concern 2 still stands** and is now the last unverified
   assumption: the resume path depends on `Query`'s anyio buffer ownership. This
   round makes the dependence more precise — correctness now rests on
   *buffered-not-handed*, which `retire()` secures — but it is still SDK-internal
   and only the live-CLI round trip in Task 9's checklist can close it.
4. **Deferred, unchanged from round 2:** `disconnect()` leaves the S3 pending
   record, so `pending()` still advertises an unanswerable question after
   teardown (one `await clear_pending` fixes it, once Task 8 wires
   `disconnect()`).

---

# Fix round 4 — the class eliminated, not the fourth window patched

All three findings were reproduced against HEAD (`c6def54`) before any code
changed, with the reviewer's exact numbers. Then I **restructured** rather than
adding a fourth `retire()`, because two probes showed the current shape has no
correct version.

## The two probes that decided the approach

Both on a real anyio memory-object stream, both reproducible.

**1. `retire()`-then-re-arm is impossible, not merely untested.** The reviewer
flagged the structural fix as "retire before every suspension and re-arm after"
and noted cancel-then-re-arm was untested here. It does not work at all —
cancelling `agen.__anext__()` **closes the async generator**:

```
armed:                         (1, 0)
after retire+send:             (0, 1)      <- buffered, as round 3 found
RE-ARM RESULT: StopAsyncIteration — the cancel CLOSED the generator
fresh iterator got:            m1          <- only a NEW iterator can read it
```

So every suspension in `_pump` needs a *fresh iterator* afterwards, and a fresh
iterator cannot be created without knowing the old one is finished. There is no
safe suspension protocol for a peek future. That is why each round closed one
window and left or opened another: the queue drain, the second sweep, `yield
done`, the pre-terminal drain, the abandoned exit — five instances of one
unfixable shape.

**2. A reader task nobody cancels loses nothing**, including after the consumer
walks away:

```
reader parked:                    (1, 0)
inbox after the consumer left:    ['m1', 'm2']
inbox later:                      ['m1', 'm2', 'm3']
```

That is the lever the peek future never had. So: **`_MessageReader`** — a task
that owns the turn's `receive_response()` iterator, never stops reading, and
appends to a plain inbox on the driver. `_pump` consumes only from `inbox`.

**Why this eliminates the class rather than patching a window.** The bug class
is "a message handed to a receiver that is later cancelled". After the change
`_pump` owns **no cancellable receive at all**, so no suspension of `_pump` —
`yield`, `await`, or the `GeneratorExit` of an abandoned generator — can destroy
a message. There is no window list to keep correct. Three defects collapse into
properties:

| Old failure mode | Now |
|---|---|
| message destroyed at some suspension | pump has nothing cancellable to destroy |
| unread messages lost on abandonment | they are in OUR inbox, relayed by the answers turn |
| last message translated twice | popped exactly once; double side effects unrepresentable |

The one cancellable await left is `settle()`, which waits on an `asyncio.Event`.
An Event carries no payload, so cancelling it cannot lose anything.

The two properties the brief required stay verified: **exactly one terminal
event, last, on all 15 paths** (table below), and **queue events precede the
terminal event** (its own test, revert-verified).

## CRITICAL — `retire()` one line too late

**Reproduced exactly as reported**, with nothing but `await asyncio.sleep(0)` on
the consumer:

```
TRACE: ['queued file_changed', ('(parked,buffered) before->after send', (1, 0), (0, 0))]
TURN KINDS:   ['message', 'questions', 'file_changed', 'done']
ANSWERS TURN: [('done', None)]
MESSAGE SURVIVED ANYWHERE: False
```

I did **not** apply the verified one-line fix (`retire()` above the drain).
It is correct for this instance, but it is the fourth placement of the same
band-aid and probe 1 shows a fifth window is always available. After the
restructure:

```
TURN KINDS:   ['message', 'questions', 'file_changed', 'message', 'done']
MESSAGE SURVIVED ANYWHERE: True
```

The queued `file_changed` still precedes `done`, and the message now comes out
in the same turn instead of merely surviving into the next one.

**Tests:** `test_a_message_arriving_while_a_queued_tool_event_is_yielded_survives`
(the reviewer's exact trigger: a tool event queued in the same burst as the
question) and `test_a_message_arriving_while_the_question_is_yielded_survives`.

## IMPORTANT — the abandoned exit

**Reproduced**; the report's "closed by construction" claim was indeed false —
`retire()` cannot un-hand a delivery that already happened:

```
STATS before->after send: (1, 0) -> (0, 0)
ABANDONED-SURVIVED: False
```

Fixed by the same restructure, with no extra code: the reader is deliberately
**not** cancelled when a question parks the turn, so it keeps collecting through
the disconnect. After:

```
ABANDONED KINDS: ['message', 'questions'] -> [('message', '중단 직전 도착'), ('done', None)]
ABANDONED-SURVIVED: True
```

**Test:** `test_a_message_arriving_as_the_turn_is_abandoned_survives` — asserts
both a message delivered *at* abandonment and one delivered *after* the consumer
is gone are relayed by the answers turn, and that the turn still terminates.

## IMPORTANT — the window-finding predicate

**Reproduced** the reviewer's slow-S3 result exactly:

```
SLOW-S3 KINDS: ['message', 'message', 'questions', 'done']   <- landed PRE-drain
SLOW-S3 drain_window_hit: True                               <- reports a hit anyway
```

I found a **third** false positive while confirming it: the predicate is still
True after the whole turn has ended (`predicate AFTER the whole turn: True`),
and its firing was never really an observation — it depended on the old
`sweep()`'s incidental `asyncio.wait(timeout=0)` giving the fake a scheduler
turn. Sampling it per frame:

```
predicate sampled at each yield: [('message', False), ('questions', True), ('done', True)]
drain_window_hit: False          <- with a non-awaiting consumer
drain_window_hit: True           <- same driver, consumer does sleep(0)
```

So the fragility had not been removed in round 3, only moved from a constant
into a predicate that was hostage to await counts in *two* functions.

**Fix: deleted the predicate and the `during_drain`/`_DURING_DRAIN_MAX_TURNS`/
`drain_window_hit` machinery.** Mid-turn delivery is now driven from the
**consumer**, inside the test's own `async for`, where the driver is provably
suspended at that exact `yield` — nothing is inferred and nothing can go
vacuous. The fake no longer needs a `driver` back-reference at all.

**The reviewer predicted this would expose tests relying on the false positive,
and it did.** Both `during_drain` tests failed the moment the predicate went
away, including `test_queued_tool_events_are_emitted_before_the_terminal_event`
— the one measured to be vacuous. Both were rewritten against consumer-driven
delivery and both are revert-verified below.

## A fourth defect, found by my own mutation run

Mutating away `_retire_reader()` in `_stream` left all 32 tests green, so I
probed reachability by construction rather than trusting either result. It is
load-bearing, and the failure is worse than a lost message:

```
### WITH the fix
reader1 cancelled by turn 2's query(): True
turn 2 events: [('message','새 턴 문장 1'), ('message','새 턴 문장 2'), ('done', None)]

### WITHOUT it
reader1 cancelled by turn 2's query(): False
turn 2 events: [('message','새 턴 문장 2'), ('<HUNG>', None)]
reader1 inbox (what it STOLE from turn 2): ['AssistantMessage', 'ResultMessage']
```

Path: question turn → answers turn abandoned mid-relay → the future is resolved,
so a new user message reaches `query()`. Two readers then compete on one anyio
stream, and anyio hands each item to the **first** parked receiver
(`waiting_receivers` is an OrderedDict popped `last=False`) — the stale one. It
steals the turn's `ResultMessage`, so the turn **never terminates**, which hangs
`runner.py`'s loop and the SSE client — the failure the brief calls worse than a
double `done`.

**Test:** `test_a_new_turn_does_not_lose_messages_to_the_previous_reader`. Its
first draft passed for the wrong reason (answering delivered the `ResultMessage`
instantly, so the reader was already finished); the fake gained
`turn_continues_after_answer` to model the real case where the model keeps
working for seconds after an answer.

Two more mutants I ran that were *correctly* green, both now documented:
`settle()`'s second post-`clear()` re-check was dead code (there is no `await`
between the two checks) — removed, with the ordering argument written down; and
reader-error propagation is already covered by the contract's `raise` script.

## Revert-verification — every new/changed test, each against its own fix

| Reverted | Failing test(s) |
|---|---|
| reader not carried across the question (the old shape's defining property) | `..._post_done_sync_is_not_destroyed`, `..._as_the_turn_is_abandoned_survives`, `test_answers_reach_the_sdk_as_the_tool_result` |
| terminal harvest → single pass (round 3's ordering shape) | `test_queued_tool_events_are_emitted_before_the_terminal_event` |
| terminal harvest removed entirely (round 3's CRITICAL shape) | `..._while_the_question_is_yielded_survives`, `..._while_a_queued_tool_event_is_yielded_survives`, `test_queued_tool_events_...` |
| `_retire_reader()` removed from `_stream` | `test_a_new_turn_does_not_lose_messages_to_the_previous_reader` |
| `sweep`/harvest yields its own `done` again | `test_a_question_turn_yields_exactly_one_terminal_event` |
| inbox never popped (message consumed twice) | hangs the suite (detected; `..._translated_only_once` never completes) |
| reader errors swallowed instead of raised | `test_claude_driver_satisfies_the_same_contract` |

Verified by running each, not asserted. All restored → 33 passed.

## Exactly one terminal event — re-verified on 15 paths

```
OK  1 text turn                     n=1 ['message', 'done']
OK  2 question turn                 n=1 ['message', 'questions', 'done']
OK  3 sdk raises                    n=1 ['error']
OK  4 rules missing                 n=1 ['error']
OK  5 concurrent rejected           n=1 ['error']
OK  6 answer-first short-circuit    n=1 ['message', 'questions', 'done']
OK  7 live answers                  n=1 ['done']
OK  8 stale iid (live)              n=1 ['error']
OK  9 restart answers               n=1 ['message', 'done']
OK 10 no pending                    n=1 ['error']
OK 11 stale iid (restart)           n=1 ['error']
OK 12 followup during answers       n=1 ['questions', 'done']
OK 13 stream closed mid-turn        n=1 ['done']
OK 14 answers with no reader        n=1 ['error']
OK 15 s3 save broken                n=1 ['message', 'questions', 'done']

ALL PATHS EXACTLY ONE TERMINAL EVENT, LAST: True (15/15)
```

Paths 13/14 are new with the restructure. 14 is a defensive branch (`_reader is
None` on the live-future path) reported as a turn failure rather than a silent
empty turn, since the user's answers have nowhere to go.

## Commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
33 passed in 0.36s
```
(was 30; +3 net — 4 added, 1 replaced by a consumer-driven equivalent)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.24s
```

```
$ cd backend && .venv/bin/python -m pytest -q
609 passed, 1 warning in 12.63s
```
606 → 609. `git diff HEAD` shows zero changes to `driver.py`, `strands_tools.py`,
`proto/builder.py`, `runner.py`, `driver_contract.py`. No "Task was destroyed" /
"never awaited" / "never retrieved" output anywhere in the suite (grep count 0);
the long-lived reader task is cleaned up at loop close. A question turn costs
2.8 ms CPU (was 3.1).

## Concerns after this round

1. **The peek-future shape is gone, and with it the window enumeration** — the
   defect class of rounds 1-3 is now unrepresentable rather than guarded. What
   replaces it is a much smaller obligation: `_retire_reader()` must be called
   exactly where a turn is *replaced* or torn down (two call sites, `_stream`
   and `disconnect`) and nowhere else. My own mutation run found the one
   untested call site, so both are now covered — but this is the property a
   future editor must not break, and it is stated in `_retire_reader`'s
   docstring for that reason.
2. **`_MessageReader` outlives the `run()` generator by design.** That is what
   fixes the abandoned exit, and it means a parked question keeps one task and
   one inbox alive per driver until the question is answered, a new turn starts,
   or `disconnect()` runs. Bounded (one reader per driver, replaced not
   accumulated) and verified not to leak into loop close, but it is a real
   lifetime change from "everything dies with the generator", and the leak is
   now closed by `disconnect()` — which is still **unwired** until Task 8.
3. **The `Query`-anyio-buffer dependence from round 1's Concern 2 is gone.**
   Correctness no longer rests on any claim about where the SDK buffers unread
   messages, because the driver holds them itself. The remaining SDK-internal
   assumption is far weaker: that `receive_response()` can be iterated once to
   completion per turn, which is its documented contract. The live-CLI round
   trip in Task 9's checklist is still worth doing, but it is no longer
   load-bearing for this bug class.
4. **"Instrumentation shows zero calls" cost this task two rounds, and my own
   mutation run reproduced the trap in miniature** (a surviving mutant that
   looked like dead code but was a hang waiting to happen). Both times the
   answer came from constructing the interleaving and diffing behaviour with and
   without the code — not from observing whether it fired.
5. **Deferred, unchanged from rounds 2-3:** `disconnect()` leaves the S3 pending
   record, so `pending()` still advertises an unanswerable question after
   teardown (one `await clear_pending` fixes it, once Task 8 wires it).

---

# Fix round 5 — the ownership rule, and the batch pop that broke it

## The finding is correct, and it is mine

Reproduced end-to-end through the real `runner.AgentRunner` before changing
anything, in both loops:

```
=== HARVEST batch-pop (regression)
  consumer saw: ['문장 1']
  LOST MESSAGES: ['문장 2', '문장 3']
=== DRAIN_QUEUE batch-pop (pre-existing, same shape)
  consumer saw: [('message', None), ('file_changed', 'doc1.md')]
  LOST QUEUE EVENTS: ['doc2.md', 'doc3.md']
```

**Diagnosis.** Round 4 moved message ownership out of anyio's buffer and into
the driver, which is what killed the abandoned-receive class. But `harvest()`
then popped the whole inbox into a **local list** before yielding any of it, so
the not-yet-yielded remainder lived in the generator's frame — and
`GeneratorExit` destroys frames. The reviewer's framing is exactly right: I
moved the items from a place with the wrong owner to a place with *no* owner.

And it directly contradicted the invariant I had written one round earlier
("whatever is still in `inbox` when this generator ends is relayed by the next
pump"). The batch pop is what made that false. I have fixed the claim as well as
the code: `_MessageReader`'s docstring now carries an explicit NOTE that moving
ownership onto the reader protects an event **only while it is in
`inbox`/`outbox`**, and that copying it into a local list puts it back in a
frame `GeneratorExit` destroys.

## The rule, stated so it can be checked by reading

`_pump`'s invariant 2 is now one sentence a future editor can check without
reasoning about interleavings:

> An event is in exactly one place that outlives this generator, and it leaves
> that place only AFTER the consumer has received it.

Enforced by three things, each independently revert-verified:

1. **`reader.outbox`** — translated-but-undelivered events live on the *reader*,
   not in the pump's frame. Its lifetime is exactly the turn's, so
   `_retire_reader` disposes of a dead turn's undelivered events and a new turn
   starts empty.
2. **`translate_into_outbox()`** is wholly synchronous, so a message is never in
   neither place: it leaves `inbox` and enters `outbox` with no suspension
   between.
3. **`relay()` pops after the `yield` resumes**, not before — `queue[0]`, yield,
   then `pop(0)`. Reaching the line after the `yield` *is* the proof the consumer
   received the item.

**Yes, I unified the two loops.** They were the same shape and, per the
coordinator's note, two half-fixes of one bug is how this got to round five.
`relay()` iterates `(reader.outbox, self._queue)` with identical semantics, and
the error paths use a shared `_relay_queue()` helper. `drain_queue()` is
**deleted** — it had no remaining caller on this driver, and leaving a method
whose shape *is* the bug is an invitation to reintroduce it at the next call
site. A comment marks the deliberate divergence from `builder.py:183`.

## Why pop-after-delivery rather than pop-before

Probed, because it is the one real trade-off:

```
A pop-BEFORE-yield: delivered ['a'] still owned ['c']
A  -> 'b' produced but never received; recoverable? False
B pop-AFTER-resume: delivered ['a'] still owned ['b', 'c']
B  -> every unreceived item recoverable? True
```

A consumer can be cancelled at its `__anext__` await *after* the generator
produced the value — the same shape as anyio's parked receiver. So
pop-before-yield loses the in-flight item, and at-least-once is the only safe
choice. The cost is that the item being delivered at the moment of abandonment
may be delivered twice.

**One consequence of that needed its own fix, and it was user-visible.** A
re-delivered `questions` event re-shows a card the user has already answered,
and answering it again is refused (`no pending questions`) because the future is
gone:

```
answers turn: [('message', None), ('questions', 'c6a1f89b…'), ('done', None)]
answering the re-shown card -> [('error', 'no pending questions')]
```

`run_answers` now drops any still-owned `questions` event for the round it is
answering. That is not a loss: the answers this call carries are that event's
entire purpose, so it has been *fulfilled*. Verified the fix does not truncate
the turn — the post-answer prose still arrives, and the terminal event is still
exactly one.

Duplicates of `message`/`stage`/`file_changed` are left alone deliberately: the
frontend appends them (`useWorkspaceStream.ts:134-148`), so at worst a line
repeats in the transcript, which is strictly better than losing the model's
prose. Flagged as a concern rather than papered over.

## Revert-verification — each new test against its own fix

| Reverted | Failing test(s) |
|---|---|
| `relay()` batch-pops into a local list (the exact round-4 regression) | all three of `..._messages_not_yet_yielded_...`, `..._queued_tool_events_not_yet_yielded_...`, `..._already_answered_question_card_...` |
| pop **before** the yield instead of after | same three |
| `outbox` a local list instead of on the reader | `..._messages_not_yet_yielded_...`, `..._already_answered_question_card_...` |
| stale-`questions` filter removed | `..._already_answered_question_card_is_not_re_shown` (alone) |
| error paths use `drain_queue()` batch pop again | `test_the_error_path_does_not_strand_queued_events_either` (alone) |

All restored → 37 passed. Verified by running each, not asserted.

The error-path mutant **initially survived** — nothing covered it. Rather than
commit unfalsifiable polish I probed for reachability by construction and found
the shape that reaches it: a previous turn abandoned mid-relay leaves items
owned in `_queue` (now possible *because* of this round's fix), then the next
turn fails at `query()`, before any `_pump` exists to relay them. Batch pop
strands `doc2`/`doc3` there; `_relay_queue` does not. Now covered.

New tests go through the **real `AgentRunner`**, per the reviewer: that is the
component that abandons the generator in production, and it adds the extra
generator layer `GeneratorExit` must propagate through. They also wait out
`_reconnect_gap()` — `aclose()` runs only the outermost generator's `finally`
synchronously (round 1's IMPORTANT 3 measured the same), and a browser reconnect
is a network round trip, so this is honest rather than an artificial delay.

## Terminal-event property re-verified

16/16 paths yield exactly one terminal event, last — the 15 from round 4 plus a
new one for this round's shape (abandoned question turn → reconnect → answers
turn relays the leftovers):

```
ALL PATHS EXACTLY ONE TERMINAL EVENT, LAST: True (16/16)
```

## Commands and output

```
$ cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q
37 passed in 0.36s
```
(was 33; +4)

```
$ cd backend && .venv/bin/python -m pytest tests/test_agent_driver.py tests/test_strands_driver_contract.py tests/test_proto_builder.py -q
34 passed in 0.25s
```

```
$ cd backend && .venv/bin/python -m pytest -q
613 passed, 1 warning in 12.47s
```
609 → 613, exactly the four new tests. `git diff HEAD` shows zero changes to
`driver.py`, `strands_tools.py`, `proto/builder.py`, `runner.py`,
`driver_contract.py`. No "Task was destroyed" / "never awaited" / "never
retrieved" output anywhere (grep count 0). Probe files removed.

## Concerns after this round

1. **Event delivery is now explicitly at-least-once at the abandonment
   boundary.** The item in flight when the consumer disappears is re-delivered
   on the next turn. This is a deliberate trade — probe A shows at-most-once
   loses that item outright — and `questions` is special-cased because a
   duplicate there is user-visible and unanswerable. `message`, `stage`,
   `document` and `file_changed` can therefore repeat once after an SSE drop.
   The frontend appends rather than replaces, so the visible effect is a
   repeated transcript line or stage entry, never a wrong state; and losing the
   model's prose would be worse. If a duplicate turns out to matter for
   `document` (it calls `setLastDocument`/`setActiveDoc`), the fix is an event
   id and consumer-side dedupe, which is a frontend change and out of scope
   here.
2. **`drain_queue()` is gone from this driver** while `builder.py` keeps its
   own. That is an intentional divergence from the reference implementation,
   marked with a comment, on the grounds that the batch-pop shape is the defect.
   `builder.py` is a protected file and has the same latent issue at its four
   call sites; I did not touch it, but whoever revisits the two drivers should
   know it is there.
3. **Round 4's Concern 1 stands, narrowed.** The remaining obligation for a
   future editor is two rules, both now written where they are enforced: call
   `_retire_reader()` only where a turn is replaced or torn down, and never move
   an event out of `outbox`/`_queue` before the consumer has received it.
4. **Unchanged deferral:** `disconnect()` leaves the S3 pending record, so
   `pending()` still advertises an unanswerable question after teardown (one
   `await clear_pending`, once Task 8 wires `disconnect()`).
