# Task 4 Report: SDK 질문 입력 → QuestionFile 변환 통합

## What was implemented

1. **`backend/pathfinder/agent/questions_payload.py`**: added
   `question_file_from_sdk(sdk_questions: list[dict], *, name: str) -> dict`
   at the end of the module, exactly as specified in the brief. It maps each
   SDK `AskUserQuestion` option to a letter (`_LETTERS` indexed by SDK option
   order), assembles option text with
   `f"{label} — {desc}".rstrip(" —") if desc else label`, carries `header` →
   `category` and `multiSelect` → `multi_select`, then delegates the final
   payload to the existing `normalize_questions_payload()` — which enforces
   the real contract (letter dedup, `is_other` collapsing, required-field
   validation, `ValueError` on unusable input) rather than reimplementing any
   of it.

2. **`backend/pathfinder/proto/builder.py`**:
   - Removed the old `_to_question_file` function (previously at lines
     37–52).
   - Added `from pathfinder.agent.questions_payload import
     question_file_from_sdk` to the imports.
   - Changed the call site in `_on_can_use_tool` from
     `qfile = _to_question_file(sdk_questions)` to
     `qfile = question_file_from_sdk(sdk_questions, name="prototype-questions")`.
   - Left the module-level `_LETTERS = "ABCDEFGHIJ"` constant in `builder.py`
     untouched — it is still used elsewhere in the file (line 212,
     `_answer_to_sdk`, unrelated to question-file construction), so it was
     not part of this merge and removing it would have been out of scope.

3. **`backend/tests/test_questions_payload.py`**: appended the exact test
   block from the brief (Step 1), covering: letter ordering, label/description
   join, empty-description dash-dropping, header→category and
   multiSelect→multi_select mapping, file-level contract fields
   (`name`/`parse_ok`/`raw_markdown`), idempotence under
   `normalize_questions_payload`, and rejection of a question with no options.

## Verification (no builder regression)

Followed the TDD order specified in the brief:

- **Before implementing**: ran
  `cd backend && .venv/bin/python -m pytest tests/test_questions_payload.py -q`
  → failed as expected with
  `ImportError: cannot import name 'question_file_from_sdk' from 'pathfinder.agent.questions_payload'`.
- **After implementing `question_file_from_sdk`** (before touching
  `builder.py`): re-ran `tests/test_questions_payload.py` alone →
  **21 passed** (14 pre-existing normalizer tests + 7 new ones).
- **After switching `builder.py`** to call the new function and deleting
  `_to_question_file`: ran
  `tests/test_questions_payload.py tests/test_proto_builder_questions.py -q`
  → **28 passed**, no failures. `test_proto_builder_questions.py` in
  particular still asserts `qf["parse_ok"] is True`, `q["number"] == 1`,
  `q["text"] == "Which DB?"`, `[o["letter"] for o in q["options"]] ==
  ["A", "B"]`, and `q["options"][0]["text"].startswith("Postgres")` — all of
  which passed unchanged, confirming the merged function reproduces the old
  `_to_question_file` output exactly for this case, including the
  `"prototype-questions"` name argument.
- **Full backend suite**: `cd backend && .venv/bin/python -m pytest -q` →
  **584 passed**, 1 unrelated deprecation warning (httpx/starlette), no
  failures, no regressions anywhere else in the codebase.

## Deviations from the brief

None. Implementation matches the brief's code blocks verbatim (function
body, import line, call-site change, test additions, commit message body).

## Concerns

- None material. The merge is a straightforward extraction — semantics for
  the builder's existing test case are provably unchanged (regression suite
  green), and the new function now runs through `normalize_questions_payload`,
  so the `is_other`-duplicate-collapsing fix (previously Discovery-only) now
  also protects the prototype-builder path, as intended by this task.
- `builder.py`'s own `_LETTERS` constant is now unused for question-file
  construction but is still used by `_answer_to_sdk` for letter→index
  lookups on answers; it was intentionally left in place since consolidating
  it further was not in scope for this task.

## Fix round (code review findings)

Started from a clean tree at `c638186` (Task 4's own commit) after the prior
implementer died to an API timeout before writing any of these fixes.

### Finding 1 (Important) — `_looks_like_other` heuristic leaking into the SDK path

**Problem confirmed by reading the whole module first**: `_normalize_options`
in `backend/pathfinder/agent/questions_payload.py` always ORs the model's
`is_other` flag with `_looks_like_other(letter, text)` — a text-sniffing
heuristic ("does the text start with 'other'?") designed for the
markdown/Discovery path, where the model hands over a free-form dict it might
get wrong. Once `question_file_from_sdk` started delegating to
`normalize_questions_payload` (Task 4's merge), that same heuristic now also
runs over SDK `AskUserQuestion` options, which are already structured and
explicit — a legitimate option labeled e.g. "Other database" would be
silently reclassified as the free-text Other slot, losing its real label to
the hardcoded "Other — 직접 입력" and breaking `_answer_to_sdk`'s
label-lookup (it would hand the model raw typed text instead of the option it
actually defined).

**Fix**: added a `guess_other: bool = True` keyword to
`normalize_questions_payload`, `_normalize_question`, and `_normalize_options`.
When `False`, the two `_looks_like_other(...)` calls inside
`_normalize_options` are skipped — the option's `is_other` becomes exactly
`bool(raw.get("is_other"))` — while everything else (letter dedup, the
"collapse to a single Other, keep only the last" logic, blank-text backfill
for a demoted Other, all downstream validation) still runs unchanged.
`question_file_from_sdk` now calls
`normalize_questions_payload(..., guess_other=False)` since every SDK option
it builds already has `is_other: False` set explicitly — there is nothing to
guess. The default stayed `True` everywhere else, so `ask_questions` (the
markdown/Discovery entry point in `tools.py`, which calls
`normalize_questions_payload` with no `guess_other` argument) is byte-for-byte
unaffected — confirmed by running the existing heuristic tests in
`test_questions_payload.py` (`test_treats_an_other_prefixed_text_as_other`,
`test_collapses_a_duplicate_other_into_a_real_option`,
`test_keeps_only_the_last_other_when_several_look_like_other`, etc.), all
still green.

Added `test_an_option_literally_labeled_other_is_not_reclassified` to
`backend/tests/test_questions_payload.py`: builds an SDK question with two
options, one labeled `"Other database"` with a description, and asserts it
survives `question_file_from_sdk` with `is_other is False` and
`text == "Other database — specify your own"` (label+description joined
intact, not replaced).

### Finding 2 (Important) — unguarded `ValueError` in `_on_can_use_tool`

**Problem**: `question_file_from_sdk` raises `ValueError` on unusable SDK
input (e.g. a question with an empty `options` list — enforced by
`normalize_questions_payload`'s "at least one selectable option" check). The
deleted `_to_question_file` never raised anything, so `_on_can_use_tool` in
`backend/pathfinder/proto/builder.py` had no exception handling around the
call. Prototype builds run under `bypassPermissions` with no operator
watching, so an uncaught `ValueError` here would propagate out of the SDK's
`can_use_tool` control-request handling with no recovery path — worse than
Discovery's equivalent path, where `ask_questions` in
`backend/pathfinder/agent/tools.py` already catches exactly this `ValueError`
and returns a string telling the model what was wrong (`QUESTIONS_SCHEMA_HINT`
+ retry instruction) so the turn continues instead of dying.

**Fix**: wrapped the `question_file_from_sdk(...)` call in `_on_can_use_tool`
in a `try/except ValueError`. Read the call site's return contract first —
every other path in `_on_can_use_tool` returns a `PermissionResult`
(`PermissionResultAllow`), because that is what the SDK's `can_use_tool`
callback contract requires (verified in
`.venv/lib/python3.11/site-packages/claude_agent_sdk/_internal/query.py`:
the handler does `isinstance(response, PermissionResultAllow)` /
`isinstance(response, PermissionResultDeny)` and raises `TypeError` for
anything else — a bare string, unlike `tools.py`'s `ask_questions`, is not a
legal return value here). So the fix returns `PermissionResultDeny(message=...)`
on `ValueError` — the SDK-native way to hand the model an explanation it can
read and retry from, consistent with the existing shape at this call site
rather than inventing a new one. The deny message mirrors `ask_questions`'s
tone: states the question couldn't be built, includes the underlying
`ValueError` text, and tells the model to retry `AskUserQuestion` with at
least one option per question. The exception is also logged via `_log.warning`
(matching `ask_questions`'s `_log.warning("ask_questions payload rejected: %s", e)`).
No new return shape, no interrupt flag set (`interrupt=False`, the dataclass
default) — this is a same-turn retry signal, not a turn abort.

Added `test_zero_option_question_is_denied_not_raised` to
`backend/tests/test_proto_builder_questions.py`: calls
`b._on_can_use_tool("AskUserQuestion", {"questions": [{"question": "q",
"options": []}]}, None)` directly and asserts the result is a
`PermissionResultDeny` with a non-empty `message`, and that `_pending_payload`
is `None` (no exception escaped, no question got stuck as pending, and
`test_proto_builder_questions.py`'s existing coverage of the "happy path"
`PermissionResultAllow` shape at this same call site was left completely
unchanged).

### Finding 3 (Minor) — duplicate test name

`backend/tests/test_questions_payload.py` had `test_sets_the_file_level_contract_fields`
defined twice: once for the markdown/Discovery normalizer contract (was line
120) and once for the SDK-path contract (was line 212). Python silently kept
only the second definition when collecting, discarding the first —
confirmed by collecting tests at `c638186` before any other change: **584
tests collected**, one fewer than the 585 physically-defined `def test_...`
functions in the repo at that commit, because of this exact shadowing.

**Fix**: renamed both to say what they actually check —
`test_normalize_sets_the_file_level_contract_fields` (for the
`normalize_questions_payload(_q(...))` case, asserting `parse_ok`/
`raw_markdown`/`name` on the markdown-path contract) and
`test_sdk_sets_the_file_level_contract_fields` (for the
`question_file_from_sdk(SDK_Q, ...)` case, asserting the same three fields on
delegated SDK output). No assertions changed — this was a pure rename, per
the brief's explicit instruction not to weaken or duplicate-fix coverage that
was already correct.

## Verification

Command 1 — targeted:
```
cd backend && .venv/bin/python -m pytest tests/test_questions_payload.py tests/test_proto_builder_questions.py tests/test_agent_tools.py -q
```
Output: `49 passed in 0.46s`

Command 2 — full suite:
```
cd backend && .venv/bin/python -m pytest -q
```
Output: `587 passed, 1 warning in 12.89s` (the 1 warning is the pre-existing,
unrelated `httpx`/`starlette` deprecation warning also present in the Task 4
baseline run — no new warnings).

Command 3 — collected-count proof (guards against Finding 3's exact failure
mode recurring silently):
```
cd backend && .venv/bin/python -m pytest --collect-only -q
```
- At `c638186` (before this fix round, via `git stash`): **584 tests
  collected**.
- After this fix round: **587 tests collected**.
- Delta: **+3**, matching exactly what was added: (a) one new test for
  Finding 1 (`test_an_option_literally_labeled_other_is_not_reclassified`),
  (b) one new test for Finding 2
  (`test_zero_option_question_is_denied_not_raised`), and (c) one test that
  was previously silently discarded by the Finding-3 name collision and is
  now visible again as `test_sdk_sets_the_file_level_contract_fields` (its
  sibling `test_normalize_sets_the_file_level_contract_fields` was the one
  that used to survive the collision, so renaming both recovered exactly one
  previously-hidden test rather than creating a duplicate).

## Files changed in this fix round

- `backend/pathfinder/agent/questions_payload.py` — added `guess_other`
  parameter (Finding 1).
- `backend/pathfinder/proto/builder.py` — wrapped `question_file_from_sdk`
  call in `try/except ValueError` → `PermissionResultDeny` (Finding 2).
- `backend/tests/test_questions_payload.py` — renamed the SDK-path duplicate
  test (Finding 3), added the Other-relabeling regression test (Finding 1).
- `backend/tests/test_proto_builder_questions.py` — added the zero-option
  deny-not-raise test (Finding 2).

## Concerns

- None material. `test_proto_builder_questions.py`'s pre-existing tests
  (including the happy-path `PermissionResultAllow` shape assertions) were
  not touched and still pass unchanged, confirming the new `except ValueError`
  branch is additive and does not alter behavior for well-formed input.
- The `guess_other=False` default choice (opt-out per call, `True` everywhere
  else) was picked over an opt-in flip specifically so that every *existing*
  caller of `normalize_questions_payload` — including any future direct
  caller nobody has written yet — keeps today's heuristic behavior unless it
  explicitly asks not to. Only `question_file_from_sdk` opts out, which is
  exactly the one call site the finding named.
