# Pathfinder Phase 1 — Discovery Engine (Backend Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend "file-as-contract" core that lets a thin web service drive AI-PLC Discovery: parse the methodology's markdown artifacts into structured UI payloads, write user answers back, and relay turns to a Claude Code agent running in a sandbox — with a local fake sandbox so the whole engine is testable without AWS.

**Architecture:** A FastAPI backend owns no methodology logic. It exposes REST + SSE endpoints that (1) parse `*-questions.md`, `aiplc-state.md`, `audit.md`, `discovery-document.md` from a project workspace into JSON, (2) write `[Answer]:` tags back into question files, and (3) forward user turns to a `Sandbox` abstraction. The `Sandbox` interface has two implementations: `LocalSandbox` (runs a workspace on local disk with a scripted/echo agent, for tests and dev) and — in a later plan — `MicroVMSandbox`. This plan delivers everything except the real MicroVM implementation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest, `sse-starlette` for SSE, `httpx` for the harness client. No database in this plan — project state lives in the workspace files and an in-memory registry.

## Global Constraints

- Python version floor: 3.11 (copied from spec tech stack "FastAPI backend"; 3.11 for `tomllib`/typing).
- The backend contains **no methodology logic**: no stage lists, no question wording, no contradiction rules hardcoded. It only parses/serializes files and relays turns. (Spec §"핵심 설계 결정 (A안)" and §2.)
- Question file format is authoritative per `files/aiplc-rules/aws-aiplc-rule-details/common/question-format-guide.md`: `## Question N` or `### Question N` headers, options `A)`–`F)` then a mandatory final `X) Other`, and an `[Answer]:` tag per question. Answers may be a single letter, comma-separated letters (multi-select), or free text after `X`.
- Parser must never hard-fail the workflow: on any unparseable question file, return a `raw_markdown` fallback payload instead of raising. (Spec §2 "파싱 실패 시 원본 마크다운 + 자유 입력 폼으로 폴백".)
- Never read, log, or echo credential-shaped strings. audit rendering must pass through values verbatim EXCEPT redact tokens matching `AKIA`, `sk-`, `bedrock-api-key-`, `goog_`, `AWS_BEARER_TOKEN` prefixes. (Spec §1; core-workflow.md audit rules.)
- All artifact paths are relative to a project workspace root; never accept absolute or `..`-escaping paths from clients.

---

## File Structure

```
backend/
  pathfinder/
    __init__.py
    models.py            # Pydantic payload models (Question, QuestionFile, StageState, AuditEntry, etc.)
    parsers/
      __init__.py
      questions.py       # parse_question_file / serialize_answers
      state.py           # parse_state_file (aiplc-state.md -> stages)
      audit.py           # parse_audit_file (audit.md -> entries, with redaction)
      redaction.py       # redact_credentials(text)
    sandbox/
      __init__.py
      base.py            # Sandbox ABC + dataclasses (AgentEvent, TurnResult)
      local.py           # LocalSandbox: on-disk workspace + scripted agent
    workspace.py         # Workspace: safe path resolution + file read/write over a sandbox
    app.py               # FastAPI app: routes wiring
    routes/
      __init__.py
      projects.py        # POST /projects, GET /projects/{id}
      artifacts.py       # GET /projects/{id}/questions/{name}, GET state, GET audit, GET document
      answers.py         # PUT /projects/{id}/questions/{name}  (write answers)
      turns.py           # POST /projects/{id}/message, GET /projects/{id}/events (SSE)
  tests/
    fixtures/            # copied real question/state/audit files from pilot1
    test_parse_questions.py
    test_serialize_answers.py
    test_parse_state.py
    test_parse_audit.py
    test_redaction.py
    test_local_sandbox.py
    test_workspace.py
    test_routes_artifacts.py
    test_routes_answers.py
    test_routes_turns.py
    test_golden_path_replay.py
  pyproject.toml
```

Rationale: parsers are split one-file-per-artifact-type because they change independently and each has a distinct fixture set. `sandbox/` isolates the one seam where the real MicroVM will later plug in. `workspace.py` centralizes path-safety so no route re-implements it.

---

### Task 1: Project scaffold + first parser model

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/pathfinder/__init__.py`
- Create: `backend/pathfinder/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: Pydantic models used everywhere downstream:
  - `QuestionOption(letter: str, text: str, is_other: bool, recommended: bool)`
  - `Question(number: int, category: str | None, text: str, options: list[QuestionOption], answer: str | None)`
  - `QuestionFile(name: str, preamble: str | None, questions: list[Question], parse_ok: bool, raw_markdown: str | None)`
  - `StageState(name: str, status: Literal["pending","in_progress","completed"], note: str | None)`
  - `ProjectState(project_type: str | None, current_stage: str | None, stages: list[StageState])`
  - `AuditEntry(index: int, timestamp: str, user_input: str, ai_response: str, context: str | None)`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_models.py
from pathfinder.models import Question, QuestionOption, QuestionFile

def test_question_file_roundtrips_multiselect_answer():
    q = Question(
        number=12, category="Success Metrics", text="핵심 KPI는?",
        options=[
            QuestionOption(letter="A", text="시간 절감", is_other=False, recommended=True),
            QuestionOption(letter="X", text="Other", is_other=True, recommended=False),
        ],
        answer="A,B",
    )
    qf = QuestionFile(name="strategy-questions.md", preamble=None,
                      questions=[q], parse_ok=True, raw_markdown=None)
    assert qf.questions[0].answer == "A,B"
    assert qf.questions[0].options[0].recommended is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pathfinder'`

- [ ] **Step 3: Write pyproject and models**

```toml
# backend/pyproject.toml
[project]
name = "pathfinder"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "pydantic>=2.6", "sse-starlette>=2.0", "httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

```python
# backend/pathfinder/__init__.py
```

```python
# backend/pathfinder/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class QuestionOption(BaseModel):
    letter: str
    text: str
    is_other: bool = False
    recommended: bool = False

class Question(BaseModel):
    number: int
    category: str | None = None
    text: str
    options: list[QuestionOption]
    answer: str | None = None

class QuestionFile(BaseModel):
    name: str
    preamble: str | None = None
    questions: list[Question]
    parse_ok: bool
    raw_markdown: str | None = None

class StageState(BaseModel):
    name: str
    status: Literal["pending", "in_progress", "completed"]
    note: str | None = None

class ProjectState(BaseModel):
    project_type: str | None = None
    current_stage: str | None = None
    stages: list[StageState]

class AuditEntry(BaseModel):
    index: int
    timestamp: str
    user_input: str
    ai_response: str
    context: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pip install -e ".[dev]" && python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/pathfinder/__init__.py backend/pathfinder/models.py backend/tests/test_models.py
git commit -m "feat: scaffold backend with core Pydantic models"
```

---

### Task 2: Credential redaction

**Files:**
- Create: `backend/pathfinder/parsers/__init__.py`
- Create: `backend/pathfinder/parsers/redaction.py`
- Test: `backend/tests/test_redaction.py`

**Interfaces:**
- Produces: `redact_credentials(text: str) -> str` — replaces credential-shaped tokens with `[CREDENTIAL REDACTED]`, leaves all other text byte-for-byte intact. Used by the audit parser and anywhere agent output is surfaced.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_redaction.py
from pathfinder.parsers.redaction import redact_credentials

def test_redacts_known_credential_prefixes():
    assert redact_credentials("key AKIAIOSFODNN7EXAMPLE done") == "key [CREDENTIAL REDACTED] done"
    assert redact_credentials("sk-abc123def456ghi789") == "[CREDENTIAL REDACTED]"
    assert redact_credentials("bedrock-api-key-XYZ123456") == "[CREDENTIAL REDACTED]"
    assert redact_credentials("export AWS_BEARER_TOKEN_BEDROCK=zzz999") == "export [CREDENTIAL REDACTED]"

def test_leaves_normal_text_untouched():
    text = "MD가 자연어로 컨셉을 입력하면 30~50개 후보를 받습니다."
    assert redact_credentials(text) == text

def test_does_not_redact_short_or_wordlike_tokens():
    assert redact_credentials("skiing is fun") == "skiing is fun"

def test_does_not_over_redact_hyphenated_words():
    # `sk-`/`AKIA`/etc. must not match the tail of ordinary hyphenated words.
    for phrase in [
        "we recommend a risk-mitigation-plan before launch",
        "the desk-research-summary indicates strong demand",
        "a task-oriented-workflow reduces friction",
        "kiosk-deployment-schedule needs revision",
    ]:
        assert redact_credentials(phrase) == phrase

def test_still_redacts_real_sk_key_at_token_start():
    assert redact_credentials("key sk-proj-abc123def456 here") == "key [CREDENTIAL REDACTED] here"
    assert redact_credentials("sk-abc123def456ghi789") == "[CREDENTIAL REDACTED]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_redaction.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/parsers/__init__.py
```

```python
# backend/pathfinder/parsers/redaction.py
import re

# Token-form patterns carry a left word-boundary lookbehind `(?<![A-Za-z0-9_])`
# so they only match at a token start — without it, `sk-[...]` matches the tail of
# ordinary hyphenated words ("risk-mitigation-plan" -> over-redacted), violating the
# "never over-redact ordinary text" global constraint. The AWS_BEARER_TOKEN pattern
# matches the assignment form (KEY=value); the `=` already forces a boundary.
_PATTERNS = [
    re.compile(r"AWS_BEARER_TOKEN[A-Z_]*=\S+"),
    re.compile(r"(?<![A-Za-z0-9_])AKIA[0-9A-Z]{12,}"),
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9_])bedrock-api-key-[A-Za-z0-9\-]{4,}"),
    re.compile(r"(?<![A-Za-z0-9_])goog_[A-Za-z0-9\-]{4,}"),
]

def redact_credentials(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub("[CREDENTIAL REDACTED]", text)
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_redaction.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/parsers/
git commit -m "feat: credential redaction for audit and agent output"
```

---

### Task 3: Question file parser — happy path

**Files:**
- Create: `backend/pathfinder/parsers/questions.py`
- Create: `backend/tests/fixtures/strategy-questions.md` (copy of pilot1 file)
- Create: `backend/tests/fixtures/discovery-mode-selection-questions.md` (copy of pilot1 file)
- Test: `backend/tests/test_parse_questions.py`

**Interfaces:**
- Consumes: `Question`, `QuestionOption`, `QuestionFile` from Task 1.
- Produces: `parse_question_file(name: str, markdown: str) -> QuestionFile`. On success `parse_ok=True`, `raw_markdown=None`. Handles both flat (`## Question N`) and categorized (`## Category` + `### Question N`) layouts. Captures a leading preamble (text before the first question/category header) into `preamble`. Marks the option containing `← 추천` or `← recommended` as `recommended=True` and strips that marker from `text`. Marks the final `X) Other...` option as `is_other=True`.

- [ ] **Step 1: Copy fixtures**

```bash
cp files/pilot1/aiplc-docs/discovery/product-strategy/strategy-questions.md backend/tests/fixtures/strategy-questions.md
cp files/pilot1/aiplc-docs/discovery-mode-selection-questions.md backend/tests/fixtures/discovery-mode-selection-questions.md
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_parse_questions.py
from pathlib import Path
from pathfinder.parsers.questions import parse_question_file

FIX = Path(__file__).parent / "fixtures"

def _load(name): return parse_question_file(name, (FIX / name).read_text(encoding="utf-8"))

def test_flat_layout_single_question():
    qf = _load("discovery-mode-selection-questions.md")
    assert qf.parse_ok is True
    assert len(qf.questions) == 1
    q = qf.questions[0]
    assert q.number == 1
    assert q.category is None
    assert q.answer == "A"
    assert q.options[-1].is_other is True
    assert q.options[-1].letter == "C"

def test_categorized_layout_and_categories():
    qf = _load("strategy-questions.md")
    assert qf.parse_ok is True
    assert len(qf.questions) == 13
    assert qf.preamble is not None and "가정" in qf.preamble
    q1 = next(q for q in qf.questions if q.number == 1)
    assert q1.category == "Positioning"
    # A) option carries the recommendation marker in the source
    a_opt = next(o for o in q1.options if o.letter == "A")
    assert a_opt.recommended is True
    assert "←" not in a_opt.text

def test_multiselect_and_letter_answers_captured():
    qf = _load("strategy-questions.md")
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[11] == "C"
    assert answers[12] == "A,B"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_parse_questions.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

```python
# backend/pathfinder/parsers/questions.py
from __future__ import annotations
import re
from pathfinder.models import Question, QuestionOption, QuestionFile

_Q_HEADER = re.compile(r"^#{2,3}\s+Question\s+(\d+)\s*$", re.MULTILINE)
_CAT_HEADER = re.compile(r"^##\s+(?!Question\b)(.+?)\s*$", re.MULTILINE)
_OPTION = re.compile(r"^([A-F]|X)\)\s+(.*)$")
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$")
_RECO = re.compile(r"\s*←\s*(추천|recommended).*$", re.IGNORECASE)

def parse_question_file(name: str, markdown: str) -> QuestionFile:
    try:
        return _parse(name, markdown)
    except Exception:
        return QuestionFile(name=name, preamble=None, questions=[],
                            parse_ok=False, raw_markdown=markdown)

def _parse(name: str, markdown: str) -> QuestionFile:
    lines = markdown.splitlines()
    questions: list[Question] = []
    current_category: str | None = None
    preamble_lines: list[str] = []
    seen_first_header = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        qm = _Q_HEADER.match(line)
        cm = _CAT_HEADER.match(line)
        # Category header (## X, but not "## Question")
        if cm and not qm and line.startswith("## "):
            current_category = cm.group(1).strip()
            seen_first_header = True
            i += 1
            continue
        if qm:
            seen_first_header = True
            number = int(qm.group(1))
            i += 1
            text_parts: list[str] = []
            options: list[QuestionOption] = []
            answer: str | None = None
            # consume until next header
            while i < n and not _Q_HEADER.match(lines[i]) and not (
                lines[i].startswith("## ") and not _Q_HEADER.match(lines[i])
            ):
                raw = lines[i].strip()
                om = _OPTION.match(raw)
                am = _ANSWER.match(raw)
                if am:
                    answer = am.group(1).strip() or None
                elif om:
                    letter, otext = om.group(1), om.group(2).strip()
                    recommended = bool(_RECO.search(otext))
                    otext = _RECO.sub("", otext).strip()
                    options.append(QuestionOption(
                        letter=letter, text=otext,
                        is_other=(letter == "X" or otext.lower().startswith("other")),
                        recommended=recommended,
                    ))
                elif raw and not options:
                    text_parts.append(raw)
                i += 1
            questions.append(Question(
                number=number, category=current_category,
                text=" ".join(text_parts).strip(), options=options, answer=answer,
            ))
            continue
        if not seen_first_header and line.strip():
            preamble_lines.append(line.rstrip())
        i += 1

    if not questions:
        raise ValueError("no questions found")
    preamble = "\n".join(preamble_lines).strip() or None
    return QuestionFile(name=name, preamble=preamble, questions=questions,
                        parse_ok=True, raw_markdown=None)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_parse_questions.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/parsers/questions.py backend/tests/fixtures/ backend/tests/test_parse_questions.py
git commit -m "feat: parse flat and categorized question files"
```

---

### Task 4: Question parser fallback

**Files:**
- Modify: `backend/tests/test_parse_questions.py` (add fallback tests)

**Interfaces:**
- Consumes: `parse_question_file` from Task 3 (no signature change; verifies fallback behavior already coded).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_parse_questions.py
def test_unparseable_file_falls_back_to_raw():
    qf = parse_question_file("weird.md", "This has no questions at all.\nJust prose.")
    assert qf.parse_ok is False
    assert qf.questions == []
    assert qf.raw_markdown == "This has no questions at all.\nJust prose."

def test_empty_file_falls_back():
    qf = parse_question_file("empty.md", "")
    assert qf.parse_ok is False
    assert qf.raw_markdown == ""
```

- [ ] **Step 2: Run test to verify result**

Run: `cd backend && python -m pytest tests/test_parse_questions.py -v`
Expected: PASS (5 tests total) — fallback is already implemented in Task 3's `try/except`; this task pins it with tests.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_parse_questions.py
git commit -m "test: pin question parser raw-markdown fallback"
```

---

### Task 5: Answer serialization (write-back)

**Files:**
- Modify: `backend/pathfinder/parsers/questions.py` (add `serialize_answers`)
- Test: `backend/tests/test_serialize_answers.py`

**Interfaces:**
- Consumes: `parse_question_file`.
- Produces: `serialize_answers(markdown: str, answers: dict[int, str]) -> str` — takes the original question-file markdown and a map of `{question_number: answer_string}`, and returns the markdown with each matching `[Answer]:` line rewritten to `[Answer]: <value>`. Preserves every other byte (headers, options, blank lines, and each line's original ending including CRLF). Question numbers absent from `answers` are left unchanged. Raises `KeyError` if `answers` references a question number not present in the file.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_serialize_answers.py
from pathlib import Path
from pathfinder.parsers.questions import serialize_answers, parse_question_file

FIX = Path(__file__).parent / "fixtures"

def test_writes_answers_and_reparses():
    md = (FIX / "discovery-mode-selection-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {1: "B"})
    assert "[Answer]: B" in out
    assert parse_question_file("x.md", out).questions[0].answer == "B"

def test_multiselect_value_written():
    md = (FIX / "strategy-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {12: "A,C"})
    reparsed = {q.number: q.answer for q in parse_question_file("x.md", out).questions}
    assert reparsed[12] == "A,C"
    # untouched question retains original answer
    assert reparsed[1] == "A"

def test_unknown_question_number_raises():
    md = (FIX / "discovery-mode-selection-questions.md").read_text(encoding="utf-8")
    try:
        serialize_answers(md, {99: "A"})
        assert False, "expected KeyError"
    except KeyError:
        pass

def test_preserves_exact_bytes_of_untargeted_lines():
    # Rewriting Q1 to a NEW value must change only Q1's [Answer] line, byte-for-byte
    # everywhere else. (Whole-file == won't hold: the fixture's line is `[Answer]:A`
    # with no space, and serialize normalizes to `[Answer]: A`.)
    md = (FIX / "strategy-questions.md").read_text(encoding="utf-8")
    out = serialize_answers(md, {1: "B"})
    orig_lines, new_lines = md.splitlines(keepends=True), out.splitlines(keepends=True)
    assert len(orig_lines) == len(new_lines)
    diffs = [i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b]
    assert len(diffs) == 1  # exactly one line changed — Q1's answer line

def test_preserves_crlf_line_endings():
    md = "## Question 1\nPick one\nA) x\nX) Other\n[Answer]: A\n".replace("\n", "\r\n")
    out = serialize_answers(md, {1: "B"})
    assert "[Answer]: B\r\n" in out
    assert "[Answer]: B\n" not in out.replace("\r\n", "")  # no bare-LF degradation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_serialize_answers.py -v`
Expected: FAIL with `ImportError: cannot import name 'serialize_answers'`

- [ ] **Step 3: Write the implementation**

```python
# append to backend/pathfinder/parsers/questions.py
# (`import re` is already at the top of the module)

def serialize_answers(markdown: str, answers: dict[int, str]) -> str:
    present = {q.number for q in _parse("_", markdown).questions}
    missing = set(answers) - present
    if missing:
        raise KeyError(f"question numbers not in file: {sorted(missing)}")

    lines = markdown.splitlines(keepends=True)
    current_q: int | None = None
    out: list[str] = []
    for line in lines:
        # Match headers on the same basis _parse uses (raw line, minus the line
        # ending) so the KeyError precondition and the rewrite loop cannot diverge
        # on indented headers.
        qm = _Q_HEADER.match(line.rstrip("\r\n"))
        if qm:
            current_q = int(qm.group(1))
            out.append(line)
            continue
        if current_q in answers and line.strip().startswith("[Answer]:"):
            # Preserve the ORIGINAL line ending exactly (\r\n / \r / \n / none at
            # EOF). Assuming "\n" silently drops \r on CRLF files, corrupting the
            # file the agent reads back — violates the preserve-every-byte rule.
            m_end = re.search(r"(\r\n|\r|\n)$", line)
            ending = m_end.group(1) if m_end else ""
            out.append(f"[Answer]: {answers[current_q]}{ending}")
            continue
        out.append(line)
    return "".join(out)
```

Note: only the *other* bytes are preserved — the rewritten answer line is
normalized to `[Answer]: <value>` (a single space after the colon), which may
differ from an original `[Answer]:A`. Tests must assert byte-equality on every
line *except* the rewritten one, not whole-file equality.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_serialize_answers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/parsers/questions.py backend/tests/test_serialize_answers.py
git commit -m "feat: write-back answers into question files"
```

---

### Task 6: State file parser

**Files:**
- Create: `backend/pathfinder/parsers/state.py`
- Create: `backend/tests/fixtures/aiplc-state.md` (copy of pilot1 file)
- Test: `backend/tests/test_parse_state.py`

**Interfaces:**
- Consumes: `ProjectState`, `StageState` from Task 1.
- Produces: `parse_state_file(markdown: str) -> ProjectState`. Reads `**Project Type**:` and `**Current Stage**:` fields. Parses each `- [x]`/`- [ ]` checklist line under the stage section into a `StageState`: `- [x]` → `completed`, `- [ ]` → `pending`. Marks **at most one** pending stage `in_progress`: a stage whose name exactly equals `Current Stage`, else the longest pending stage name that substring-matches `Current Stage` (in either direction). Completed stages are never marked `in_progress`. Stage `name` is the text up to the first `—`/`-` delimiter; the remainder becomes `note`.

- [ ] **Step 1: Copy fixture**

```bash
cp files/pilot1/aiplc-docs/aiplc-state.md backend/tests/fixtures/aiplc-state.md
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_parse_state.py
from pathlib import Path
from pathfinder.parsers.state import parse_state_file

FIX = Path(__file__).parent / "fixtures"

def test_parses_pilot1_state():
    st = parse_state_file((FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    assert st.project_type == "Greenfield"
    names = [s.name for s in st.stages]
    assert "Workspace Detection" in names
    assert all(s.status == "completed" for s in st.stages)  # pilot1 finished all stages

def test_pending_and_note_split():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Envision\n"
        "## Stage Progress\n"
        "- [x] Workspace Detection — Completed 2026-07-04\n"
        "- [ ] Envision\n"
    )
    st = parse_state_file(md)
    ws = next(s for s in st.stages if s.name == "Workspace Detection")
    assert ws.status == "completed"
    assert ws.note == "Completed 2026-07-04"
    env = next(s for s in st.stages if s.name == "Envision")
    assert env.status == "in_progress"  # matches Current Stage, not yet completed

def test_in_progress_single_on_substring_collision():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Discovery Mode Selection\n"
        "## Stage Progress\n"
        "- [ ] Discovery Mode Selection\n"
        "- [ ] Discovery Mode Selection Extended Review\n"
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Discovery Mode Selection"]  # exactly one — the exact match

def test_in_progress_longest_match_when_no_exact():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Discovery Mode Selection Extended Review Phase\n"
        "## Stage Progress\n"
        "- [ ] Discovery\n"
        "- [ ] Discovery Mode Selection Extended Review\n"
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Discovery Mode Selection Extended Review"]  # longest/most-specific, only one
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_parse_state.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

```python
# backend/pathfinder/parsers/state.py
from __future__ import annotations
import re
from pathfinder.models import ProjectState, StageState

_PROJECT_TYPE = re.compile(r"\*\*Project Type\*\*:\s*(.+)")
_CURRENT_STAGE = re.compile(r"\*\*Current Stage\*\*:\s*(.+)")
_CHECK = re.compile(r"^- \[([ xX])\]\s*(.+)$")
_SPLIT = re.compile(r"\s+[—-]\s+")

def parse_state_file(markdown: str) -> ProjectState:
    project_type = None
    current_stage = None
    stages: list[StageState] = []
    for line in markdown.splitlines():
        line = line.rstrip()
        if project_type is None and (m := _PROJECT_TYPE.search(line)):
            project_type = m.group(1).strip()
            continue
        if current_stage is None and (m := _CURRENT_STAGE.search(line)):
            current_stage = m.group(1).strip()
            continue
        if (m := _CHECK.match(line.strip())):
            checked = m.group(1).lower() == "x"
            body = m.group(2).strip()
            parts = _SPLIT.split(body, maxsplit=1)
            name = parts[0].strip()
            note = parts[1].strip() if len(parts) > 1 else None
            status = "completed" if checked else "pending"
            stages.append(StageState(name=name, status=status, note=note))

    # Resolve at most ONE in_progress stage. Exact name match wins; otherwise
    # the longest substring-matching pending name (most specific). A plain
    # `name in current_stage` test marks EVERY overlapping pending stage active
    # (AI-PLC stage names overlap heavily: "Discovery", "Discovery Mode
    # Selection", "Discovery Document"), so the dashboard would show several
    # stages "in progress" at once. Completed stages are never selected.
    if current_stage:
        pending = [s for s in stages if s.status == "pending"]
        exact = [s for s in pending if s.name == current_stage]
        if exact:
            exact[0].status = "in_progress"
        else:
            partial = [
                s for s in pending
                if s.name in current_stage or current_stage in s.name
            ]
            if partial:
                best = max(partial, key=lambda s: len(s.name))
                best.status = "in_progress"

    return ProjectState(project_type=project_type, current_stage=current_stage, stages=stages)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_parse_state.py -v`
Expected: PASS (4 tests — 2 base + 2 in_progress collision/longest-match regressions)

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/parsers/state.py backend/tests/fixtures/aiplc-state.md backend/tests/test_parse_state.py
git commit -m "feat: parse aiplc-state.md into stage timeline"
```

---

### Task 7: Audit file parser (with redaction)

**Files:**
- Create: `backend/pathfinder/parsers/audit.py`
- Create: `backend/tests/fixtures/audit.md` (copy of pilot1 file)
- Test: `backend/tests/test_parse_audit.py`

**Interfaces:**
- Consumes: `AuditEntry` from Task 1, `redact_credentials` from Task 2.
- Produces: `parse_audit_file(markdown: str) -> list[AuditEntry]`. Splits on `## Entry N:` headers. Within each entry block, extracts `**Timestamp**:`, `**User Input**:`, `**AI Response**:`, `**Context**:` field values **marker-to-next-marker** (NOT end-of-line — some pilot logs squash an entire entry onto one physical line using literal `\n` text, so an end-of-line regex would bleed one field's value into the next). Strips surrounding quotes. Runs `redact_credentials` over user_input, ai_response, AND context (context redaction is defense-in-depth beyond the strict requirement). Preserves order.

- [ ] **Step 1: Copy fixture**

```bash
cp files/pilot1/aiplc-docs/audit.md backend/tests/fixtures/audit.md
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_parse_audit.py
from pathlib import Path
from pathfinder.parsers.audit import parse_audit_file

FIX = Path(__file__).parent / "fixtures"

def test_parses_pilot1_audit_entries():
    entries = parse_audit_file((FIX / "audit.md").read_text(encoding="utf-8"))
    assert entries[0].index == 1
    assert entries[0].user_input == "ai-plc를 시작하고 싶어"
    assert entries[0].context == "Session start"
    # entries are in order and cover the full pilot run
    assert [e.index for e in entries] == list(range(1, len(entries) + 1))

def test_redacts_credentials_in_entries():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: my key is AKIAIOSFODNN7EXAMPLE ok\n"
        "**AI Response**: noted\n"
        "**Context**: test\n"
    )
    e = parse_audit_file(md)[0]
    assert "AKIA" not in e.user_input
    assert "[CREDENTIAL REDACTED]" in e.user_input

def test_redacts_credentials_in_ai_response():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: hello\n"
        "**AI Response**: token is AKIAIOSFODNN7EXAMPLE right\n"
        "**Context**: test\n"
    )
    e = parse_audit_file(md)[0]
    assert "AKIA" not in e.ai_response
    assert "[CREDENTIAL REDACTED]" in e.ai_response

def test_redacts_credentials_in_context():
    md = (
        "## Entry 1: Test\n"
        "**Timestamp**: 2026-07-04T00:00:00Z\n"
        "**User Input**: hi\n"
        "**AI Response**: ok\n"
        "**Context**: leaked sk-abc123def456ghi789 here\n"
    )
    e = parse_audit_file(md)[0]
    assert "[CREDENTIAL REDACTED]" in e.context

def test_squashed_single_line_entry_splits_fields():
    # A whole entry on one physical line (literal \n as text) must still split
    # at markers — ai_response must NOT absorb the Context value.
    md = (
        "## Entry 1: Squashed\n"
        '**Timestamp**: 2026-07-04T00:00:00Z **User Input**: "big blob with \\n escapes and more" '
        "**AI Response**: the real answer **Context**: Some Context Label\n"
    )
    e = parse_audit_file(md)[0]
    assert e.ai_response == "the real answer"
    assert e.context == "Some Context Label"
    assert "big blob" in e.user_input

def test_no_unredacted_credentials_anywhere_in_real_fixture():
    entries = parse_audit_file((FIX / "audit.md").read_text(encoding="utf-8"))
    for e in entries:
        for field in (e.user_input, e.ai_response, e.context or ""):
            for marker in ("AKIA", "sk-", "bedrock-api-key-", "goog_"):
                assert marker not in field, f"unredacted {marker} in entry {e.index}"
        # AWS_BEARER_TOKEN appears as a bare env-var NAME in prose (Entry 21) with
        # no =value, which is correctly left alone — check only the secret-bearing
        # assignment form.
        import re as _re
        for field in (e.user_input, e.ai_response, e.context or ""):
            assert not _re.search(r"AWS_BEARER_TOKEN[A-Z_]*=\S", field)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_parse_audit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

```python
# backend/pathfinder/parsers/audit.py
from __future__ import annotations
import re
from pathfinder.models import AuditEntry
from pathfinder.parsers.redaction import redact_credentials

_ENTRY = re.compile(r"^##\s+Entry\s+(\d+):", re.MULTILINE)

# Matches any of the four field markers, in whatever order they appear in the
# block. Some pilot logs squash an entire entry onto one physical line (using
# literal "\n" text rather than real newlines), so field values must be
# extracted marker-to-next-marker rather than end-of-line.
_MARKER = re.compile(r"\*\*(Timestamp|User Input|AI Response|Context)\*\*:\s*")

_KEY_MAP = {
    "Timestamp": "timestamp",
    "User Input": "user_input",
    "AI Response": "ai_response",
    "Context": "context",
}

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s

def parse_audit_file(markdown: str) -> list[AuditEntry]:
    matches = list(_ENTRY.finditer(markdown))
    entries: list[AuditEntry] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        block = markdown[start:end]

        marker_matches = list(_MARKER.finditer(block))
        fields: dict[str, str] = {}
        for i, mm in enumerate(marker_matches):
            key = _KEY_MAP[mm.group(1)]
            value_start = mm.end()
            value_end = marker_matches[i + 1].start() if i + 1 < len(marker_matches) else len(block)
            value = _strip_quotes(block[value_start:value_end])
            # First occurrence of a marker wins, matching prior behavior.
            fields.setdefault(key, value)

        entries.append(AuditEntry(
            index=int(m.group(1)),
            timestamp=fields.get("timestamp", ""),
            user_input=redact_credentials(fields.get("user_input", "")),
            ai_response=redact_credentials(fields.get("ai_response", "")),
            context=redact_credentials(fields.get("context", "")) or None,
        ))
    return entries
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_parse_audit.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/pathfinder/parsers/audit.py backend/tests/fixtures/audit.md backend/tests/test_parse_audit.py
git commit -m "feat: parse audit.md with credential redaction"
```

---

### Task 8: Sandbox interface + agent event types

**Files:**
- Create: `backend/pathfinder/sandbox/__init__.py`
- Create: `backend/pathfinder/sandbox/base.py`
- Test: `backend/tests/test_sandbox_base.py`

**Interfaces:**
- Produces:
  - `AgentEvent(kind: Literal["message","file_changed","status","done","error"], text: str | None, path: str | None)` — a Pydantic model streamed over SSE.
  - `TurnResult(events: list[AgentEvent])` — result of a completed turn.
  - `Sandbox` ABC with async methods:
    - `async start() -> None`
    - `async read_file(rel_path: str) -> str`
    - `async write_file(rel_path: str, content: str) -> None`
    - `async list_files(glob: str) -> list[str]`
    - `async send_message(text: str) -> AsyncIterator[AgentEvent]` — yields events as the agent processes the turn, ending with a `done` (or `error`) event.
    - `async stop() -> None`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sandbox_base.py
import inspect
from pathfinder.sandbox.base import Sandbox, AgentEvent, TurnResult

def test_agent_event_shape():
    e = AgentEvent(kind="message", text="hi", path=None)
    assert e.kind == "message"

def test_sandbox_is_abstract():
    assert inspect.isabstract(Sandbox)
    for m in ("start", "read_file", "write_file", "list_files", "send_message", "stop"):
        assert hasattr(Sandbox, m)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_sandbox_base.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/sandbox/__init__.py
```

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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_sandbox_base.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/
git commit -m "feat: Sandbox interface and agent event types"
```

---

### Task 9: LocalSandbox with a scripted agent

**Files:**
- Create: `backend/pathfinder/sandbox/local.py`
- Test: `backend/tests/test_local_sandbox.py`

**Interfaces:**
- Consumes: `Sandbox`, `AgentEvent` from Task 8.
- Produces: `LocalSandbox(root: Path, script: AgentScript | None = None)` implementing `Sandbox` over a real on-disk temp directory. Files are read/written under `root` with path-safety (no `..`, no absolute). `send_message` delegates to an `AgentScript`: `AgentScript` is a callable `(text: str, sandbox: LocalSandbox) -> list[AgentEvent]` that a test supplies to simulate the agent (e.g. "on this message, write this file, emit these events"). Default script echoes the message back as a single `message` event followed by `done`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_local_sandbox.py
import pytest
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

async def _collect(aiter):
    return [e async for e in aiter]

async def test_read_write_roundtrip(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/audit.md", "hello")
    assert await sb.read_file("aiplc-docs/audit.md") == "hello"

async def test_path_escape_rejected(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("../evil.md", "x")

async def test_default_script_echoes(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    events = await _collect(sb.send_message("승인"))
    assert events[0].kind == "message" and "승인" in events[0].text
    assert events[-1].kind == "done"

async def test_custom_script_can_write_files_and_emit(tmp_path: Path):
    def script(text, sb):
        return [AgentEvent(kind="file_changed", path="aiplc-docs/x.md"),
                AgentEvent(kind="done")]
    sb = LocalSandbox(root=tmp_path, script=script)
    await sb.start()
    events = await _collect(sb.send_message("go"))
    assert events[0].kind == "file_changed"
    assert events[0].path == "aiplc-docs/x.md"

async def test_list_files_glob(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/a-questions.md", "x")
    await sb.write_file("aiplc-docs/b-questions.md", "y")
    found = sorted(await sb.list_files("aiplc-docs/*-questions.md"))
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]

async def test_list_files_rejects_traversal_glob(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.list_files("../*")

async def test_read_file_rejects_traversal(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.read_file("../secret.md")

async def test_rejects_absolute_path(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    with pytest.raises(ValueError):
        await sb.write_file("/etc/evil.md", "x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_local_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/sandbox/local.py
from __future__ import annotations
from pathlib import Path
from typing import AsyncIterator, Callable
from pathfinder.sandbox.base import Sandbox, AgentEvent

AgentScript = Callable[[str, "LocalSandbox"], list[AgentEvent]]

def _default_script(text: str, sb: "LocalSandbox") -> list[AgentEvent]:
    return [AgentEvent(kind="message", text=f"echo: {text}"), AgentEvent(kind="done")]

class LocalSandbox(Sandbox):
    def __init__(self, root: Path, script: AgentScript | None = None):
        self.root = Path(root)
        self._script = script or _default_script

    def _resolve(self, rel_path: str) -> Path:
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"unsafe path: {rel_path}")
        return self.root / rel_path

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    async def read_file(self, rel_path: str) -> str:
        return self._resolve(rel_path).read_text(encoding="utf-8")

    async def write_file(self, rel_path: str, content: str) -> None:
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def list_files(self, glob: str) -> list[str]:
        # Same traversal guard as _resolve: a client glob like "../*" would
        # otherwise enumerate files OUTSIDE root (filename disclosure). `*` and
        # other wildcards are literal, non-`..` path segments, so legitimate
        # patterns like "aiplc-docs/*-questions.md" still pass.
        if glob.startswith("/") or ".." in Path(glob).parts:
            raise ValueError(f"unsafe glob: {glob}")
        return [str(p.relative_to(self.root)) for p in self.root.glob(glob) if p.is_file()]

    # Deliberate: this is an async-generator function (uses `yield`), even though
    # the Sandbox ABC declares send_message as a plain method returning
    # AsyncIterator. Calling an async-gen function returns an AsyncIterator
    # synchronously (no await needed), so `async for event in
    # sandbox.send_message(text)` works. Do not "fix" this to a plain async def.
    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for event in self._script(text, self):
            yield event

    async def stop(self) -> None:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_local_sandbox.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/sandbox/local.py backend/tests/test_local_sandbox.py
git commit -m "feat: LocalSandbox with scriptable agent for tests"
```

---

### Task 10: Workspace + project registry

**Files:**
- Create: `backend/pathfinder/workspace.py`
- Test: `backend/tests/test_workspace.py`

**Interfaces:**
- Consumes: `Sandbox` (Task 8), all parsers (Tasks 3, 5, 6, 7).
- Produces:
  - `Workspace(sandbox: Sandbox)` with async methods that combine sandbox IO + parsing:
    - `async get_questions(name: str) -> QuestionFile`
    - `async put_answers(name: str, answers: dict[int, str]) -> QuestionFile` (reads file, serializes, writes back, returns reparsed)
    - `async get_state() -> ProjectState`
    - `async get_audit() -> list[AuditEntry]`
    - `async get_document() -> str` (raw markdown of `aiplc-docs/discovery/discovery-document.md`; empty string if absent)
    - `async list_question_files() -> list[str]`
  - `ProjectRegistry` — in-memory `dict[str, Workspace]` with `create(project_id, sandbox) -> Workspace` and `get(project_id) -> Workspace` (raises `KeyError` if absent).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_workspace.py
from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.workspace import Workspace, ProjectRegistry

FIX = Path(__file__).parent / "fixtures"

async def _seeded(tmp_path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/strategy-questions.md",
                        (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    await sb.write_file("aiplc-docs/aiplc-state.md",
                        (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    return Workspace(sb)

async def test_get_questions_and_put_answers(tmp_path):
    ws = await _seeded(tmp_path)
    qf = await ws.get_questions("aiplc-docs/strategy-questions.md")
    assert len(qf.questions) == 13
    updated = await ws.put_answers("aiplc-docs/strategy-questions.md", {1: "B"})
    assert next(q for q in updated.questions if q.number == 1).answer == "B"

async def test_get_state(tmp_path):
    ws = await _seeded(tmp_path)
    st = await ws.get_state()
    assert st.project_type == "Greenfield"

async def test_missing_document_returns_empty(tmp_path):
    ws = await _seeded(tmp_path)
    assert await ws.get_document() == ""

async def test_registry_create_and_get(tmp_path):
    reg = ProjectRegistry()
    sb = LocalSandbox(root=tmp_path); await sb.start()
    ws = reg.create("p1", sb)
    assert reg.get("p1") is ws

async def test_list_question_files_finds_top_level_and_nested(tmp_path):
    # list_question_files must find question files BOTH directly under aiplc-docs/
    # (pilot1: discovery-mode-selection-questions.md) and nested several levels
    # (pilot1: discovery/product-strategy/strategy-questions.md), and exclude
    # non-question files. Locks in the `**` glob behavior verified on 3.11.
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await sb.write_file("aiplc-docs/discovery-mode-selection-questions.md", "x")
    await sb.write_file("aiplc-docs/discovery/product-strategy/strategy-questions.md", "y")
    await sb.write_file("aiplc-docs/audit.md", "z")  # must NOT be listed
    ws = Workspace(sb)
    found = sorted(await ws.list_question_files())
    assert found == [
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/workspace.py
from __future__ import annotations
from pathfinder.sandbox.base import Sandbox
from pathfinder.models import QuestionFile, ProjectState, AuditEntry
from pathfinder.parsers.questions import parse_question_file, serialize_answers
from pathfinder.parsers.state import parse_state_file
from pathfinder.parsers.audit import parse_audit_file

_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"
_STATE_PATH = "aiplc-docs/aiplc-state.md"
_AUDIT_PATH = "aiplc-docs/audit.md"

class Workspace:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    async def get_questions(self, name: str) -> QuestionFile:
        md = await self.sandbox.read_file(name)
        return parse_question_file(name.split("/")[-1], md)

    async def put_answers(self, name: str, answers: dict[int, str]) -> QuestionFile:
        md = await self.sandbox.read_file(name)
        new_md = serialize_answers(md, answers)
        await self.sandbox.write_file(name, new_md)
        return parse_question_file(name.split("/")[-1], new_md)

    async def get_state(self) -> ProjectState:
        try:
            md = await self.sandbox.read_file(_STATE_PATH)
        except FileNotFoundError:
            return ProjectState(stages=[])
        return parse_state_file(md)

    async def get_audit(self) -> list[AuditEntry]:
        try:
            md = await self.sandbox.read_file(_AUDIT_PATH)
        except FileNotFoundError:
            return []
        return parse_audit_file(md)

    async def get_document(self) -> str:
        try:
            return await self.sandbox.read_file(_DOC_PATH)
        except FileNotFoundError:
            return ""

    async def list_question_files(self) -> list[str]:
        return await self.sandbox.list_files("aiplc-docs/**/*-questions.md")

class ProjectRegistry:
    def __init__(self):
        self._projects: dict[str, Workspace] = {}

    def create(self, project_id: str, sandbox: Sandbox) -> Workspace:
        ws = Workspace(sandbox)
        self._projects[project_id] = ws
        return ws

    def get(self, project_id: str) -> Workspace:
        return self._projects[project_id]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_workspace.py -v`
Expected: PASS (5 tests)

Note (verified on Python 3.11.14): `Path.glob("aiplc-docs/**/*-questions.md")` matches BOTH files directly under `aiplc-docs/` AND nested files — CPython's `**` means "zero or more directories". So `list_question_files` works for the real pilot1 layout (top-level `discovery-mode-selection-questions.md` and nested `discovery/product-strategy/strategy-questions.md`) with no `rglob` workaround. `test_list_question_files_finds_top_level_and_nested` locks this in; if a future Python changes `**` semantics, that test catches it.

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/workspace.py backend/tests/test_workspace.py
git commit -m "feat: Workspace facade and in-memory project registry"
```

---

### Task 11: FastAPI app + artifact read routes

**Files:**
- Create: `backend/pathfinder/app.py`
- Create: `backend/pathfinder/routes/__init__.py`
- Create: `backend/pathfinder/routes/deps.py` (shared `get_workspace(pid)` helper — used by artifacts.py here and by answers.py/turns.py in Tasks 12–13, so the "get workspace or 404" logic lives in one place instead of being duplicated three times)
- Create: `backend/pathfinder/routes/projects.py`
- Create: `backend/pathfinder/routes/artifacts.py`
- Create: `backend/tests/conftest.py` (autouse fixture that ensures a usable event loop before each test; pytest-asyncio auto mode leaves the loop unset after async tests, which breaks the sync seed helpers' `asyncio.run`/loop access on some orderings)
- Test: `backend/tests/test_routes_artifacts.py`

**Interfaces:**
- Consumes: `ProjectRegistry`, `Workspace`, `LocalSandbox`.
- Produces: FastAPI app with a module-level `registry = ProjectRegistry()` and a `make_sandbox` hook (defaults to a `LocalSandbox` in a temp dir; overridden in later MicroVM plan). A shared `routes/deps.py::get_workspace(pid) -> Workspace` raises `HTTPException(404)` on unknown project; all read/write/turn routes use it. Routes:
  - `POST /projects` body `{project_id: str}` → creates workspace, returns `{project_id}`. 409 if exists.
  - `GET /projects/{pid}/state` → `ProjectState` JSON. 404 if project unknown.
  - `GET /projects/{pid}/audit` → `list[AuditEntry]`.
  - `GET /projects/{pid}/document` → `{markdown: str}`.
  - `GET /projects/{pid}/questions/{name}` → `QuestionFile` JSON.
- Test uses FastAPI `TestClient` and seeds files by grabbing the workspace's sandbox directly from `registry`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_routes_artifacts.py
from pathlib import Path
from fastapi.testclient import TestClient
from pathfinder.app import app, registry

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _create_and_seed(pid):
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)
    import asyncio
    async def seed():
        await ws.sandbox.write_file("aiplc-docs/aiplc-state.md",
            (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
        await ws.sandbox.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    # asyncio.run — NOT get_event_loop().run_until_complete (deprecated on 3.11
    # and conflicts with pytest-asyncio's managed loop; see conftest.py note).
    asyncio.run(seed())

def test_create_project_conflict():
    client.post("/projects", json={"project_id": "dup"})
    r = client.post("/projects", json={"project_id": "dup"})
    assert r.status_code == 409

def test_get_state_route():
    _create_and_seed("proj-state")
    r = client.get("/projects/proj-state/state")
    assert r.status_code == 200
    assert r.json()["project_type"] == "Greenfield"

def test_get_questions_route():
    _create_and_seed("proj-q")
    r = client.get("/projects/proj-q/questions/aiplc-docs/strategy-questions.md")
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 13

def test_unknown_project_404():
    assert client.get("/projects/nope/state").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_artifacts.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/routes/__init__.py
```

```python
# backend/pathfinder/app.py
from __future__ import annotations
import tempfile
from pathlib import Path
from fastapi import FastAPI
from pathfinder.workspace import ProjectRegistry
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import Sandbox

registry = ProjectRegistry()

async def make_sandbox(project_id: str) -> Sandbox:
    root = Path(tempfile.mkdtemp(prefix=f"pf-{project_id}-"))
    sb = LocalSandbox(root=root)
    await sb.start()
    return sb

app = FastAPI(title="Pathfinder")

from pathfinder.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router)
app.include_router(artifacts.router)
```

```python
# backend/pathfinder/routes/projects.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module

router = APIRouter()

class CreateProject(BaseModel):
    project_id: str

@router.post("/projects")
async def create_project(body: CreateProject):
    try:
        app_module.registry.get(body.project_id)
        raise HTTPException(status_code=409, detail="project exists")
    except KeyError:
        pass
    sandbox = await app_module.make_sandbox(body.project_id)
    app_module.registry.create(body.project_id, sandbox)
    return {"project_id": body.project_id}
```

```python
# backend/pathfinder/routes/deps.py
# Shared "get workspace or 404" helper — used by artifacts.py, answers.py,
# and turns.py so the logic isn't duplicated three times.
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.workspace import Workspace

def get_workspace(pid: str) -> Workspace:
    try:
        return app_module.registry.get(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown project")
```

```python
# backend/pathfinder/routes/artifacts.py
from fastapi import APIRouter, HTTPException
from pathfinder.routes.deps import get_workspace

router = APIRouter()

@router.get("/projects/{pid}/state")
async def get_state(pid: str):
    return await get_workspace(pid).get_state()

@router.get("/projects/{pid}/audit")
async def get_audit(pid: str):
    return await get_workspace(pid).get_audit()

@router.get("/projects/{pid}/document")
async def get_document(pid: str):
    return {"markdown": await get_workspace(pid).get_document()}

@router.get("/projects/{pid}/questions/{name:path}")
async def get_questions(pid: str, name: str):
    try:
        return await get_workspace(pid).get_questions(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
```

```python
# backend/tests/conftest.py
import asyncio
import pytest

@pytest.fixture(autouse=True)
def _ensure_event_loop():
    # pytest-asyncio auto mode calls set_event_loop(None) during async-test
    # teardown, leaving the thread with no loop; a later sync seed helper's
    # asyncio.run / loop access then raises RuntimeError depending on test
    # order. Ensure a usable loop before every test so standalone and
    # full-suite runs behave identically.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_artifacts.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/app.py backend/pathfinder/routes/
git commit -m "feat: FastAPI app with project creation and artifact read routes"
```

---

### Task 12: Answer write route

**Files:**
- Create: `backend/pathfinder/routes/answers.py`
- Modify: `backend/pathfinder/app.py` (register router)
- Test: `backend/tests/test_routes_answers.py`

**Interfaces:**
- Consumes: `Workspace.put_answers`.
- Produces: `PUT /projects/{pid}/questions/{name:path}` body `{answers: dict[str,str]}` (JSON keys are strings; converted to int) → returns reparsed `QuestionFile`. 400 on unknown question number (`KeyError` from `serialize_answers`). 404 if file/project absent.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_routes_answers.py
from pathlib import Path
import asyncio
from fastapi.testclient import TestClient
from pathfinder.app import app, registry

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _seed(pid):
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    # Use asyncio.run (not get_event_loop().run_until_complete) — the latter is
    # deprecated on 3.11 and conflicts with pytest-asyncio's managed loop.
    asyncio.run(
        ws.sandbox.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8")))

def test_put_answers_updates_file():
    _seed("ans1")
    r = client.put("/projects/ans1/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"1": "B", "12": "A,C"}})
    assert r.status_code == 200
    by_num = {q["number"]: q["answer"] for q in r.json()["questions"]}
    assert by_num[1] == "B"
    assert by_num[12] == "A,C"

def test_put_unknown_question_400():
    _seed("ans2")
    r = client.put("/projects/ans2/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"99": "A"}})
    assert r.status_code == 400

def test_put_non_numeric_key_400():
    # A non-numeric answer key must yield a clean 400, not an unhandled 500.
    _seed("ans3")
    r = client.put("/projects/ans3/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"abc": "A"}})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_answers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/routes/answers.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder.models import QuestionFile
from pathfinder.routes.deps import get_workspace

router = APIRouter()

class AnswersBody(BaseModel):
    answers: dict[str, str]

@router.put("/projects/{pid}/questions/{name:path}", response_model=QuestionFile)
async def put_answers(pid: str, name: str, body: AnswersBody):
    # Guard int() BEFORE touching the workspace so a non-numeric key yields a
    # clean 400, not an unhandled 500 on this public write endpoint.
    try:
        answers = {int(k): v for k, v in body.answers.items()}
    except ValueError:
        raise HTTPException(status_code=400, detail="question numbers must be integers")
    try:
        return await get_workspace(pid).put_answers(name, answers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

```python
# add to backend/pathfinder/app.py, after existing includes
from pathfinder.routes import answers  # noqa: E402
app.include_router(answers.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_answers.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/routes/answers.py backend/pathfinder/app.py backend/tests/test_routes_answers.py
git commit -m "feat: answer write-back route"
```

---

### Task 13: Turn relay + SSE event stream

**Files:**
- Create: `backend/pathfinder/routes/turns.py`
- Modify: `backend/pathfinder/app.py` (register router)
- Test: `backend/tests/test_routes_turns.py`

**Interfaces:**
- Consumes: `Workspace.sandbox.send_message` yielding `AgentEvent`.
- Produces:
  - `POST /projects/{pid}/message` body `{text: str}` → drives `sandbox.send_message(text)` to completion, returns `TurnResult` (`{events: [...]}`) as JSON. This is the simple synchronous path used by clients that don't need streaming.
  - `GET /projects/{pid}/events?text=...` → SSE stream (via `sse_starlette.EventSourceResponse`) yielding one SSE `data:` frame per `AgentEvent` (JSON-encoded), used by the canvas/live UI.
- To make the turn observable in tests without AWS, `make_sandbox` in tests is monkeypatched to install a scripted `LocalSandbox`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_routes_turns.py
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

client = TestClient(app_module.app)

def _install_scripted(monkeypatch, pid, script):
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)
        await sb.start()
        return sb
    # monkeypatch auto-restores make_sandbox at teardown (no leak into other tests).
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def test_message_returns_events(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text=f"got {text}"), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn1", script)
    r = client.post("/projects/turn1/message", json={"text": "승인"})
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["message", "done"]
    assert "승인" in r.json()["events"][0]["text"]

def test_sse_stream_emits_frames(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn2", script)
    with client.stream("GET", "/projects/turn2/events", params={"text": "go"}) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "working" in body          # first (status) frame
    assert "ok" in body               # middle (message) frame
    assert '"kind":"done"' in body.replace(" ", "")  # final frame

def test_message_redacts_credentials_in_event_text(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred1", script)
    r = client.post("/projects/turnred1/message", json={"text": "go"})
    assert r.status_code == 200
    joined = " ".join(e.get("text") or "" for e in r.json()["events"])
    assert "AKIA" not in joined
    assert "[CREDENTIAL REDACTED]" in joined

def test_sse_redacts_credentials_in_event_text(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred2", script)
    with client.stream("GET", "/projects/turnred2/events", params={"text": "go"}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_turns.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/pathfinder/routes/turns.py
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.routes.deps import get_workspace
from pathfinder.sandbox.base import AgentEvent, TurnResult

router = APIRouter()

class MessageBody(BaseModel):
    text: str

def _redacted(event: AgentEvent) -> AgentEvent:
    # Redact credential-bearing agent output at the surface seam. Only `text`
    # is agent-authored; `path`/`kind` are structural. A real MicroVM agent
    # can echo secrets over these routes, so redaction must live HERE, not
    # only in the audit parser.
    if event.text is None:
        return event
    return event.model_copy(update={"text": redact_credentials(event.text)})

@router.post("/projects/{pid}/message")
async def post_message(pid: str, body: MessageBody):
    ws = get_workspace(pid)
    events = [_redacted(e) async for e in ws.sandbox.send_message(body.text)]
    return TurnResult(events=events)

@router.get("/projects/{pid}/events")
async def stream_events(pid: str, text: str):
    ws = get_workspace(pid)
    async def gen():
        async for event in ws.sandbox.send_message(text):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())
```

```python
# add to backend/pathfinder/app.py, after existing includes
from pathfinder.routes import turns  # noqa: E402
app.include_router(turns.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_turns.py -v`
Expected: PASS (4 tests — message, SSE, + credential redaction at both routes)

- [ ] **Step 5: Commit**

```bash
git add backend/pathfinder/routes/turns.py backend/pathfinder/app.py backend/tests/test_routes_turns.py
git commit -m "feat: turn relay and SSE event stream"
```

---

### Task 14: Golden-path replay test

**Files:**
- Create: `backend/tests/test_golden_path_replay.py`

**Interfaces:**
- Consumes: everything. This is the spec's §7 "골든 패스 리플레이" regression test in miniature: it drives a scripted `LocalSandbox` whose agent script transitions `aiplc-state.md` exactly as pilot1 did, and asserts the state timeline the API surfaces matches pilot1's completed stages.

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_golden_path_replay.py
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent
from pathfinder.parsers.state import parse_state_file

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app_module.app)

# The pilot1 stage sequence, in completion order (from aiplc-state.md).
STAGES = [
    "Workspace Detection", "Discovery Mode Selection", "Envision",
    "Solution Analysis", "Prototype & Validation", "Product Strategy",
    "Go-to-Market", "Discovery Document",
]

def _state_md(completed_count):
    lines = ["# AI-PLC State Tracking",
             "- **Project Type**: Greenfield",
             f"- **Current Stage**: {STAGES[min(completed_count, len(STAGES)-1)]}",
             "## Stage Progress"]
    for i, name in enumerate(STAGES):
        mark = "x" if i < completed_count else " "
        lines.append(f"- [{mark}] {name}")
    return "\n".join(lines) + "\n"

def test_replay_advances_state_like_pilot1(monkeypatch):
    # Agent script: each user message advances the workspace by one completed stage.
    counter = {"n": 1}
    def script(text, sb):
        counter["n"] += 1
        # write synchronously via the sandbox's resolve (LocalSandbox is on disk)
        p = sb._resolve("aiplc-docs/aiplc-state.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_state_md(counter["n"]), encoding="utf-8")
        return [AgentEvent(kind="file_changed", path="aiplc-docs/aiplc-state.md"),
                AgentEvent(kind="done")]

    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)
        await sb.start()
        sb._resolve("aiplc-docs").mkdir(parents=True, exist_ok=True)
        sb._resolve("aiplc-docs/aiplc-state.md").write_text(_state_md(1), encoding="utf-8")
        return sb
    # monkeypatch auto-restores make_sandbox at teardown, so this fake factory
    # doesn't leak into other tests (this file collects alphabetically first).
    monkeypatch.setattr(app_module, "make_sandbox", make)

    client.post("/projects", json={"project_id": "replay"})
    # advance through all remaining stages
    for _ in range(len(STAGES) - 1):
        assert client.post("/projects/replay/message", json={"text": "승인"}).status_code == 200

    state = client.get("/projects/replay/state").json()
    names = [s["name"] for s in state["stages"]]
    assert names == STAGES
    assert all(s["status"] == "completed" for s in state["stages"])

def test_stages_match_real_pilot1_fixture():
    # Guard against STAGES drifting from the real pilot1 artifact — the §7
    # reproducibility guarantee is meant to track the actual fixture, not a copy.
    md = (FIX / "aiplc-state.md").read_text(encoding="utf-8")
    real_names = [s.name for s in parse_state_file(md).stages]
    assert real_names == STAGES
```

- [ ] **Step 2: Run the test**

Run: `cd backend && python -m pytest tests/test_golden_path_replay.py -v`
Expected: PASS (2 tests — the replay, plus the STAGES-matches-real-fixture guard)

- [ ] **Step 3: Run the full suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all tests from Tasks 1–14 — 52 total)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_golden_path_replay.py
git commit -m "test: golden-path state-advance replay over the API"
```

---

## Self-Review

**Spec coverage (Phase 1 backend scope):**
- §1 thin backend, IAM auth, Sonnet 5 pin → backend has no methodology logic (verified by design); IAM/model live in the MicroVM plan, not this backend plan. Covered/deferred correctly.
- §2 file-as-contract table → parsers for questions (T3–5), state (T6), audit (T7), document (T10/T11); routes render them (T11–13). All five artifact rows covered.
- §2 parse-fail fallback → T4.
- §2 harness endpoints (`/message`, `/events`, file read/write, `/preview`) → `/message` + `/events` (T13), file read/write via Workspace/routes (T11–12); `/preview` is a Phase 2 (prototype) concern, deferred.
- §2 post-submit contradiction check → clarification files are just another `*-questions.md`, parsed by T3 (a clarification file matches the same format). No extra code needed; noted here.
- §4 S3 sync + session-continuity restore → deferred to MicroVM plan (needs the real sandbox); LocalSandbox has no persistence layer by design.
- §6 governance/redaction → T2 + T7.
- §7 golden-path replay → T14; parser unit tests use pilot1 fixtures (T3, T6, T7).
- Global constraint "no absolute/`..` paths" → enforced in `LocalSandbox._resolve` (T9), tested.

**Deferred to follow-on plans (out of this plan's scope, by the phase split):**
- MicroVMSandbox implementation, S3 persistence, session-continuity restore, `ANTHROPIC_MODEL`/IAM wiring (MicroVM plan).
- Frontend screens 01–04, SSE consumption in the canvas, iframe preview (frontend plan).
- Prototype build/iterate/publish, buildah/ECR/traefik (Phase 3 plan).

**Placeholder scan:** No TBD/TODO; every code step shows full code; no "similar to Task N" references.

**Type consistency:** `parse_question_file(name, markdown)`, `serialize_answers(markdown, answers)`, `Workspace.put_answers(name, answers)`, `AgentEvent(kind,text,path)`, `TurnResult(events)`, `ProjectRegistry.create/get` — names and signatures are used identically across Tasks 1–14. `make_sandbox(project_id)` and module-level `registry` are referenced consistently in routes and tests.

One known-fragile spot flagged inline: `LocalSandbox.list_files` `**` handling (Task 10 note) — the fix (`rglob`) is spelled out if the glob test fails.
