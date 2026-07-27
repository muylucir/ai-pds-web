# Discovery 드라이버 Claude Agent SDK 이관 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discovery 워크플로우 드라이버를 Strands Agents SDK에서 Claude Agent SDK로 이관해, AI-PLC 룰이 전제한 실행 환경(CLAUDE.md + CWD 상대경로 + 내장 도구)을 그대로 재현한다.

**Architecture:** `StrandsDriver`와 **같은 3-메서드 계약**(`run` / `run_answers` / `pending`)을 갖는 `ClaudeDriver`를 새로 만들고 `PATHFINDER_DISCOVERY_DRIVER` env로 전환한다. `proto/builder.py`가 같은 문제를 Claude Agent SDK로 이미 풀어 프로덕션에서 돌고 있으므로 참조 구현으로 재사용한다. `runner.py`와 라우트는 계약이 동일하므로 손대지 않는다.

**Tech Stack:** Python 3.11, FastAPI, `claude-agent-sdk==0.2.126`(이미 백엔드 의존성), boto3(S3), pytest/pytest-asyncio, Next.js 15 + Vitest(프론트 라벨).

**설계 문서:** `docs/superpowers/specs/2026-07-27-discovery-driver-claude-agent-sdk-design.md`

## Global Constraints

- Python `requires-python = ">=3.11"`. 테스트는 `cd backend && .venv/bin/python -m pytest -q` (bare `python3`은 3.9라 pydantic이 깨진다).
- **이벤트 계약 불변:** `AgentEvent`는 `kind: Literal["message","questions","stage","document","file_changed","status","done","error"]`, `text: str|None`, `path: str|None`, `payload: str|None` — 프론트 SSE 계약이므로 필드/리터럴 변경 금지.
- **드라이버 계약 불변:** `run(text: str, session: dict) -> AsyncIterator[AgentEvent]`, `run_answers(interrupt_id: str, answers: dict[str,str], session: dict) -> AsyncIterator[AgentEvent]`, `pending(session: dict) -> str | None`. `runner.py:129,167,183`이 이것만 호출한다.
- **에러 문자열 그대로 유지**(테스트가 부분 매칭): `"turn already in progress"`, `"no pending questions"`, `"agent turn failed"`.
- **API 표면 불변** — 라우트 경로·요청/응답 형태·SSE 프레임 변경 없음.
- **S3 레이아웃:** 프로젝트 산출물 `projects/{pid}/...`, pending 질문 `projects/{pid}/pending/questions.json`(신규). Strands 세션 `sessions/...`는 폴백 기간 유지.
- `.gitignore`의 `docs/`·`infra/`·`**/tests/` 규칙 때문에 그 경로의 **신규 파일은 `git add -f`가 필요**하다.
- 커밋 메시지 말미: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## File Structure

```
backend/pathfinder/agent/
  driver.py             # StrandsDriver — 손대지 않음 (env 폴백용)
  claude_driver.py      # ClaudeDriver ★신규 — Task 4~6
  workspace_rules.py    # 룰 배치 ★신규 — Task 1
  pending_store.py      # pending 질문 S3 영속 ★신규 — Task 2
  questions_payload.py  # 기존 — Task 3에서 SDK 입력 변환 추가
  tools.py              # 6개 → 2개 (Task 5)
backend/tests/
  driver_contract.py    # 두 드라이버 공용 계약 테스트 ★신규 — Task 3
discovery-config/
  CLAUDE.md             # Pathfinder 통합 규약 ★신규 — Task 5
frontend/components/canvas/
  AiMessage.tsx         # ACTIVITY_LABELS 확장 (Task 7)
frontend/app/projects/[projectId]/workspace/
  page.tsx:100          # 첨부 안내에서 file_read 언급 제거 (Task 7)
backend/pathfinder/app.py
  driver_factory        # env 토글 (Task 8)
infra/lib/user-data.ts  # env 주입 (Task 8)
```

**책임 분리:** `workspace_rules.py`와 `pending_store.py`는 드라이버 없이 테스트 가능한 순수 로직이다. `claude_driver.py`를 `driver.py`에 합치지 않는 이유는 두 드라이버가 공존해야 하고 `driver.py`가 이미 240행이기 때문이다.

**Task 순서 원칙:** 순수 로직을 먼저 세우고(1–2), 계약을 **동작하는 기존 코드로** 확정하고(3), 그 위에 새 드라이버를 만들고(4–6), 프론트와 배선을 마지막에 한다(7–8).

---

### Task 1: 워크스페이스 룰 배치

**Files:**
- Create: `backend/pathfinder/agent/workspace_rules.py`
- Test: `backend/tests/test_workspace_rules.py`

**Interfaces:**
- Consumes: 없음 (순수 파일 조작)
- Produces: `place_rules(workspace: str, rules_dir: str) -> None` — Task 4가 턴 시작 시 호출한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_workspace_rules.py`:

```python
# 상류(aws-samples/sample-ai-plc) 레이아웃을 워크스페이스에 재현한다:
#   core-workflow.md → CLAUDE.md,  aws-aiplc-rule-details/ → 그 이름 그대로.
# core-workflow.md:18이 `Rule details location: ./aws-aiplc-rule-details/`로
# CWD 상대경로를 전제하므로, 룰이 워크스페이스에 있어야 에이전트가 그 경로를
# 그대로 읽는다.
from pathlib import Path

import pytest

from pathfinder.agent.workspace_rules import place_rules


def _rules(tmp_path: Path) -> Path:
    """리포의 rule/aiplc-rules 레이아웃을 흉내낸 픽스처."""
    rules = tmp_path / "rules"
    (rules / "aws-aiplc-rules").mkdir(parents=True)
    (rules / "aws-aiplc-rules" / "core-workflow.md").write_text(
        "# DISCOVERY PHASE WORKFLOW", encoding="utf-8")
    details = rules / "aws-aiplc-rule-details" / "common"
    details.mkdir(parents=True)
    (details / "process-overview.md").write_text("OVERVIEW", encoding="utf-8")
    return rules


def test_copies_core_workflow_as_claude_md(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "# DISCOVERY PHASE WORKFLOW"


def test_copies_rule_details_under_the_name_the_rules_expect(tmp_path):
    # 이름이 바뀌면 `./aws-aiplc-rule-details/common/...` 읽기가 전부 깨진다.
    ws = tmp_path / "ws"
    ws.mkdir()
    place_rules(str(ws), str(_rules(tmp_path)))
    assert (ws / "aws-aiplc-rule-details" / "common" / "process-overview.md") \
        .read_text(encoding="utf-8") == "OVERVIEW"


def test_is_idempotent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules))
    place_rules(str(ws), str(rules))
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "# DISCOVERY PHASE WORKFLOW"


def test_skips_a_file_already_present_with_the_same_size(tmp_path):
    # 매 턴 수십 개 파일을 다시 쓰지 않는다. 룰은 읽기 전용이므로 크기가 같으면
    # 같은 파일로 본다. mtime을 뒤로 밀어 두고 그대로인지 확인한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules))
    target = ws / "CLAUDE.md"
    import os
    os.utime(target, (1, 1))
    place_rules(str(ws), str(rules))
    assert target.stat().st_mtime == 1


def test_overwrites_a_file_whose_size_differs(tmp_path):
    # 상류 룰이 갱신되면 워크스페이스에도 반영돼야 한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    rules = _rules(tmp_path)
    place_rules(str(ws), str(rules))
    (ws / "CLAUDE.md").write_text("STALE", encoding="utf-8")
    place_rules(str(ws), str(rules))
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "# DISCOVERY PHASE WORKFLOW"


def test_raises_when_core_workflow_is_missing(tmp_path):
    # 룰 없이 조용히 진행하면 에이전트가 워크플로우를 모르는 채로 돈다 —
    # 그건 빈 대화로 나타나서 원인 추적이 어렵다. 즉시 실패한다.
    ws = tmp_path / "ws"
    ws.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        place_rules(str(ws), str(empty))


def test_works_against_the_real_repo_rules():
    # 픽스처가 잘못된 레이아웃을 굳혀 실제 배치가 깨지는 것을 막는 통합 핀
    # (test_agent_tools.py의 test_file_read_reaches_real_rules_layout과 같은 이유).
    import tempfile
    repo_rules = Path(__file__).resolve().parents[2] / "rule" / "aiplc-rules"
    if not (repo_rules / "aws-aiplc-rules" / "core-workflow.md").is_file():
        pytest.skip("repo rules not present")
    with tempfile.TemporaryDirectory() as ws:
        place_rules(ws, str(repo_rules))
        assert (Path(ws) / "CLAUDE.md").is_file()
        assert (Path(ws) / "aws-aiplc-rule-details" / "common").is_dir()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.agent.workspace_rules'`

- [ ] **Step 3: 구현한다**

`backend/pathfinder/agent/workspace_rules.py`:

```python
# backend/pathfinder/agent/workspace_rules.py — 상류 AI-PLC 레이아웃을
# 워크스페이스에 재현한다.
#
# 상류(aws-samples/sample-ai-plc)의 Claude Code 셋업은 core-workflow.md를
# 프로젝트 루트의 CLAUDE.md로 복사하고 상세 룰을 aws-aiplc-rule-details/에 둔다.
# core-workflow.md:18이 `Rule details location: ./aws-aiplc-rule-details/`로
# CWD 상대경로를 전제하므로 룰은 CLAUDE_CONFIG_DIR이 아니라 워크스페이스에 있어야
# 한다 — 그래야 에이전트가 그 경로를 그대로 읽는다. 이 배치가 있기 때문에
# Strands 시절 file_read의 `aiplc-rules/` 프리픽스 특수 처리가 필요 없어진다.
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_log = logging.getLogger("pathfinder.agent")

_CORE_WORKFLOW = "aws-aiplc-rules/core-workflow.md"
_DETAILS_DIR = "aws-aiplc-rule-details"


def _copy_if_changed(src: Path, dst: Path) -> None:
    """크기가 같으면 건너뛴다. 룰은 읽기 전용이므로 크기 비교로 충분하고,
    매 턴 수십 개 파일을 다시 쓰지 않게 한다."""
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def place_rules(workspace: str, rules_dir: str) -> None:
    """`core-workflow.md` → `<workspace>/CLAUDE.md`,
    `aws-aiplc-rule-details/` → `<workspace>/aws-aiplc-rule-details/`.

    멱등이며 매 턴 호출해도 싸다. 룰이 없으면 FileNotFoundError — 조용히
    진행하면 에이전트가 워크플로우를 모르는 채로 돌고, 그건 빈 대화로 나타나서
    원인 추적이 어렵다.
    """
    root = Path(rules_dir)
    core = root / _CORE_WORKFLOW
    if not core.is_file():
        raise FileNotFoundError(f"AI-PLC core workflow not found: {core}")

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    _copy_if_changed(core, ws / "CLAUDE.md")

    details = root / _DETAILS_DIR
    if not details.is_dir():
        # core만으로도 워크플로우는 시작된다(상세 룰은 온디맨드) — 경고만.
        _log.warning("AI-PLC rule details missing: %s", details)
        return
    for src in details.rglob("*"):
        if src.is_file():
            _copy_if_changed(src, ws / _DETAILS_DIR / src.relative_to(details))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_workspace_rules.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋한다**

```bash
git add -f backend/pathfinder/agent/workspace_rules.py backend/tests/test_workspace_rules.py
git commit -m "$(cat <<'EOF'
feat(agent): 워크스페이스에 상류 AI-PLC 레이아웃을 배치한다

core-workflow.md → CLAUDE.md, aws-aiplc-rule-details/ → 동명 디렉터리.
core-workflow.md:18이 `./aws-aiplc-rule-details/`로 CWD 상대경로를
전제하므로 룰은 CLAUDE_CONFIG_DIR이 아니라 워크스페이스에 있어야 한다.

크기가 같으면 건너뛴다 — 룰은 읽기 전용이고 매 턴 호출되므로.
룰이 없으면 FileNotFoundError: 조용히 진행하면 에이전트가 워크플로우를
모르는 채로 돌고 그건 빈 대화로 나타나 원인 추적이 어렵다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: pending 질문 S3 영속

**Files:**
- Create: `backend/pathfinder/agent/pending_store.py`
- Test: `backend/tests/test_pending_store.py`

**Interfaces:**
- Consumes: `S3StoreLike`(`pathfinder/s3store.py`) — `get(key) -> str`, `put(key, content) -> None`, `list(prefix) -> list[str]`, `delete_prefix(prefix) -> int`. `FileNotFoundError`가 키 없음이다.
- Produces:
  - `PENDING_KEY = "pending/questions.json"`
  - `async save_pending(s3, *, interrupt_id: str, questions: dict, sdk_questions: list[dict], session_id: str) -> None`
  - `async load_pending(s3) -> dict | None` — `{"interrupt_id","questions","sdk_questions","session_id"}` 또는 None
  - `async clear_pending(s3) -> None`
  - Task 4·6이 사용한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_pending_store.py`:

```python
# Strands는 세션에 pending interrupt를 함께 영속하지만 Claude Agent SDK의
# session store는 트랜스크립트 미러여서 pending 질문이 인메모리 Future다.
# GET /pending(새로고침 후 질문 폼 복원)이 그 기능을 쓰므로 별도로 영속한다.
#
# sdk_questions(SDK 원형)를 함께 저장하는 이유: 답변을 SDK 라벨로 되번역할 때
# 필요하고(builder._answer_to_sdk), 재시작 후에는 인메모리 사본이 없다.
import pytest

from pathfinder.agent.pending_store import (
    PENDING_KEY, clear_pending, load_pending, save_pending,
)
from tests.fakes.in_memory_s3 import FakeS3Store

QUESTIONS = {"name": "envision", "preamble": None, "parse_ok": True,
             "raw_markdown": None,
             "questions": [{"number": 1, "category": None, "text": "누구?",
                            "answer": None, "multi_select": False,
                            "options": [{"letter": "A", "text": "PM",
                                         "is_other": False,
                                         "recommended": False}]}]}
SDK_QUESTIONS = [{"question": "누구?", "header": "Audience",
                  "multiSelect": False,
                  "options": [{"label": "PM", "description": "제품 관리자"}]}]


@pytest.mark.asyncio
async def test_round_trips_every_field():
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    got = await load_pending(s3)
    assert got == {"interrupt_id": "i-1", "questions": QUESTIONS,
                   "sdk_questions": SDK_QUESTIONS, "session_id": "s-1"}


@pytest.mark.asyncio
async def test_load_returns_none_when_nothing_is_pending():
    assert await load_pending(FakeS3Store()) is None


@pytest.mark.asyncio
async def test_clear_removes_it():
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    await clear_pending(s3)
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_clear_is_idempotent():
    # 답변 제출과 인터럽트가 겹쳐 두 번 호출될 수 있다 — 두 번째가 터지면
    # 턴이 죽는다.
    await clear_pending(FakeS3Store())


@pytest.mark.asyncio
async def test_save_replaces_an_earlier_pending():
    # 한 프로젝트에 pending 질문은 하나뿐이다(고정 키). 이전 것이 남으면
    # 새로고침 시 답변 불가한 옛 폼이 뜬다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="old", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    await save_pending(s3, interrupt_id="new", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    got = await load_pending(s3)
    assert got is not None and got["interrupt_id"] == "new"


@pytest.mark.asyncio
async def test_load_degrades_to_none_on_corrupt_json():
    # 손상된 payload로 500을 내지 않는다 — pending은 복원 편의이고, 없으면
    # 사용자가 턴을 다시 시작할 수 있다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = "{not json"
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_load_degrades_to_none_when_a_required_field_is_missing():
    # 계약이 드리프트한 payload를 반쯤 복원하면 답변 제출이 조용히 실패한다.
    s3 = FakeS3Store()
    s3.blobs[PENDING_KEY] = '{"interrupt_id": "i-1"}'
    assert await load_pending(s3) is None


@pytest.mark.asyncio
async def test_hangul_survives_the_round_trip():
    # ensure_ascii=False로 저장해야 화면에 \\uXXXX가 뜨지 않는다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1", questions=QUESTIONS,
                       sdk_questions=SDK_QUESTIONS, session_id="s-1")
    assert "누구?" in s3.blobs[PENDING_KEY]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pending_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.agent.pending_store'`

- [ ] **Step 3: 구현한다**

`backend/pathfinder/agent/pending_store.py`:

```python
# backend/pathfinder/agent/pending_store.py — pending 질문의 S3 영속.
#
# 왜 필요한가: Strands는 세션에 pending interrupt를 함께 영속했지만(그래서
# agent._interrupt_state를 읽으면 됐다), Claude Agent SDK의 session store는
# 트랜스크립트 미러여서 pending 질문은 인메모리 Future다. GET /pending —
# 새로고침 후 질문 폼 복원 — 이 그 기능을 쓰므로 별도로 저장한다.
#
# 한 프로젝트에 pending 질문은 하나뿐이므로 키가 고정이다(프로젝트 프리픽스는
# S3Store가 붙인다).
from __future__ import annotations

import json
import logging

from pathfinder.s3store import S3StoreLike

_log = logging.getLogger("pathfinder.agent")

PENDING_KEY = "pending/questions.json"

_REQUIRED = ("interrupt_id", "questions", "sdk_questions", "session_id")


async def save_pending(s3: S3StoreLike, *, interrupt_id: str, questions: dict,
                       sdk_questions: list[dict], session_id: str) -> None:
    """sdk_questions(SDK 원형)를 함께 저장한다 — 답변을 SDK 라벨로 되번역할 때
    필요하고, 재시작 후에는 인메모리 사본이 없다."""
    await s3.put(PENDING_KEY, json.dumps({
        "interrupt_id": interrupt_id,
        "questions": questions,
        "sdk_questions": sdk_questions,
        "session_id": session_id,
    }, ensure_ascii=False))


async def load_pending(s3: S3StoreLike) -> dict | None:
    """없거나 손상됐으면 None. 500을 내지 않는다 — pending은 복원 편의이고,
    없으면 사용자가 턴을 다시 시작할 수 있다. 반쯤 복원하는 것이 더 나쁘다."""
    try:
        raw = await s3.get(PENDING_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("pending payload is not valid JSON — ignoring")
        return None
    if not isinstance(data, dict) or any(k not in data for k in _REQUIRED):
        _log.warning("pending payload missing required fields — ignoring")
        return None
    return data


async def clear_pending(s3: S3StoreLike) -> None:
    """멱등 — 답변 제출과 인터럽트가 겹쳐 두 번 호출될 수 있다."""
    await s3.delete_prefix(PENDING_KEY)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pending_store.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋한다**

```bash
git add -f backend/pathfinder/agent/pending_store.py backend/tests/test_pending_store.py
git commit -m "$(cat <<'EOF'
feat(agent): pending 질문을 S3에 영속한다

Strands는 세션에 pending interrupt를 함께 저장했지만 Claude Agent SDK의
session store는 트랜스크립트 미러여서 pending 질문이 인메모리 Future다.
GET /pending(새로고침 후 질문 폼 복원)이 그 기능을 쓰므로 별도로 저장한다.

sdk_questions(SDK 원형)를 함께 저장한다 — 답변을 SDK 라벨로 되번역할 때
필요하고 재시작 후에는 인메모리 사본이 없다.

손상/누락 payload는 None으로 강등한다. pending은 복원 편의이고 없으면
사용자가 턴을 다시 시작할 수 있다 — 반쯤 복원하는 것이 더 나쁘다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 드라이버 계약 테스트 (기존 StrandsDriver로 확정)

동작하는 기존 코드로 계약을 못박는다. Task 4~6에서 이 테스트가 그대로 `ClaudeDriver`의 스펙이 된다.

**Files:**
- Create: `backend/tests/driver_contract.py`
- Create: `backend/tests/test_strands_driver_contract.py`
- Test: 위 두 파일

**Interfaces:**
- Consumes: Task 1·2 없음 (독립)
- Produces: `async def assert_driver_contract(make_driver)` — `make_driver(scripted) -> (driver, session)`를 받아 계약 전체를 검증한다. Task 6이 `ClaudeDriver`로 같은 함수를 호출한다.

- [ ] **Step 1: 계약 테스트 모듈을 쓴다**

`backend/tests/driver_contract.py`:

```python
# 두 드라이버(StrandsDriver / ClaudeDriver)가 공유하는 계약.
#
# runner.py는 세 메서드만 쓴다(runner.py:129,167,183). 그 계약을 여기 한 곳에
# 두고 양쪽에 걸면 "기능 동등"을 기계적으로 증명할 수 있다 — 삭제된
# sandbox_contract.py가 같은 패턴이었다.
#
# make_driver(scripted) 규약: scripted는 드라이버가 흉내낼 턴 대본이고,
# (driver, session) 튜플을 돌려준다. 대본의 형태는 SDK마다 다르므로 각
# 어댑터 테스트가 번역한다 — 이 모듈은 AgentEvent 출력만 본다.
from __future__ import annotations

from pathfinder.models import AgentEvent


async def _collect(agen) -> list[AgentEvent]:
    return [ev async for ev in agen]


async def assert_driver_contract(make_driver) -> None:
    """계약 전체. 실패 시 어느 항목인지 메시지로 드러난다."""
    await _assert_text_turn(make_driver)
    await _assert_tool_status(make_driver)
    await _assert_questions_carry_an_interrupt_id(make_driver)
    await _assert_failure_is_sanitized(make_driver)
    await _assert_pending_is_none_when_nothing_pends(make_driver)


async def _assert_text_turn(make_driver) -> None:
    # 가장 기본: 모델 텍스트가 kind=message로, 턴 끝이 kind=done으로 온다.
    driver, session = make_driver({"text": ["안녕하세요"]})
    events = await _collect(driver.run("hi", session))
    kinds = [e.kind for e in events]
    assert "message" in kinds, f"텍스트가 message로 오지 않았다: {kinds}"
    assert kinds[-1] == "done", f"턴이 done으로 끝나지 않았다: {kinds}"
    assert any(e.text == "안녕하세요" for e in events if e.kind == "message")


async def _assert_tool_status(make_driver) -> None:
    # 도구 실행은 kind=status로 오고, 같은 도구가 연속되면 한 번만 온다
    # (SDK가 델타마다 프레임을 내므로 중복 제거가 계약이다).
    driver, session = make_driver({"tools": ["Read", "Read", "Write"]})
    events = await _collect(driver.run("hi", session))
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["Read", "Write"], f"status 중복 제거 실패: {statuses}"


async def _assert_questions_carry_an_interrupt_id(make_driver) -> None:
    # runner.py가 payload에서 interrupt_id를 뽑아 send_answers에 넘긴다
    # (_interrupt_id_from). 없으면 답변 제출 경로가 죽는다.
    import json
    driver, session = make_driver({"questions": True})
    events = await _collect(driver.run("hi", session))
    q = [e for e in events if e.kind == "questions"]
    assert q, "questions 이벤트가 없다"
    payload = json.loads(q[0].payload or "{}")
    assert payload.get("interrupt_id"), f"interrupt_id 없음: {payload}"
    assert payload.get("questions"), f"questions 본문 없음: {payload}"


async def _assert_failure_is_sanitized(make_driver) -> None:
    # SDK 예외가 그대로 새면 스택트레이스가 사용자 화면에 간다. 정해진 문자열로
    # 강등한다(테스트가 부분 매칭하는 계약 문자열).
    driver, session = make_driver({"raise": True})
    events = await _collect(driver.run("hi", session))
    errors = [e for e in events if e.kind == "error"]
    assert errors, f"실패가 error로 오지 않았다: {[e.kind for e in events]}"
    assert errors[0].text == "agent turn failed"


async def _assert_pending_is_none_when_nothing_pends(make_driver) -> None:
    driver, session = make_driver({"text": ["ok"]})
    assert await driver.pending(session) is None
```

- [ ] **Step 2: StrandsDriver 어댑터 테스트를 쓴다**

`backend/tests/test_strands_driver_contract.py`:

```python
# 계약을 동작하는 기존 드라이버로 확정한다. 여기가 통과해야 driver_contract.py가
# 신뢰할 수 있는 스펙이 되고, ClaudeDriver가 같은 함수를 통과하면 동등하다.
import pytest

from pathfinder.agent.driver import StrandsDriver
from tests.driver_contract import assert_driver_contract


class _FakeStrandsAgent:
    """strands Agent.stream_async를 흉내낸다 — 실제 SDK 이벤트 dict 형태
    ({"data":...} / {"current_tool_use":...} / {"result":...})."""

    def __init__(self, scripted: dict, emit):
        self._scripted = scripted
        self._emit = emit
        self._interrupt_state = None

    async def stream_async(self, prompt):
        if self._scripted.get("raise"):
            raise RuntimeError("boom")
        for text in self._scripted.get("text", []):
            yield {"data": text}
        for name in self._scripted.get("tools", []):
            yield {"current_tool_use": {"name": name}}
        if self._scripted.get("questions"):
            # ask_questions 도구가 큐에 넣는 경로를 흉내낸다.
            import json
            from pathfinder.models import AgentEvent
            self._emit(AgentEvent(kind="questions", payload=json.dumps(
                {"interrupt_id": "i-strands",
                 "questions": {"name": "q", "questions": []}},
                ensure_ascii=False)))
        yield {"result": _FakeResult()}


class _FakeResult:
    stop_reason = "end_turn"
    interrupts: list = []


def _make_strands_driver(scripted: dict):
    def factory(session, emit):
        return _FakeStrandsAgent(scripted, emit)
    driver = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules",
                          agent_factory=factory)
    return driver, {"session_id": "s-1"}


@pytest.mark.asyncio
async def test_strands_driver_satisfies_the_contract():
    await assert_driver_contract(_make_strands_driver)
```

- [ ] **Step 3: 실행해 계약이 기존 드라이버에서 성립하는지 본다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_strands_driver_contract.py -q`
Expected: PASS. **실패하면 계약 정의가 틀린 것이다** — `driver_contract.py`를 실제 `StrandsDriver` 동작에 맞춰 고친다(구현을 고치지 않는다). 특히 `_assert_tool_status`의 도구 이름은 Strands에서 `file_read` 같은 값이 오므로, 대본이 그대로 반영되는지만 본다.

- [ ] **Step 4: 커밋한다**

```bash
git add -f backend/tests/driver_contract.py backend/tests/test_strands_driver_contract.py
git commit -m "$(cat <<'EOF'
test(agent): 드라이버 계약을 기존 StrandsDriver로 확정한다

runner.py는 run/run_answers/pending 세 메서드만 쓴다(:129,167,183).
그 계약을 driver_contract.py 한 곳에 두고 양쪽 드라이버에 걸면 기능
동등을 기계적으로 증명할 수 있다 — 삭제된 sandbox_contract.py와 같은 패턴.

동작하는 기존 코드로 먼저 확정하는 이유: 새 코드로 계약을 정의하면 그
계약이 맞는지 알 수 없다. 여기가 통과해야 ClaudeDriver의 스펙이 된다.

계약 5항목 — 텍스트 턴이 message+done으로, 도구가 status로(연속 중복
제거), questions가 interrupt_id를 싣고, SDK 예외가 "agent turn failed"로
강등되고, pending 없으면 None.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: SDK 질문 입력 → QuestionFile 변환 통합

`questions_payload.py`(Discovery)와 `builder._to_question_file()`(프로토타입)이 같은 일을 한다. 하나로 합쳐 `is_other` 중복 교정이 양쪽에 적용되게 한다.

**Files:**
- Modify: `backend/pathfinder/agent/questions_payload.py`
- Modify: `backend/pathfinder/proto/builder.py:37-52` (`_to_question_file` 제거, 새 함수 호출)
- Test: `backend/tests/test_questions_payload.py`(추가), `backend/tests/test_proto_builder_questions.py`(회귀)

**Interfaces:**
- Consumes: 기존 `normalize_questions_payload(payload) -> dict`
- Produces: `question_file_from_sdk(sdk_questions: list[dict], *, name: str) -> dict` — Task 6의 `_on_can_use_tool`과 `builder.py`가 함께 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_questions_payload.py` 끝에 추가:

```python
# ---- SDK AskUserQuestion input → QuestionFile ----
# builder._to_question_file과 Discovery의 정규화가 같은 일을 하던 것을 합친다.
# 합치면 is_other 중복 교정(2026-07-26 버그)이 프로토타입 빌더에도 적용된다.
from pathfinder.agent.questions_payload import question_file_from_sdk

SDK_Q = [{"question": "다음 단계는?", "header": "Next",
          "multiSelect": False,
          "options": [{"label": "진행", "description": "다음 스테이지로"},
                      {"label": "종료", "description": "핸드오프"}]}]


def test_maps_sdk_options_to_letters_in_order():
    f = question_file_from_sdk(SDK_Q, name="next-step")
    opts = f["questions"][0]["options"]
    assert [o["letter"] for o in opts] == ["A", "B"]
    # letter 인덱스가 SDK 옵션 순서와 1:1이어야 답변 되번역이 맞는다.
    assert opts[0]["text"].startswith("진행")


def test_joins_label_and_description():
    f = question_file_from_sdk(SDK_Q, name="n")
    assert f["questions"][0]["options"][0]["text"] == "진행 — 다음 스테이지로"


def test_drops_the_dash_when_description_is_empty():
    f = question_file_from_sdk([{"question": "q", "options": [{"label": "진행"}]}],
                               name="n")
    assert f["questions"][0]["options"][0]["text"] == "진행"


def test_carries_header_as_category_and_multiselect():
    f = question_file_from_sdk(
        [{"question": "q", "header": "Audience", "multiSelect": True,
          "options": [{"label": "A"}, {"label": "B"}]}], name="n")
    q = f["questions"][0]
    assert q["category"] == "Audience"
    assert q["multi_select"] is True


def test_sets_the_file_level_contract_fields():
    f = question_file_from_sdk(SDK_Q, name="next-step")
    assert f["name"] == "next-step"
    assert f["parse_ok"] is True
    assert f["raw_markdown"] is None


def test_result_passes_the_normalizer_unchanged():
    # 두 경로가 한 계약으로 수렴하는지 — SDK 입력을 변환한 결과가 정규화를
    # 통과해도 그대로여야 한다(옵션이 강등되거나 letter가 바뀌지 않는다).
    f = question_file_from_sdk(SDK_Q, name="n")
    assert normalize_questions_payload(f) == f


def test_rejects_a_question_with_no_options():
    # SDK가 옵션 없는 질문을 보내면 폼에 고를 게 없다.
    with pytest.raises(ValueError):
        question_file_from_sdk([{"question": "q", "options": []}], name="n")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_questions_payload.py -q`
Expected: FAIL — `ImportError: cannot import name 'question_file_from_sdk'`

- [ ] **Step 3: 구현한다**

`backend/pathfinder/agent/questions_payload.py` 끝에 추가:

```python
# SDK AskUserQuestion의 input을 프론트 QuestionFile 형태로 옮긴다. letter는 SDK
# 옵션 순서를 그대로 인덱싱한다 — 답변을 SDK 라벨로 되번역할 때(_answer_to_sdk)
# 그 인덱스가 키이므로 순서가 어긋나면 다른 보기를 고른 것이 된다.
#
# builder._to_question_file에서 옮겨온 것이다. 두 경로가 한 함수로 수렴하면
# is_other 중복 교정(normalize_questions_payload)이 프로토타입 빌드에도 적용된다.
def question_file_from_sdk(sdk_questions: list[dict], *, name: str) -> dict:
    questions = []
    for i, q in enumerate(sdk_questions, start=1):
        raw_options = q.get("options") or []
        options = []
        for j, o in enumerate(raw_options):
            label = str(o.get("label") or "")
            desc = str(o.get("description") or "")
            text = f"{label} — {desc}".rstrip(" —") if desc else label
            options.append({
                "letter": _LETTERS[j] if j < len(_LETTERS) else f"Z{j}",
                "text": text, "is_other": False, "recommended": False,
            })
        questions.append({
            "number": i,
            "category": q.get("header") or None,
            "text": str(q.get("question") or ""),
            "answer": None,
            "multi_select": bool(q.get("multiSelect")),
            "options": options,
        })
    # 정규화가 최종 계약을 강제한다 — 옵션 없는 질문은 여기서 ValueError.
    return normalize_questions_payload(
        {"name": name, "preamble": None, "questions": questions})
```

- [ ] **Step 4: builder를 새 함수로 바꾼다**

`backend/pathfinder/proto/builder.py`:
- `_to_question_file` 함수 정의(37–52행)를 삭제한다.
- 상단 import에 추가: `from pathfinder.agent.questions_payload import question_file_from_sdk`
- 250행 `qfile = _to_question_file(sdk_questions)` →
  `qfile = question_file_from_sdk(sdk_questions, name="prototype-questions")`

- [ ] **Step 5: 통과와 회귀를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_questions_payload.py tests/test_proto_builder_questions.py -q`
Expected: PASS. `test_proto_builder_questions.py`가 `"prototype-questions"` 이름과 옵션 텍스트 형태를 단정하고 있으면 그대로 통과해야 한다 — 실패하면 위 `name=` 인자나 텍스트 조립을 기존 동작에 맞춘다.

- [ ] **Step 6: 커밋한다**

```bash
git add -f backend/pathfinder/agent/questions_payload.py backend/pathfinder/proto/builder.py backend/tests/test_questions_payload.py
git commit -m "$(cat <<'EOF'
refactor(questions): SDK 질문 변환을 한 곳으로 합친다

questions_payload.py(Discovery)와 builder._to_question_file(프로토타입)이
같은 일을 하고 있었다. question_file_from_sdk로 합치면 is_other 중복
교정(0c88fc3)이 프로토타입 빌더에도 적용된다.

letter는 SDK 옵션 순서를 그대로 인덱싱한다 — 답변을 SDK 라벨로
되번역할 때 그 인덱스가 키이므로 순서가 어긋나면 다른 보기를 고른 것이
된다. 변환 결과는 정규화를 통과하며, 그것이 최종 계약을 강제한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: discovery-config + 도구 축소

**Files:**
- Create: `discovery-config/CLAUDE.md`
- Create: `discovery-config/README.md`
- Modify: `backend/pathfinder/agent/tools.py` (6개 → 2개)
- Modify: `.gitignore` (discovery-config 런타임 산출물 제외)
- Test: `backend/tests/test_agent_tools.py` (파일 도구 테스트 제거, 2개만 남김)

**Interfaces:**
- Consumes: 없음
- Produces: `build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list` — `report_stage`, `submit_document` 두 개만 돌려준다. `rules_dir` 인자가 **사라진다**(룰은 Task 1이 워크스페이스에 배치하고 내장 Read가 읽는다). Task 6이 호출한다.

- [ ] **Step 1: discovery-config/CLAUDE.md를 쓴다**

`driver.py`의 `_CONTACT_ADDENDUM`을 옮기되 도구 이름을 SDK 내장으로 바꾼다.

```markdown
# Pathfinder 통합 규약 (UI 접점 — 반드시 준수)

이 파일은 Pathfinder 웹 UI와의 접점만 규정한다. Discovery 워크플로우 자체는
작업 디렉터리의 `CLAUDE.md`(AI-PLC core-workflow)를 따른다.

- 사용자에게 객관식 질문을 할 때는 반드시 **AskUserQuestion** 도구를 사용한다.
  질문 파일(aiplc-docs/**-questions.md)은 기록용으로 계속 작성하되, 질문 전달
  자체는 도구로만 한다.
- 스테이지를 시작/완료할 때마다 **report_stage** 도구를 호출한다. 이 도구가
  aiplc-state.md를 자동 갱신하므로 상태 파일을 직접 만들 필요 없다.
- discovery-document를 생성/갱신할 때마다 **submit_document** 도구를 호출한다.
  **순서가 중요하다: 반드시 파일을 저장한 뒤에 submit_document를 호출한다.**
  파일이 없거나 비어 있으면 도구가 선언을 거부하고 그 이유를 돌려준다 — 그
  경우 파일 저장부터 다시 하라는 뜻이다.
- audit.md에 엔트리를 추가할 때는 **Edit**으로 append한다. **Write는 파일
  전체를 덮어쓴다** — 새 엔트리만 담아 Write를 호출하면 기존 감사 기록이 전부
  유실된다.

## 대화 진행 (사용자 화면에 반드시 노출)

- 도구만 호출하고 끝내지 말 것. **모든 턴에서 사용자에게 보일 대화 텍스트를
  함께 작성한다** — 도구를 호출하기 전에는 지금 무엇을 왜 하는지 한두 문장으로
  알리고, 턴을 마칠 때는 무엇을 했고 다음에 무엇을 요청/기대하는지 요약한다.
  채팅 말풍선은 이 텍스트로 채워진다. 텍스트 없이 도구 호출만 있는 턴은
  사용자에게 빈 말풍선으로 보이므로 금지한다.
- AskUserQuestion으로 질문을 전달하는 턴에서도, 질문 폼을 띄우기 전에 왜 이
  질문이 필요한지 한 문장으로 먼저 설명한다.
```

- [ ] **Step 2: discovery-config/README.md를 쓴다**

```markdown
# discovery-config

Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR` (`PATHFINDER_DISCOVERY_CONFIG_DIR`).

## 왜 proto-config와 분리하는가

`proto-config/CLAUDE.md`는 "프로토타입 디자인은 shadcn-design 스킬을 사용"을
지시한다. 이 지시가 Discovery에 들어가면 문서 작성 중 무관한 UI 스킬을 로드한다.
게다가 프로토타입 빌더는 `skills="all"`이므로 **config dir의 모든 스킬이
활성화**된다 — 공유하면 Discovery가 shadcn-design을 켠 채로 돈다. 역방향도 같다:
여기의 `report_stage`/`submit_document` 규약이 빌더에 들어가면 존재하지 않는
도구를 부르려 한다.

미지정 시 호스트 유저의 `~/.claude`(개인 skills/agents/CLAUDE.md)가 섞여 워크숍
결과가 호스트 설정에 따라 달라진다 — 그래서 격리된 값을 반드시 준다.

## AI-PLC 룰은 여기 두지 않는다

룰은 **워크스페이스**로 간다(`agent/workspace_rules.py`가 배치).
`core-workflow.md:18`이 `Rule details location: ./aws-aiplc-rule-details/`로
CWD 상대경로를 전제하므로, config dir에 두면 그 경로가 맞지 않는다.

| 디렉터리 | 내용 |
|---|---|
| `rule/aiplc-rules/` | 상류 룰 원본(읽기 전용 마스터) |
| 워크스페이스 `{project_id}/` | `CLAUDE.md` + `aws-aiplc-rule-details/` 사본 + 산출물 |
| `discovery-config/` | 이 파일과 통합 규약 `CLAUDE.md`만 |

## skills를 두지 않는다

상류 AI-PLC 셋업은 skills를 쓰지 않는다(CLAUDE.md + 온디맨드 파일 읽기). 룰을
SKILL.md로 승격하면 상류 업데이트를 받아올 수 없고 룰 본문의 읽기 지시와
충돌한다.
```

- [ ] **Step 3: .gitignore에 런타임 산출물 제외를 추가한다**

`proto-config/`와 같은 패턴으로, `.gitignore`의 `proto-config/settings.local.json` 줄 다음에 추가:

```
discovery-config/projects/
discovery-config/.credentials.json
discovery-config/.claude.json
discovery-config/settings.local.json
```

- [ ] **Step 4: tools.py를 2개로 줄인다 (테스트 먼저)**

`backend/tests/test_agent_tools.py`를 수정한다:
- `_tools()` 헬퍼의 이름 목록을 `("report_stage", "submit_document")`로 줄이고, `build_tools(str(workspace), emitted.append)` (rules_dir 인자 제거)로 바꾼다.
- `file_read`/`file_write`/`file_append`/`ask_questions` 관련 테스트 전부 삭제한다. 그 동작은 이제 SDK 내장 도구의 책임이다.
- `report_stage`/`submit_document` 테스트는 그대로 둔다.

`_ws_and_tools()` 헬퍼도 `rules` 인자를 쓰지 않게 정리한다.

- [ ] **Step 5: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: FAIL — `build_tools() missing 1 required positional argument: 'emit'`(시그니처 불일치)

- [ ] **Step 6: tools.py를 구현한다**

`backend/pathfinder/agent/tools.py`:
- `QUESTIONS_SCHEMA_HINT` 상수와 `ask_questions` 도구를 **삭제**한다(내장 `AskUserQuestion`이 스키마를 강제한다).
- `file_read` / `file_write` / `file_append` 도구를 **삭제**한다(내장 Read/Write/Edit).
- `_confine`은 `submit_document`가 계속 쓰므로 유지한다.
- 시그니처를 `def build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list:`로 바꾸고 `rules_dir` 인자와 그 docstring 단락을 제거한다.
- 파일 상단 docstring을 갱신한다:

```python
# backend/pathfinder/agent/tools.py — 에이전트의 UI 접점.
#
# 커스텀 도구는 둘뿐이다. 파일 조작(Read/Write/Edit)과 질문(AskUserQuestion)은
# Claude Agent SDK 내장 도구가 담당한다 — AI-PLC 룰이 전제한 그 도구들이며,
# 자작했던 file_read의 `aiplc-rules/` 프리픽스 특수 처리는 룰을 워크스페이스에
# 배치(agent/workspace_rules.py)하면서 필요 없어졌다.
#
# 여기 남는 둘은 상류 룰에 없는 우리 UI 요구다: 스테이지 사이드바와 문서 패널은
# 모델의 명시적 선언이 있어야 신뢰할 수 있다. aiplc-state.md 쓰기에서
# 역추론하면 한 턴에 여러 번 갱신될 때 UI가 흔들린다.
```

- `@tool` 데코레이터(strands)를 걷어내고 Claude Agent SDK 방식으로 바꾼다. `claude_agent_sdk`의 `@tool` 데코레이터와 `create_sdk_mcp_server`를 쓰며, 정확한 형태는 `proto-config`가 아니라 SDK 문서를 따른다 — **Step 7에서 확인한 뒤 확정한다.**

- [ ] **Step 7: SDK의 커스텀 도구 등록 방식을 확인한다**

Run:
```bash
cd backend && .venv/bin/python -c "
import claude_agent_sdk as s
print([n for n in dir(s) if 'tool' in n.lower() or 'mcp' in n.lower()])
"
```

확인 대상: `tool` 데코레이터와 `create_sdk_mcp_server`의 존재와 시그니처. 결과에 맞춰 Step 6의 도구 정의와 `ClaudeAgentOptions`의 `mcp_servers`/`allowed_tools` 배선을 확정한다. **`report_stage`와 `submit_document`의 본문 로직(`upsert_stage` 호출, 파일 존재·비어있음 검사, `emit`)은 기존 `tools.py`에서 그대로 옮긴다** — 바뀌는 것은 등록 방식뿐이다.

- [ ] **Step 8: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_tools.py -q`
Expected: PASS (report_stage / submit_document 테스트만)

- [ ] **Step 9: 커밋한다**

```bash
git add -f discovery-config/ backend/pathfinder/agent/tools.py backend/tests/test_agent_tools.py .gitignore
git commit -m "$(cat <<'EOF'
refactor(agent): 커스텀 도구를 6개에서 2개로 줄이고 config dir을 분리한다

파일 조작(Read/Write/Edit)과 질문(AskUserQuestion)은 SDK 내장 도구가
담당한다 — AI-PLC 룰이 전제한 그 도구들이다. 자작했던 file_read의
`aiplc-rules/` 프리픽스 특수 처리는 룰을 워크스페이스에 배치하면서
필요 없어졌다. QUESTIONS_SCHEMA_HINT도 함께 삭제한다: 내장
AskUserQuestion이 스키마를 강제하므로 프롬프트로 지시할 필요가 없다.

남는 둘은 상류 룰에 없는 우리 UI 요구다. 스테이지 사이드바와 문서
패널은 모델의 명시적 선언이 있어야 신뢰할 수 있다 — aiplc-state.md
쓰기에서 역추론하면 한 턴에 여러 번 갱신될 때 UI가 흔들린다.

discovery-config/를 proto-config/와 분리한다. 프로토타입 빌더는
skills="all"이므로 config dir의 모든 스킬이 활성화된다 — 공유하면
Discovery가 shadcn-design을 켠 채로 돈다. 역방향도 같다: 여기의
report_stage 규약이 빌더에 들어가면 없는 도구를 부르려 한다.

AI-PLC 룰은 discovery-config가 아니라 워크스페이스로 간다(README에
근거 기록). core-workflow.md:18이 CWD 상대경로를 전제한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: ClaudeDriver

**Files:**
- Create: `backend/pathfinder/agent/claude_driver.py`
- Test: `backend/tests/test_claude_driver.py`, `backend/tests/test_claude_driver_contract.py`

**Interfaces:**
- Consumes: `place_rules`(Task 1), `save_pending`/`load_pending`/`clear_pending`(Task 2), `assert_driver_contract`(Task 3), `question_file_from_sdk`(Task 4), `build_tools`(Task 5)
- Produces: `ClaudeDriver(workspace: str, rules_dir: str, config_dir: str, s3: S3StoreLike, anthropic_model: str | None = None, client_factory: Callable[[dict], Any] | None = None)` — Task 8의 `driver_factory`가 생성한다. 메서드 3개는 Global Constraints의 계약과 동일.

- [ ] **Step 1: 계약 테스트 어댑터를 쓴다**

`backend/tests/test_claude_driver_contract.py`:

```python
# Task 3에서 StrandsDriver로 확정한 계약을 ClaudeDriver가 그대로 통과하는지.
# 통과하면 runner.py와 프론트가 두 드라이버를 구분하지 못한다(= 기능 동등).
import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from tests.driver_contract import assert_driver_contract
from tests.fakes.fake_sdk import FakeSdkClient  # builder 테스트에서 쓰는 가짜
from tests.fakes.in_memory_s3 import FakeS3Store


def _make_claude_driver(scripted: dict, tmp_path_factory=None):
    import tempfile
    ws = tempfile.mkdtemp()
    rules = tempfile.mkdtemp()
    # place_rules가 요구하는 최소 레이아웃.
    from pathlib import Path
    core = Path(rules) / "aws-aiplc-rules"
    core.mkdir(parents=True)
    (core / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")

    def factory(session):
        return FakeSdkClient(scripted)

    driver = ClaudeDriver(workspace=ws, rules_dir=rules,
                          config_dir=tempfile.mkdtemp(), s3=FakeS3Store(),
                          client_factory=factory)
    return driver, {"session_id": "s-1"}


@pytest.mark.asyncio
async def test_claude_driver_satisfies_the_same_contract():
    await assert_driver_contract(_make_claude_driver)
```

**`FakeSdkClient`는 확장하지 않는다.** 기존 것은 `script`를 SDK 메시지 객체
리스트(`AssistantMessage` / `ResultMessage`)로 받고 builder 테스트가 그 형태에
의존한다. 계약 테스트의 dict 대본을 그 객체 리스트로 **번역하는 헬퍼**를 어댑터
테스트 안에 둔다 — 가짜를 건드리지 않으므로 builder 테스트에 무해하다.

`test_claude_driver_contract.py`에 함께 넣을 번역 헬퍼:

```python
from tests.fakes.fake_sdk import (
    AssistantMessage, FakeSdkClient, ResultMessage, TextBlock, ToolUseBlock,
)


class _RaisingSdkClient(FakeSdkClient):
    """SDK 예외 경로 — 계약은 그것이 "agent turn failed"로 강등되기를 요구한다."""

    async def receive_response(self):
        raise RuntimeError("boom")
        yield  # pragma: no cover — async generator로 만들기 위한 것


def _script_from(scripted: dict):
    """계약 테스트의 dict 대본 → FakeSdkClient가 먹는 SDK 메시지 객체 리스트."""
    blocks = [TextBlock(text=t) for t in scripted.get("text", [])]
    blocks += [ToolUseBlock(name=n, input={}) for n in scripted.get("tools", [])]
    if scripted.get("questions"):
        # AskUserQuestion 호출을 흉내낸다 — 드라이버의 can_use_tool 콜백이
        # 이것을 가로채 questions 이벤트로 바꾸는 것이 계약이다.
        blocks.append(ToolUseBlock(name="AskUserQuestion", input={
            "questions": [{"question": "다음 단계는?", "header": "Next",
                           "multiSelect": False,
                           "options": [{"label": "진행", "description": "계속"},
                                       {"label": "종료", "description": "핸드오프"}]}],
        }))
    msgs = []
    if blocks:
        msgs.append(AssistantMessage(content=blocks))
    msgs.append(ResultMessage())
    return msgs
```

`TextBlock` / `ToolUseBlock` / `AssistantMessage` / `ResultMessage`의 실제
생성자 인자는 `tests/fakes/fake_sdk.py:13-33`을 열어 확인하고 맞춘다(dataclass
필드명이 다르면 그쪽을 따른다).

**질문 대본의 한계:** `FakeSdkClient`는 `can_use_tool` 콜백을 호출하지 않는다 —
실제 SDK만 그 훅을 건다. 그래서 계약의 `_assert_questions_carry_an_interrupt_id`는
`ClaudeDriver`에서 **드라이버의 `_on_can_use_tool`을 직접 호출**해 검증한다.
어댑터에서 이렇게 감싼다:

```python
def _make_claude_driver(scripted: dict):
    ...
    if scripted.get("questions"):
        # 실제 SDK만 can_use_tool을 호출하므로, 가짜에서는 드라이버의 콜백을
        # 직접 태워 questions 이벤트가 큐에 들어가게 한다.
        async def _pump():
            await driver._on_can_use_tool("AskUserQuestion", {
                "questions": [{"question": "다음 단계는?",
                               "options": [{"label": "진행"}]}]}, None)
        fake.on_query = _pump   # FakeSdkClient에 없으면 아래 주의 참조
```

`FakeSdkClient`에 `on_query` 훅이 없으므로, 대신 **드라이버의 큐에 직접 넣는**
방식을 쓴다 — 계약이 요구하는 것은 "questions 이벤트가 interrupt_id를 싣고
나온다"이고, 그 payload를 만드는 것은 `_on_can_use_tool`이다. 어댑터에서
`asyncio.create_task(driver._on_can_use_tool(...))`로 태우고 `run()`의 큐 폴링이
그것을 집어내게 한다. 정확한 배선은 Task 6 Step 5에서 테스트를 돌려 확정한다.

- [ ] **Step 2: 드라이버 고유 테스트를 쓴다**

`backend/tests/test_claude_driver.py`:

```python
# 계약(driver_contract.py) 밖의 ClaudeDriver 고유 동작.
import json
from pathlib import Path

import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from pathfinder.agent.pending_store import PENDING_KEY, save_pending
from tests.fakes.fake_sdk import FakeSdkClient
from tests.fakes.in_memory_s3 import FakeS3Store


def _driver(tmp_path, scripted, s3=None):
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    captured = {}

    def factory(session):
        captured["session"] = session
        return FakeSdkClient(scripted)

    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=s3 or FakeS3Store(),
                     client_factory=factory)
    return d, ws, captured


@pytest.mark.asyncio
async def test_places_the_rules_before_the_first_turn(tmp_path):
    # 룰이 없으면 에이전트가 워크플로우를 모르는 채로 돈다.
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "WORKFLOW"


@pytest.mark.asyncio
async def test_persists_pending_questions_to_s3(tmp_path):
    # 새로고침 후 폼 복원의 근거. 인메모리 Future만으로는 재시작을 못 넘는다.
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert PENDING_KEY in s3.blobs
    saved = json.loads(s3.blobs[PENDING_KEY])
    assert saved["interrupt_id"]
    assert saved["sdk_questions"]  # 답변 되번역에 필요


@pytest.mark.asyncio
async def test_pending_reads_from_s3_after_a_restart(tmp_path):
    # 인메모리 상태가 전혀 없는 새 드라이버 — 백엔드 재시작을 재현한다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "q", "options": []}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    payload = await d.pending({"session_id": "s-1"})
    assert payload is not None
    assert json.loads(payload)["interrupt_id"] == "i-1"


@pytest.mark.asyncio
async def test_clears_pending_after_answers_are_submitted(tmp_path):
    # 남아 있으면 새로고침 시 답변 불가한 옛 폼이 뜬다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "질문",
                                       "options": [{"label": "예"}]}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    assert PENDING_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_resumes_with_the_answer_as_text_when_the_future_is_gone(tmp_path):
    # 재시작 후 답변: 기다리던 Future가 없으므로 resume + 텍스트 턴으로 전달한다.
    # 프롬프트에 질문과 고른 라벨이 함께 들어가야 모델이 맥락을 잇는다.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"},
                                                   {"label": "종료"}]}],
                       session_id="s-1")
    fake = FakeSdkClient({"text": ["ok"]})
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    d._client_factory = lambda session: fake  # type: ignore[attr-defined]
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    sent = " ".join(fake.queries)
    assert "다음 단계는?" in sent
    assert "진행" in sent


@pytest.mark.asyncio
async def test_a_pending_s3_failure_does_not_kill_the_turn(tmp_path):
    # pending 영속은 복원 편의다. 그것 때문에 진행 중인 질문을 잃는 게 더 큰 손실.
    class _Broken(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    d, _, _ = _driver(tmp_path, {"questions": True}, s3=_Broken())
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert "questions" in kinds
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_uses_the_discovery_config_dir_not_the_prototype_one(tmp_path):
    # 공유하면 Discovery가 shadcn-design 스킬을 켠 채로 돈다.
    d, _, captured = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # client_factory에 넘어간 옵션에서 config dir을 확인한다.
    assert str(tmp_path / "cfg") == d._config_dir
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.agent.claude_driver'`

- [ ] **Step 4: 구현한다**

`backend/pathfinder/agent/claude_driver.py`. **`proto/builder.py`의 다음 부분을 참조 구현으로 그대로 옮긴다**(주석의 근거까지 함께 — 그 주석들은 실측으로 얻은 것이다):

- `_default_client_factory`(93–157행) — `ClaudeAgentOptions` 조립. Discovery용으로 바꿀 것: `cwd=self._workspace`, `env["CLAUDE_CONFIG_DIR"]=self._config_dir`, `setting_sources=["user","project"]`, **`skills` 미지정**, `permission_mode="bypassPermissions"`, `session_id`/`resume` 중 하나만(주석의 `--session-id`/`--resume` 충돌 근거 유지), `can_use_tool=self._on_can_use_tool`, `hooks={"PostToolUse":[HookMatcher(matcher="Write|Edit|MultiEdit", hooks=[self._on_post_tool_use])]}`.
- `_suppress_shadowed_callback_warning`(159–174행) — 그대로.
- `_on_post_tool_use`(211–222행) — 그대로(`file_changed` 발행).
- `_answer_to_sdk`(224–240행) — 그대로.
- `_on_can_use_tool`(242–283행) — `question_file_from_sdk`(Task 4)를 쓰고, payload 생성 직후 `save_pending`(Task 2)을 **try/except로 감싸** 호출한다(실패는 로그만).
- `_translate`(286–300행) — 그대로.
- `run`의 큐 폴링 루프(302–367행) — 그대로. 턴 시작 전에 `place_rules(self._workspace, self._rules_dir)`를 호출한다.
- `drain_queue`(199–203행), `_ensure_client`(205–210행) — 그대로.

**새로 쓰는 것:**

```python
    async def run(self, text: str, session: dict):
        """계약: runner.py:129가 호출한다."""
        # 룰 배치는 매 턴이다 — 워크스페이스는 휘발이고(runner가 S3에서 재구성)
        # 룰이 없으면 에이전트가 워크플로우를 모르는 채로 돈다.
        try:
            place_rules(self._workspace, self._rules_dir)
        except Exception:
            _log.exception("rule placement failed")
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        async for ev in self._stream(text, session):
            yield ev

    async def run_answers(self, interrupt_id: str, answers: dict[str, str],
                          session: dict):
        """계약: runner.py:167이 호출한다.

        두 경로가 있다. 대기 중인 Future가 있으면 정상 왕복(도구 결과로 주입).
        없으면 백엔드가 재시작된 것이므로 resume + 텍스트 턴으로 전달한다 —
        모델은 트랜스크립트에서 질문 맥락을 이미 갖고 있다.
        """
        fut = self._pending_question
        if fut is not None and not fut.done():
            fut.set_result(answers)
            await self._clear_pending_quietly()
            async for ev in self._drain_until_done():
                yield ev
            return
        async for ev in self._resume_with_answers(interrupt_id, answers, session):
            yield ev

    async def pending(self, session: dict) -> str | None:
        """계약: runner.py:183이 호출한다. 인메모리를 먼저 보고, 없으면 S3 —
        새로고침(같은 프로세스)과 백엔드 재시작을 둘 다 덮는다."""
        if self._pending_payload is not None:
            return self._pending_payload
        data = await load_pending(self._s3)
        if data is None:
            return None
        return json.dumps({"interrupt_id": data["interrupt_id"],
                           "questions": data["questions"]},
                          ensure_ascii=False)
```

`_resume_with_answers`는 저장된 `sdk_questions`로 사람이 읽을 문장을 만들어 텍스트 턴으로 보낸다:

```python
    async def _resume_with_answers(self, interrupt_id: str,
                                   answers: dict[str, str], session: dict):
        data = await load_pending(self._s3)
        if data is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        lines = []
        for k, v in answers.items():
            try:
                q = data["sdk_questions"][int(k) - 1]
            except (ValueError, IndexError, KeyError, TypeError):
                continue
            label = self._answer_to_sdk(v, q.get("options", []))
            lines.append(f"- {q.get('question', '')} → {label}")
        prompt = ("[질문 답변] 앞서 드린 질문에 사용자가 답했습니다. "
                  "이 답변을 반영해 이어서 진행해 주세요.\n" + "\n".join(lines))
        await self._clear_pending_quietly()
        async for ev in self._stream(prompt, session, resume=True):
            yield ev
```

`_clear_pending_quietly`는 `clear_pending`을 try/except로 감싼다.

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py tests/test_claude_driver_contract.py -q`
Expected: PASS

- [ ] **Step 6: 전체 백엔드 회귀를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (Task 5에서 삭제한 도구 테스트만큼 총 개수가 줄어든다)

- [ ] **Step 7: 커밋한다**

```bash
git add -f backend/pathfinder/agent/claude_driver.py backend/tests/test_claude_driver.py backend/tests/test_claude_driver_contract.py
git commit -m "$(cat <<'EOF'
feat(agent): ClaudeDriver — StrandsDriver와 같은 계약의 Claude Agent SDK 드라이버

proto/builder.py가 같은 문제를 이미 풀어 프로덕션에서 돌고 있으므로
참조 구현으로 옮겼다 — 질문 왕복(can_use_tool 가로채기), 이벤트 번역,
PostToolUse 훅, 질문 대기 중 스트림 공백 레이스 처리, --session-id/
--resume 충돌 회피. 실측으로 얻은 주석의 근거도 함께 옮겼다.

Task 3의 계약 테스트를 그대로 통과하므로 runner.py와 프론트는 두
드라이버를 구분하지 못한다.

pending 질문은 S3에 영속한다. run_answers는 두 경로다 — 대기 중인
Future가 있으면 도구 결과로 주입하고, 없으면(백엔드 재시작) resume +
텍스트 턴으로 전달한다. 모델은 트랜스크립트에서 질문 맥락을 이미 갖고
있으므로 이어진다.

pending S3 실패는 턴을 죽이지 않는다 — 복원 편의 때문에 진행 중인
질문을 잃는 게 더 큰 손실이다(runner._sync_abandoned_turn과 같은 판단).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 프론트 활동 라벨 확장

Task 6과 독립이며 기존 키를 남기므로 Strands 드라이버에 무해하다.

**Files:**
- Modify: `frontend/components/canvas/AiMessage.tsx:9-16`
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx:100`
- Test: `frontend/components/canvas/AiMessage.test.tsx`

**Interfaces:**
- Consumes: `AgentEvent.text`(도구 이름)
- Produces: 없음 (UI 표시)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/components/canvas/AiMessage.test.tsx` 끝에 추가:

```tsx
describe("Claude Agent SDK 도구명 라벨 (regression)", () => {
  // 드라이버가 바뀌면 status 이벤트의 도구 이름이 SDK 내장 이름으로 온다.
  // 매핑에 없으면 폴백이 발동해 사용자에게 "Write 실행 중…" 같은 영어 도구명이
  // 노출된다 — 크래시는 아니지만 UX가 조용히 나빠진다.
  const CASES: Array<[string, RegExp]> = [
    ["AskUserQuestion", /질문을 준비하고 있어요/],
    ["Write", /문서를 작성하고 있어요/],
    ["Edit", /문서를 작성하고 있어요/],
    ["MultiEdit", /문서를 작성하고 있어요/],
    ["Read", /자료를 확인하고 있어요/],
    ["Glob", /자료를 찾고 있어요/],
  ];

  for (const [tool, label] of CASES) {
    it(`maps ${tool} to a Korean activity label`, () => {
      render(
        <AiMessage
          item={{
            id: "a1", role: "ai", text: "", streaming: true, error: null,
            trace: [{ kind: "status", text: tool, path: null }],
          }}
        />,
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      // 영어 도구명이 그대로 보이면 안 된다.
      expect(screen.queryByText(new RegExp(`${tool} 실행 중`))).toBeNull();
    });
  }

  it("keeps the Strands tool names working during the env-toggle period", () => {
    // 두 드라이버가 공존하는 기간에는 양쪽 다 올바른 라벨이 나와야 한다.
    render(
      <AiMessage
        item={{
          id: "a1", role: "ai", text: "", streaming: true, error: null,
          trace: [{ kind: "status", text: "file_write", path: null }],
        }}
      />,
    );
    expect(screen.getByText(/문서를 작성하고 있어요/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/AiMessage.test.tsx`
Expected: FAIL — `Write` 등이 `Write 실행 중…`으로 렌더돼 라벨을 찾지 못한다.

- [ ] **Step 3: ACTIVITY_LABELS를 확장한다**

`frontend/components/canvas/AiMessage.tsx:9-16`을 교체:

```tsx
// 도구명 → 사용자 친화 활동 문구. 턴 진행 중 "무슨 일이 벌어지고 있는지"를
// 접힌 추론 과정 밖에서 상시 보여준다 — 없으면 질문/문서 생성처럼 수십 초
// 걸리는 도구 실행 동안 화면이 멈춘 것처럼 보인다.
//
// 두 드라이버가 서로 다른 도구 이름을 보낸다: Claude Agent SDK는 내장 도구명
// (Write/Read/Edit/AskUserQuestion), Strands는 자작 도구명(file_write/…).
// 매핑에 없으면 activityLabel의 폴백이 영어 도구명을 그대로 노출하므로 양쪽을
// 모두 둔다(PATHFINDER_DISCOVERY_DRIVER 폴백 기간 동안 필요).
const ACTIVITY_LABELS: Record<string, string> = {
  // Claude Agent SDK 내장
  AskUserQuestion: "질문을 준비하고 있어요…",
  Write: "문서를 작성하고 있어요…",
  Edit: "문서를 작성하고 있어요…",
  MultiEdit: "문서를 작성하고 있어요…",
  Read: "자료를 확인하고 있어요…",
  Glob: "자료를 찾고 있어요…",
  // 양쪽 드라이버 공통 커스텀 도구
  report_stage: "진행 상황을 기록하고 있어요…",
  submit_document: "문서를 제출하고 있어요…",
  // Strands 드라이버 (env 폴백 기간 유지)
  ask_questions: "질문을 준비하고 있어요…",
  file_write: "문서를 작성하고 있어요…",
  file_append: "문서를 작성하고 있어요…",
  file_read: "자료를 확인하고 있어요…",
};
```

- [ ] **Step 4: 첨부 안내에서 도구 이름을 제거한다**

`frontend/app/projects/[projectId]/workspace/page.tsx:100`:

```tsx
      (p) => `[첨부 파일: ${p} — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 이 파일을 읽어보세요.]`,
```

도구 이름을 언급하지 않는다 — 모델에게 가는 지시문이므로 드라이버마다 다른 도구명을 넣으면 없는 도구를 지목하게 되고, 도구가 바뀔 때 다시 깨진다.

- [ ] **Step 5: 통과와 회귀를 확인한다**

Run: `cd frontend && npx vitest run && npm run build`
Expected: 전체 PASS + 빌드 성공

- [ ] **Step 6: 커밋한다**

```bash
git add frontend/components/canvas/AiMessage.tsx frontend/components/canvas/AiMessage.test.tsx "frontend/app/projects/[projectId]/workspace/page.tsx"
git commit -m "$(cat <<'EOF'
fix(canvas): Claude Agent SDK 도구명도 한글 활동 라벨로 매핑한다

ACTIVITY_LABELS에 Strands 이름만 있어서, 드라이버가 바뀌면 폴백이
발동해 사용자에게 "Write 실행 중…" 같은 영어 도구명이 노출된다.
크래시는 아니지만 UX가 조용히 나빠진다.

내장 도구명(AskUserQuestion/Write/Edit/MultiEdit/Read/Glob)을 추가하고
Strands 키는 남긴다 — PATHFINDER_DISCOVERY_DRIVER로 두 드라이버가
공존하는 기간에 양쪽 다 올바른 라벨이 나와야 한다.

첨부 안내 프롬프트의 "file_read로 읽으세요"도 고친다. 이건 모델에게
가는 지시문이라 없는 도구를 지목하게 된다 — 도구 이름을 언급하지 않는
표현으로 바꿔 앞으로 도구가 바뀌어도 깨지지 않게 한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: env 토글 배선 + 인프라

**Files:**
- Modify: `backend/pathfinder/app.py:128-135`(`_workspaces_dir` 아래 `driver_factory`)
- Modify: `infra/lib/user-data.ts`
- Modify: `infra/test/user-data.assert.ts`
- Modify: `backend/.env.example`, `README.md`
- Test: `backend/tests/test_driver_factory.py`(신규)

**Interfaces:**
- Consumes: `ClaudeDriver`(Task 6), `StrandsDriver`(기존)
- Produces: 없음 (배선 종점)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_driver_factory.py`:

```python
# env 토글 — 워크숍 중 문제가 나면 env 하나로 되돌린다. 다섯 번의 배포 사고를
# 겪은 만큼 탈출로를 둔다.
from pathlib import Path

import pytest

from pathfinder import app as app_module
from pathfinder.agent.claude_driver import ClaudeDriver
from pathfinder.agent.driver import StrandsDriver


def test_defaults_to_the_claude_driver(monkeypatch, tmp_path):
    monkeypatch.delenv("PATHFINDER_DISCOVERY_DRIVER", raising=False)
    d = app_module.driver_factory("p1", tmp_path)
    assert isinstance(d, ClaudeDriver)


def test_strands_opts_back_to_the_old_driver(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "strands")
    d = app_module.driver_factory("p1", tmp_path)
    assert isinstance(d, StrandsDriver)


def test_an_unknown_value_is_a_deploy_accident(monkeypatch, tmp_path):
    # 오타가 조용히 기본값으로 떨어지면 어느 드라이버가 도는지 알 수 없다.
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claud")
    with pytest.raises(ValueError):
        app_module.driver_factory("p1", tmp_path)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_driver_factory.py -q`
Expected: FAIL — 현재 `driver_factory`는 항상 `StrandsDriver`를 돌려준다.

- [ ] **Step 3: driver_factory를 구현한다**

`backend/pathfinder/app.py`의 `driver_factory`(134–135행)를 교체:

```python
def _discovery_config_dir() -> Path:
    return Path(os.environ.get("PATHFINDER_DISCOVERY_CONFIG_DIR",
                               "~/pathfinder-discovery-config")).expanduser()


# Discovery 드라이버 선택. 기본은 Claude Agent SDK — AI-PLC 룰이 전제한 실행
# 환경이다. `strands`로 되돌릴 수 있게 둔 것은 워크숍 중 문제가 났을 때의
# 탈출로다(EC2 교체 없이 env + restart). 워크숍이 끝나면 StrandsDriver와
# strands-agents 의존성을 삭제한다.
def driver_factory(project_id: str, local_root: Path):
    choice = os.environ.get("PATHFINDER_DISCOVERY_DRIVER", "claude")
    if choice == "strands":
        return StrandsDriver(workspace=str(local_root), rules_dir=_rules_dir())
    if choice != "claude":
        # 오타가 조용히 기본값으로 떨어지면 어느 드라이버가 도는지 알 수 없다.
        raise ValueError(
            f"unknown PATHFINDER_DISCOVERY_DRIVER {choice!r}; expected 'claude' or 'strands'")
    return ClaudeDriver(
        workspace=str(local_root),
        rules_dir=_rules_dir(),
        config_dir=str(_discovery_config_dir()),
        s3=s3_store_factory(project_id),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL"),
    )
```

상단 import에 `from pathfinder.agent.claude_driver import ClaudeDriver`를 추가한다.

- [ ] **Step 4: 통과와 백엔드 전체 회귀를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: user-data에 env를 주입한다 (테스트 먼저)**

`infra/test/user-data.assert.ts` 끝에 추가:

```typescript
// 14) Discovery 드라이버 env — 미주입 시 config dir이 호스트 유저의 ~/.claude로
// 떨어져 개인 skills/agents가 워크숍 결과에 섞인다(proto-config와 같은 이유).
assert.match(s, /PATHFINDER_DISCOVERY_CONFIG_DIR=/,
  'backend must get PATHFINDER_DISCOVERY_CONFIG_DIR — otherwise the host user\'s ~/.claude leaks into Discovery');
assert.match(s, /\/opt\/pathfinder\/discovery-config/,
  'discovery config dir must point at the shipped asset path');
// proto와 discovery의 config dir이 서로 다른 경로여야 한다 — 공유하면
// Discovery가 shadcn-design 스킬을 켠 채로 돈다(skills="all").
const protoCfg = s.match(/PATHFINDER_PROTO_CONFIG_DIR=([^\s\\]+)/);
const discCfg = s.match(/PATHFINDER_DISCOVERY_CONFIG_DIR=([^\s\\]+)/);
assert.ok(protoCfg && discCfg, 'both config dirs must be set');
assert.notStrictEqual(protoCfg![1], discCfg![1],
  'proto and discovery CLAUDE_CONFIG_DIRs must not be the same path');
console.log('OK  user-data: discovery config dir set and distinct from proto');
```

Run: `cd infra && npx ts-node test/user-data.assert.ts`
Expected: FAIL — env가 아직 없다.

- [ ] **Step 6: user-data를 수정한다**

`infra/lib/user-data.ts`에서 `PATHFINDER_PROTO_CONFIG_DIR`을 주입하는 줄을 찾아 그 아래에 추가한다(`${APP}`은 에셋이 풀린 앱 트리 경로 변수 — 기존 proto 줄과 같은 형태를 따른다):

```
Environment=PATHFINDER_DISCOVERY_CONFIG_DIR=/opt/pathfinder/discovery-config
```

`proto-config`가 앱 트리에서 `/opt/pathfinder/proto-config`로 배치되는 방식과 동일하게 `discovery-config`도 배치하는 셸 줄을 함께 추가한다(기존 proto 배치 줄 바로 아래).

Run: `cd infra && npm test`
Expected: PASS (전체 단정)

- [ ] **Step 7: 문서를 갱신한다**

`backend/.env.example`의 Cognito 절 위에 추가:

```
# ---- Discovery 드라이버 ----
# claude(기본) = Claude Agent SDK, strands = 구 드라이버(워크숍 중 탈출로).
# 알 수 없는 값은 배포 사고로 간주해 기동 시 ValueError.
# PATHFINDER_DISCOVERY_DRIVER=claude
# Discovery 에이전트 전용 CLAUDE_CONFIG_DIR. 미설정 시 호스트 유저의 ~/.claude가
# 섞인다 — proto용과 반드시 다른 경로여야 한다(discovery-config/README.md 참조).
# PATHFINDER_DISCOVERY_CONFIG_DIR=/home/ec2-user/pathfinder-discovery-config
```

`README.md`의 백엔드 환경변수 표에 두 줄을 추가한다:

| `PATHFINDER_DISCOVERY_DRIVER` | `claude` | Discovery 드라이버. `strands`로 구 드라이버 폴백. 그 외 값은 기동 시 ValueError |
| `PATHFINDER_DISCOVERY_CONFIG_DIR` | `~/pathfinder-discovery-config` | Discovery 에이전트 전용 `CLAUDE_CONFIG_DIR`. proto용과 달라야 한다 — 자세한 내용은 `discovery-config/README.md` |

`README.md`의 구조 설명(23–25행 근처)에서 백엔드 줄의 "인프로세스 Strands 에이전트"를 "인프로세스 Discovery 에이전트(Claude Agent SDK)"로 바꾼다.

- [ ] **Step 8: 커밋한다**

```bash
git add -f backend/pathfinder/app.py backend/tests/test_driver_factory.py infra/lib/user-data.ts infra/test/user-data.assert.ts backend/.env.example README.md
git commit -m "$(cat <<'EOF'
feat(agent): PATHFINDER_DISCOVERY_DRIVER로 드라이버를 전환한다

기본은 claude(Claude Agent SDK) — AI-PLC 룰이 전제한 실행 환경이다.
strands로 되돌릴 수 있게 둔 것은 워크숍 중 문제가 났을 때의 탈출로다
(EC2 교체 없이 env + restart). 알 수 없는 값은 배포 사고로 간주해
ValueError를 던진다 — 오타가 조용히 기본값으로 떨어지면 어느 드라이버가
도는지 알 수 없다.

user-data가 PATHFINDER_DISCOVERY_CONFIG_DIR을 주입하고, 인프라 테스트가
proto용과 다른 경로임을 단정한다 — 공유하면 skills="all" 때문에
Discovery가 shadcn-design을 켠 채로 돈다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 배포 검증 체크리스트

유닛 테스트로는 부족하다. 2026-07-26의 미들웨어 Edge 런타임 500이 교훈 — 유닛 테스트가 통과했는데 실제 런타임에서 죽었다.

**Files:**
- Create: `docs/superpowers/checklists/2026-07-27-discovery-driver-e2e.md`

- [ ] **Step 1: 체크리스트를 쓴다**

```markdown
# Discovery 드라이버(Claude Agent SDK) 배포 검증

전제: `cd infra && npx cdk deploy PathfinderHostingStack --require-approval never`
후 EC2 첫 빌드 완료(5~10분, 그 동안 CloudFront 502는 정상).

## 1. 배선 확인 (SSM)

- [ ] `systemctl show pathfinder-backend -p Environment | tr ' ' '\n' | grep DISCOVERY`
      → `PATHFINDER_DISCOVERY_CONFIG_DIR=/opt/pathfinder/discovery-config`
- [ ] `ls /opt/pathfinder/discovery-config/CLAUDE.md` → 존재
- [ ] proto와 discovery config dir이 다른 경로인지 눈으로 확인

## 2. 룰 배치

- [ ] 프로젝트를 하나 만들고 첫 메시지를 보낸다
- [ ] SSM에서 워크스페이스 확인:
      `ls $PATHFINDER_WORKSPACES_DIR/<pid>/` → `CLAUDE.md`,
      `aws-aiplc-rule-details/`, `aiplc-docs/`
- [ ] `diff /opt/pathfinder/rule/aiplc-rules/aws-aiplc-rules/core-workflow.md \
       $PATHFINDER_WORKSPACES_DIR/<pid>/CLAUDE.md` → 차이 없음

## 3. 첫 턴 (Workspace Detection)

- [ ] 채팅에 AI 텍스트가 뜬다(빈 말풍선이 아님)
- [ ] 활동 라인에 **한글** 문구가 뜬다 — "자료를 확인하고 있어요…" 등.
      `Read 실행 중…` 같은 영어 도구명이 보이면 Task 7이 누락됐다
- [ ] 사이드바에 스테이지가 표시된다(report_stage 동작)
- [ ] 우측 패널에 산출물 경로가 쌓인다(PostToolUse 훅 → file_changed)

## 4. 질문 왕복

- [ ] 질문 폼이 우측 패널에 뜬다
- [ ] 보기 텍스트가 정상이다 — "Other — 직접 입력"이 **하나만** 있고, 실질
      보기의 텍스트가 사라지지 않았다(is_other 중복 회귀 확인)
- [ ] 답변을 제출하면 턴이 이어진다

## 5. 새로고침 복원

- [ ] 질문이 떠 있는 상태에서 브라우저를 새로고침한다
- [ ] 질문 폼이 그대로 복원된다
- [ ] 그 폼에 답변하면 정상 진행된다

## 6. 백엔드 재시작 복원 (재시작 경로)

- [ ] 질문이 떠 있는 상태에서 SSM으로
      `sudo systemctl restart pathfinder-backend`
- [ ] 브라우저 새로고침 → 질문 폼이 복원된다
- [ ] 답변을 제출한다 → **모델이 그 답변을 질문 답변으로 이해하고 이어간다**
      (여기가 설계에서 "실제 워크숍 검증 필요"로 남긴 지점이다. 모델이 맥락을
      잃으면 프롬프트 문구를 조정한다)

## 7. 롤백 리허설

- [ ] SSM에서
      `sudo systemctl set-environment PATHFINDER_DISCOVERY_DRIVER=strands` 후
      backend restart → 기존 드라이버로 턴이 정상 동작
- [ ] 다시 `claude`로 되돌리고 restart → 정상

## 8. 프로토타입 빌드 회귀

Discovery 변경이 빌더를 깨지 않았는지 — `question_file_from_sdk` 통합과
config dir 분리의 영향 범위다.

- [ ] 프로토타입 세션을 시작해 첫 질문까지 도달한다
- [ ] 질문 폼이 정상 렌더된다
- [ ] shadcn-design 스킬이 여전히 프로토타입 쪽에서만 동작한다
```

- [ ] **Step 2: 커밋한다**

```bash
git add -f docs/superpowers/checklists/2026-07-27-discovery-driver-e2e.md
git commit -m "$(cat <<'EOF'
docs: Discovery 드라이버 배포 검증 체크리스트

유닛 테스트로는 부족하다 — 2026-07-26의 미들웨어 Edge 런타임 500이
교훈이다(유닛 테스트 통과, 실제 런타임에서 죽음).

8개 절: 배선·룰 배치·첫 턴·질문 왕복·새로고침 복원·백엔드 재시작
복원·롤백 리허설·프로토타입 빌드 회귀. 6번이 설계에서 "실제 워크숍
검증 필요"로 남긴 지점이다(재시작 후 답변을 모델이 질문 답변으로
이해하는지).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review 결과

**1. 스펙 커버리지**

| 스펙 요구 | Task |
|---|---|
| 워크스페이스 룰 배치(CLAUDE.md + rule-details) | 1 |
| pending 질문 S3 영속 | 2 |
| 계약 테스트 공유 | 3, 6 |
| 정규화 통합(builder와) | 4 |
| config dir 분리 | 5, 8 |
| 통합 규약 이관 | 5 |
| 도구 6→2 | 5 |
| ClaudeDriver 3-메서드 | 6 |
| 재시작 후 답변(resume + 텍스트) | 6 |
| 프론트 활동 라벨 + 첨부 문구 | 7 |
| env 토글 + 인프라 | 8 |
| 배포 검증 | 9 |

`_system_prompt()` 제거는 Task 6에서 `ClaudeDriver`가 그것을 쓰지 않음으로써 달성된다(`StrandsDriver`의 것은 폴백을 위해 남는다 — 워크숍 후 삭제 커밋 대상).

**2. 미확인 항목 두 개는 계획에 검증 단계로 반영했다**
- `setting_sources`의 project 스코프가 워크스페이스 `CLAUDE.md`를 읽는지 → Task 9 §2·§3
- 재시작 후 답변을 모델이 이해하는지 → Task 9 §6

**3. Task 5 Step 7이 유일한 조사 단계다.** SDK의 커스텀 도구 등록 방식(`tool` 데코레이터 / `create_sdk_mcp_server`)을 코드로 확인한 뒤 확정한다 — 문서에서 유추해 쓰면 틀릴 위험이 있고, 실제 설치된 `claude-agent-sdk==0.2.126`이 진실이다.
