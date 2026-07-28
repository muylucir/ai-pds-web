# 프로토타입 완전 초기화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로토타입 하나가 남긴 상태 7곳을 한 번에 지우되 스펙 문서는 남겨, 같은 스펙으로 재빌드할 수 있게 한다.

**Architecture:** `DELETE /projects/{pid}/prototypes/{slug}`가 (1) 라이브 세션·호스팅을 자동 정리하고 (2) 각 소유자 모듈의 `purge()`에 삭제를 위임한다. 라우트는 순서와 실패 수집만 담당하고 S3 키 규약을 알지 않는다. 삭제는 idempotent이며 실패 시 502로 재시도를 유도한다.

**Tech Stack:** FastAPI, pytest (asyncio), boto3 S3 (`S3Store`), 프론트는 Next.js App Router + vitest.

## Global Constraints

- 스펙 문서 `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`는 **절대 지우지 않는다**. 목록이 이것을 스캔해 카드를 만들므로(`routes/prototypes.py:112`, `_SPEC_RE`), 지우면 프로토타입이 존재하지 않게 된다.
- `aiplc-docs/discovery/prototype/validation-results.md`(`survey/store.py`의 `RESULTS_MD_KEY`)는 **절대 지우지 않는다**. `prototype/`이 단수라 slug가 없고 프로토타입 간 공유된다.
- 삭제 순서: **토큰 인덱스 → 설문 트리 → 나머지 S3 → 로컬 디렉터리**. 앞의 두 단계가 뒤바뀌면 토큰을 알아낼 방법이 사라져 고아 링크가 영구히 남는다. S3가 로컬보다 먼저여야 미완료가 카드에 드러난다.
- 모든 `purge()`는 **idempotent**다. 없는 키를 지우는 것은 성공이다.
- `S3Store`에 단일 키 `delete`는 없다. `delete_prefix(key)`에 전체 키를 넘기면 그 키만 매칭되므로 이를 사용한다.
- 새 `purge()`는 각 키를 만든 모듈에 둔다. 라우트에 S3 키 문자열을 새로 쓰지 않는다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `backend/pathfinder/survey/store.py` | 설문 키 규약 소유 → `SurveyStore.purge()` | Modify |
| `backend/pathfinder/proto/session.py` | 세션/트랜스크립트 키 소유 → `purge_session_state()` 모듈 함수 | Modify |
| `backend/pathfinder/proto/host.py` | 로컬 빌드 트리 소유 → `ProtoHost.purge()` | Modify |
| `backend/pathfinder/routes/prototypes.py` | 조율: 라이브 정리 → 위임 → 실패 수집 | Modify |
| `backend/tests/test_survey_store_purge.py` | `SurveyStore.purge()` 단위 테스트 | Create |
| `backend/tests/test_proto_host.py` | `ProtoHost.purge()` 테스트 추가 | Modify |
| `backend/tests/test_proto_session.py` | `purge_session_state()` 테스트 추가 | Modify |
| `backend/tests/test_routes_prototypes.py` | 라우트 통합 테스트 추가 | Modify |
| `frontend/lib/api/prototypes.ts` | `resetPrototype()` + `response_count` 필드 | Modify |
| `frontend/components/prototypes/PrototypeCard.tsx` | 초기화 버튼 | Modify |
| `frontend/app/projects/[projectId]/prototypes/page.tsx` | 확인 토스트 + 핸들러 | Modify |

---

## Task 1: `SurveyStore.purge()` — 토큰 역수집 후 설문 트리 삭제

**Files:**
- Modify: `backend/pathfinder/survey/store.py`
- Test: `backend/tests/test_survey_store_purge.py` (Create)

**Interfaces:**
- Consumes: `SurveyStore.__init__(project_s3, root_s3, slug, project_id)` (기존), 모듈 함수 `survey_prefix(slug)`, `questionnaire_key(slug)`, `archive_prefix(slug, closed_at)`, `questionnaire_md_key(slug)`, 상수 `TOKEN_INDEX_PREFIX` (모두 기존)
- Produces: `async SurveyStore.purge() -> None` — 예외를 던지지 않고 삭제하며, 없는 키는 무시한다

- [ ] **Step 1: Write the failing test**

`backend/tests/test_survey_store_purge.py`를 새로 만든다:

```python
# backend/tests/test_survey_store_purge.py — SurveyStore.purge()
from __future__ import annotations

import json

import pytest

from fakes.in_memory_s3 import FakeS3Store
from pathfinder.survey.store import (RESULTS_MD_KEY, SurveyStore,
                                     questionnaire_key, questionnaire_md_key,
                                     survey_prefix)

pytestmark = pytest.mark.asyncio

SLUG = "todo-app"
PID = "proj-1"


def _store() -> tuple[SurveyStore, FakeS3Store, FakeS3Store]:
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    return (SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID),
            project_s3, root_s3)


def _seed_survey(project_s3, root_s3, token: str, *, closed_at=None) -> None:
    """One survey: questionnaire + its token index. `closed_at` puts a COPY
    under archive/ the way archive_current() does on regeneration."""
    qn = {"slug": SLUG, "project_id": PID, "token": token,
          "status": "closed" if closed_at else "open",
          "closed_at": closed_at, "questions": []}
    if closed_at:
        project_s3.blobs[
            f"{survey_prefix(SLUG)}archive/{closed_at}/questionnaire.json"
        ] = json.dumps(qn)
    else:
        project_s3.blobs[questionnaire_key(SLUG)] = json.dumps(qn)
    root_s3.blobs[f"surveys/by-token/{token}.json"] = json.dumps(
        {"project_id": PID, "slug": SLUG})


async def test_purge_removes_the_whole_survey_tree():
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[f"{survey_prefix(SLUG)}responses/r1.json"] = "{}"
    project_s3.blobs[f"{survey_prefix(SLUG)}rollup.json"] = "{}"
    project_s3.blobs[questionnaire_md_key(SLUG)] = "# survey"

    await store.purge()

    assert [k for k in project_s3.blobs if k.startswith(survey_prefix(SLUG))] == []
    assert questionnaire_md_key(SLUG) not in project_s3.blobs


async def test_purge_removes_archived_tokens_too():
    """The regression this guards: archive_current() does NOT delete the token
    index, so a prototype whose survey was regenerated N times has N indexes.
    Deleting only the current one leaves live /survey/{token} links pointing at
    a survey that no longer exists — the respondent sees a broken page."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-old", closed_at="2026-01-01T00:00:00Z")
    _seed_survey(project_s3, root_s3, "tok-current")

    await store.purge()

    assert "surveys/by-token/tok-old.json" not in root_s3.blobs
    assert "surveys/by-token/tok-current.json" not in root_s3.blobs


async def test_purge_keeps_the_shared_results_doc():
    """RESULTS_MD_KEY has no slug in it ("prototype/", singular) — it is shared
    across prototypes. Purging one must not destroy another's findings."""
    store, project_s3, root_s3 = _store()
    _seed_survey(project_s3, root_s3, "tok-current")
    project_s3.blobs[RESULTS_MD_KEY] = "# shared findings"

    await store.purge()

    assert project_s3.blobs[RESULTS_MD_KEY] == "# shared findings"


async def test_purge_is_idempotent_on_a_prototype_with_no_survey():
    """Most prototypes never get a survey. Purge must be a no-op, not a raise."""
    store, project_s3, root_s3 = _store()

    await store.purge()
    await store.purge()  # twice: the second call has even less to find
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store_purge.py -v`
Expected: 4개 모두 FAIL — `AttributeError: 'SurveyStore' object has no attribute 'purge'`

- [ ] **Step 3: Write minimal implementation**

`backend/pathfinder/survey/store.py`의 `SurveyStore` 클래스에 추가한다. `archive_current` 메서드 바로 아래에 둔다(같은 아카이브 규약을 다루므로):

```python
    async def purge(self) -> None:
        """Delete this prototype's entire survey: the per-slug tree, the
        questionnaire markdown copy, and EVERY token index that ever pointed
        here.

        Token order is load-bearing. `surveys/by-token/` is root-scoped (a
        one-way token -> prototype index with no reverse lookup), so the only
        way to learn this prototype's tokens is to read them back out of the
        questionnaires. Deleting the tree first would strand those indexes
        permanently -- a live /survey/{token} link resolving to a survey that
        no longer exists.

        `archive_current` does not remove the index when it files a survey
        away, so a prototype whose survey was regenerated N times has N of
        them; collect from the archive as well as the live questionnaire.

        Idempotent and non-raising: a prototype that never had a survey is the
        common case, and a partially-purged one must converge on a retry.
        """
        for token in await self._collect_tokens():
            await self._root.delete_prefix(f"{TOKEN_INDEX_PREFIX}{token}.json")
        await self._s3.delete_prefix(survey_prefix(self.slug))
        # Outside the survey/ tree: the viewer copy under aiplc-docs/.
        # RESULTS_MD_KEY is deliberately NOT touched -- it has no slug in it
        # and is shared across prototypes.
        await self._s3.delete_prefix(questionnaire_md_key(self.slug))

    async def _collect_tokens(self) -> set[str]:
        """Every token this prototype has issued, live and archived."""
        keys = [questionnaire_key(self.slug)]
        keys += [k for k in await self._s3.list(f"{survey_prefix(self.slug)}archive/")
                 if k.endswith("/questionnaire.json")]
        tokens: set[str] = set()
        for key in keys:
            try:
                raw = await self._s3.get(key)
            except FileNotFoundError:
                continue  # no survey, or an archive entry without a definition
            try:
                token = json.loads(raw).get("token")
            except json.JSONDecodeError:
                _log.warning("unparseable questionnaire, token not reclaimed: %s", key)
                continue
            if token:
                tokens.add(token)
        return tokens
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store_purge.py -v`
Expected: 4 passed

- [ ] **Step 5: Confirm the archived-token test is load-bearing**

`purge()`에서 `_collect_tokens()` 호출을 임시로 `{}`(빈 집합)로 바꿔 실행한다:

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store_purge.py -v`
Expected: `test_purge_removes_archived_tokens_too`와 `test_purge_removes_the_whole_survey_tree` 중 토큰을 보는 것이 FAIL. 확인 후 되돌린다.

- [ ] **Step 6: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/survey/store.py backend/tests/test_survey_store_purge.py
git commit -m "feat(survey): SurveyStore.purge() — 아카이브 토큰까지 회수 후 설문 트리 삭제"
```

---

## Task 2: `purge_session_state()` — 세션·트랜스크립트·레거시 번들 삭제

**Files:**
- Modify: `backend/pathfinder/proto/session.py`
- Test: `backend/tests/test_proto_session.py`

**Interfaces:**
- Consumes: `S3StoreLike` (`get`/`put`/`list`/`delete_prefix`)
- Produces: 모듈 함수 `async purge_session_state(s3, slug: str) -> None`

**모듈 함수여야 하는 이유:** 빌드가 끝나면 세션은 `proto_sessions`에서 회수된다(정상 상태). 인스턴스 메서드로 두면 그 프로토타입은 초기화할 수 없다.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_proto_session.py` 끝에 추가한다:

```python
# ---- purge_session_state ----

async def test_purge_session_state_removes_session_transcript_and_bundle():
    """Everything under prototypes/{slug}/ that this module owns. The bundle/
    prefix is legacy (the deleted MicroVM wrote it) but old projects still
    carry one, so purge has to cover it."""
    from pathfinder.proto.session import purge_session_state
    s3 = FakeS3Store()
    s3.blobs[f"prototypes/{SLUG}/session.json"] = '{"session_id": "x"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/00000001.jsonl"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    # Must survive: a different prototype's state.
    s3.blobs["prototypes/other/session.json"] = '{"session_id": "y"}'

    await purge_session_state(s3, SLUG)

    assert [k for k in s3.blobs if k.startswith(f"prototypes/{SLUG}/")] == []
    assert "prototypes/other/session.json" in s3.blobs


async def test_purge_session_state_leaves_the_spec_alone():
    """The spec lives under aiplc-docs/, not prototypes/{slug}/ — but assert it
    explicitly: deleting it would remove the card from the list entirely
    (routes/prototypes.py scans specs to build the list), turning a reset into
    a disappearance."""
    from pathfinder.proto.session import purge_session_state
    s3 = FakeS3Store()
    spec = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
    s3.blobs[spec] = "# PROTOTYPE"

    await purge_session_state(s3, SLUG)

    assert s3.blobs[spec] == "# PROTOTYPE"


async def test_purge_session_state_is_idempotent():
    from pathfinder.proto.session import purge_session_state
    s3 = FakeS3Store()
    await purge_session_state(s3, SLUG)
    await purge_session_state(s3, SLUG)
```

`FakeS3Store`(`tests/test_proto_session.py:16`)와 `SLUG = "todo-app"`(`:18`)은 이미
이 파일에 있으므로 import를 추가할 필요가 없다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k purge -v`
Expected: FAIL — `ImportError: cannot import name 'purge_session_state'`

- [ ] **Step 3: Write minimal implementation**

`backend/pathfinder/proto/session.py`의 `PrototypeSession` 클래스 **밖**, 파일 끝에 추가한다:

```python
async def purge_session_state(s3, slug: str) -> None:
    """Delete the S3 state this module owns for one prototype: the durable
    session id, the build transcript, and the legacy bundle/ backup.

    A module function, not a method: once a build finishes the session is
    evicted from `proto_sessions` (the normal resting state), so anything
    hanging off an instance could not reach the very prototypes that most need
    resetting.

    Scoped to `prototypes/{slug}/` and therefore never touches the spec, which
    lives under aiplc-docs/ -- deleting that would remove the card from the
    list instead of resetting it. Idempotent: absent keys are a no-op.
    """
    await s3.delete_prefix(f"prototypes/{slug}/")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_session.py -k purge -v`
Expected: 3 passed

- [ ] **Step 5: Verify the survey tree overlap is intentional**

`prototypes/{slug}/`는 `prototypes/{slug}/survey/`를 **포함**한다. Task 1이 이미 설문을 지우므로 중복이지만, 순서상 설문이 먼저이고 이 호출은 남은 것을 정리한다. 중복 삭제는 idempotent라 안전하다 — 다만 Task 1을 건너뛰고 이것만 실행하면 **토큰 인덱스가 고아가 된다**. 이 위험을 함수 docstring에 남긴다:

```python
    Callers MUST run SurveyStore.purge() BEFORE this: the survey tree lives
    under this same prefix, and reclaiming its token indexes requires reading
    the questionnaires that this call would delete.
```

위 docstring 문단을 `purge_session_state`의 docstring 끝에 추가한다.

- [ ] **Step 6: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/proto/session.py backend/tests/test_proto_session.py
git commit -m "feat(proto): purge_session_state() — 세션·트랜스크립트·레거시 번들 삭제"
```

---

## Task 3: `ProtoHost.purge()` — 로컬 빌드 트리 삭제

**Files:**
- Modify: `backend/pathfinder/proto/host.py`
- Test: `backend/tests/test_proto_host.py`

**Interfaces:**
- Consumes: `ProtoHost.__init__(root, port_range)`, `ProtoHost.stop(pid, slug)` (기존)
- Produces: `async ProtoHost.purge(pid: str, slug: str) -> None`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_proto_host.py` 끝에 추가한다:

```python
# ---- purge ----

async def test_purge_removes_the_build_tree(root):
    _seed_build_dir(root)
    (root / PID / SLUG / "prototype").mkdir(parents=True)
    (root / PID / SLUG / "prototype" / "package.json").write_text("{}",
                                                                 encoding="utf-8")
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.purge(PID, SLUG)

    assert not (root / PID / SLUG).exists()


async def test_purge_leaves_other_prototypes_alone(root):
    _seed_build_dir(root, slug="keep-me")
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))

    await host.purge(PID, SLUG)

    assert not (root / PID / SLUG).exists()
    assert (root / PID / "keep-me").is_dir()


async def test_purge_stops_a_running_process_first(root):
    """Deleting the tree out from under a live `npm start` would leave an
    orphan process holding the port. purge stops it first."""
    _seed_build_dir(root)
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    info = await host.start(PID, SLUG)
    assert info.state == "running", info.log_tail

    await host.purge(PID, SLUG)

    assert host.status(PID, SLUG) is None
    assert not (root / PID / SLUG).exists()


async def test_purge_is_idempotent(root):
    host = ProtoHost(root=root, port_range=range(4001, 4010))
    await host.purge(PID, SLUG)
    await host.purge(PID, SLUG)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_host.py -k purge -v`
Expected: FAIL — `AttributeError: 'ProtoHost' object has no attribute 'purge'`

- [ ] **Step 3: Write minimal implementation**

`backend/pathfinder/proto/host.py`의 `ProtoHost`에 `stop` 메서드 아래에 추가한다. 파일 상단 import에 `import shutil`을 추가한다:

```python
    async def purge(self, pid: str, slug: str) -> None:
        """Stop this prototype and delete its local build tree.

        `stop` first, deliberately: removing the directory under a live
        `npm start` would orphan the process, which keeps holding its port (the
        registry entry is what `stop` needs to signal the process group).

        Idempotent -- a tree that was never built, or was already purged, is a
        no-op. `shutil.rmtree` runs in a thread: it is synchronous and a
        node_modules tree is large enough to stall the event loop.
        """
        await self.stop(pid, slug)
        target = self._root / pid / slug
        if not target.is_dir():
            return
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_host.py -k purge -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole host suite for regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/test_proto_host.py -q`
Expected: 전체 passed (기존 테스트 + 신규 4)

- [ ] **Step 6: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/proto/host.py backend/tests/test_proto_host.py
git commit -m "feat(proto): ProtoHost.purge() — 프로세스 정지 후 로컬 빌드 트리 삭제"
```

---

## Task 4: `DELETE /projects/{pid}/prototypes/{slug}` 라우트

**Files:**
- Modify: `backend/pathfinder/routes/prototypes.py`
- Test: `backend/tests/test_routes_prototypes.py`

**Interfaces:**
- Consumes: Task 1의 `SurveyStore.purge()`, Task 2의 `purge_session_state(s3, slug)`, Task 3의 `ProtoHost.purge(pid, slug)`, 기존 `app_module.survey_store_factory(pid, slug)`, `app_module.s3_store_factory(pid)`, `app_module.proto_host()`, `app_module.proto_sessions`
- Produces: `DELETE /projects/{pid}/prototypes/{slug}` → 204 (성공) / 404 (미등록 프로젝트) / 502 (부분 실패)

- [ ] **Step 1: Write the failing test**

`backend/tests/test_routes_prototypes.py`의 `# ---- hosting ----` 섹션 **앞**에 추가한다:

```python
# ---- reset ----

def _seed_everything(proto_env, monkeypatch):
    """All seven places one prototype leaves state, plus a sibling prototype
    and the shared results doc that must both survive."""
    s3 = proto_env["s3"]
    _seed_spec(s3)
    s3.blobs[f"prototypes/{SLUG}/session.json"] = '{"session_id": "x"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/00000001.jsonl"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = json.dumps(
        {"slug": SLUG, "project_id": PID, "token": "tok-1", "status": "open",
         "closed_at": None, "questions": []})
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = "{}"
    s3.blobs[f"aiplc-docs/discovery/prototypes/{SLUG}/validation-questionnaire.md"] = "# q"
    s3.blobs["aiplc-docs/discovery/prototype/validation-results.md"] = "# shared"
    s3.blobs["prototypes/other/session.json"] = '{"session_id": "y"}'
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")


def test_reset_clears_everything_but_keeps_the_spec(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)
    s3 = proto_env["s3"]

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert [k for k in s3.blobs if k.startswith(f"prototypes/{SLUG}/")] == []
    assert f"aiplc-docs/discovery/prototypes/{SLUG}/validation-questionnaire.md" \
        not in s3.blobs
    assert not (proto_env["root"] / PID / SLUG).exists()
    # Survivors: the spec (or the card disappears), the shared results doc
    # (no slug in its key), and any other prototype.
    assert s3.blobs[SPEC_KEY] == "# PROTOTYPE demo"
    assert s3.blobs["aiplc-docs/discovery/prototype/validation-results.md"] == "# shared"
    assert s3.blobs["prototypes/other/session.json"] == '{"session_id": "y"}'


def test_reset_leaves_the_card_listable_as_none(proto_env, monkeypatch):
    """The point of keeping the spec: the card comes back as a fresh, buildable
    prototype rather than vanishing."""
    _seed_everything(proto_env, monkeypatch)

    client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"] == [{"slug": SLUG, "spec_path": SPEC_KEY,
                                   "state": "none", "port": None,
                                   "response_count": 0}]


def test_reset_closes_a_live_session_and_frees_its_build_slot(proto_env, monkeypatch):
    """A live session is cleaned up rather than refused -- 'reset' should not
    make the user close things first. close() releases the build semaphore, so
    the slot must come back too."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert app_module.build_semaphore.snapshot()["active_builds"] == 1

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert session.closed
    assert (PID, SLUG) not in app_module.proto_sessions
    assert app_module.build_semaphore.snapshot()["active_builds"] == 0


def test_reset_stops_hosting(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=4001, log_tail="")

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert (PID, SLUG) in proto_env["host"].purged


def test_reset_without_a_session_succeeds(proto_env, monkeypatch):
    """Unlike DELETE .../session (404 when absent), a missing session is the
    NORMAL case here -- a finished build has already been evicted."""
    _seed_spec(proto_env["s3"])

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204


def test_reset_is_idempotent(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)

    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204
    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204


def test_reset_502_when_a_purge_fails_and_keeps_local_state(proto_env, monkeypatch):
    """S3 before local, so a failure leaves the card reading 'built' -- the
    incomplete reset stays visible. Wiping local first would flip the card to
    'none' and tell the user it finished while S3 still held the survey."""
    _seed_everything(proto_env, monkeypatch)

    async def boom(self):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(
        "pathfinder.survey.store.SurveyStore.purge", boom, raising=True)

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    assert (proto_env["root"] / PID / SLUG).is_dir()


def test_reset_unknown_project_404(proto_env):
    assert client.delete("/projects/nope/prototypes/demo").status_code == 404
```

`FakeProtoHost`에 `purge`를 추가한다(`start`/`stop`과 같은 자리):

```python
    async def purge(self, pid, slug):
        self.purged.append((pid, slug))
        self.infos.pop((pid, slug), None)
```

그리고 `__init__`에 기록용 리스트를 추가한다:

```python
        self.purged: list[tuple[str, str]] = []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -k reset -v`
Expected: 전부 FAIL — `405 Method Not Allowed` (라우트 없음). `test_reset_leaves_the_card_listable_as_none`은 `response_count` 때문에도 실패한다(Task 5에서 추가).

- [ ] **Step 3: Write minimal implementation**

`backend/pathfinder/routes/prototypes.py`의 `close_session` 아래, `# ---- handoff archive ----` **앞**에 추가한다:

```python
@router.delete("/projects/{pid}/prototypes/{slug}", status_code=204)
async def reset_prototype(pid: str, slug: str):
    """Wipe everything this prototype has accumulated EXCEPT its spec.

    Keeping the spec is what makes this a reset rather than a deletion: the
    list is built by scanning specs, so the card comes back as a fresh
    buildable prototype instead of disappearing.

    Live session and hosting are cleaned up rather than refused -- the point of
    one button is that the user does not have to close things first. Unlike
    `close_session`, a missing session is the normal case (a finished build has
    already been evicted), so absence is not a 404.

    Order matters twice over. SurveyStore.purge() runs FIRST because reclaiming
    its token indexes means reading questionnaires that the session purge would
    delete (they share the prototypes/{slug}/ prefix). And all S3 work precedes
    the local tree: wiping local first flips the card to "none", telling the
    user it finished while S3 may still hold the survey. This way a failure
    leaves the card reading "built" and the incomplete reset stays visible.

    Failures are collected, not raised on the spot: S3 has no transaction, so
    partial deletion is a legitimate state. Every purge is idempotent, so the
    502 this returns is an invitation to press the button again.
    """
    import pathfinder.app as app_module
    _require_registered(pid)

    failures: list[str] = []

    session = app_module.proto_sessions.pop((pid, slug), None)
    if session is not None:
        try:
            await session.close()
        except Exception:
            _log.exception("reset: session close failed: %s/%s", pid, slug)
            failures.append("session")

    for label, work in (
            ("survey", app_module.survey_store_factory(pid, slug).purge()),
            ("session-state", purge_session_state(
                app_module.s3_store_factory(pid), slug)),
            ("build-tree", app_module.proto_host().purge(pid, slug)),
    ):
        try:
            await work
        except Exception:
            _log.exception("reset: %s purge failed: %s/%s", label, pid, slug)
            failures.append(label)

    if failures:
        raise HTTPException(
            status_code=502,
            detail=f"초기화가 완료되지 않았습니다({', '.join(failures)}) — 다시 시도해 주세요")
    return Response(status_code=204)
```

파일 상단 import부에 추가한다:

```python
from pathfinder.proto.session import purge_session_state
```

**주의:** 이 import가 순환을 만들면(`proto/session.py`가 라우트를 import하지 않으므로 만들지 않아야 한다) 함수 안으로 옮긴다. Step 4에서 확인된다.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -k reset -v`
Expected: `test_reset_leaves_the_card_listable_as_none`만 FAIL(`response_count` 미구현 — Task 5), 나머지 7개 passed

- [ ] **Step 5: Verify the S3-before-local order is load-bearing**

`for` 루프에서 `("build-tree", ...)` 항목을 `("survey", ...)` 앞으로 옮긴 뒤 실행한다:

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py::test_reset_502_when_a_purge_fails_and_keeps_local_state -v`
Expected: FAIL — 로컬 트리가 이미 지워져 `is_dir()`가 False. 확인 후 되돌린다.

- [ ] **Step 6: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/routes/prototypes.py backend/tests/test_routes_prototypes.py
git commit -m "feat(proto): DELETE .../prototypes/{slug} — 스펙만 남기는 완전 초기화"
```

---

## Task 5: 목록에 `response_count` 노출

**Files:**
- Modify: `backend/pathfinder/routes/prototypes.py:143-144` (목록 응답 dict)
- Test: `backend/tests/test_routes_prototypes.py`

**Interfaces:**
- Consumes: `app_module.s3_store_factory(pid).list(prefix)`, `pathfinder.survey.store.responses_prefix(slug)`
- Produces: 목록 각 항목에 `response_count: int` 추가

**왜 필요한가:** 확인 토스트가 "응답 12건이 삭제됩니다"를 보여주려면 갯수를 알아야 한다. 버튼을 누른 뒤 별도 요청을 하면 토스트가 늦게 뜨므로, 목록에 실어 버튼 시점에 이미 알고 있게 한다.

- [ ] **Step 1: Write the failing test**

`test_routes_prototypes.py`의 listing 섹션(다른 `test_list_*` 옆)에 추가한다:

```python
def test_list_reports_survey_response_count(proto_env):
    """The reset confirmation needs the count at button-press time, so it rides
    the list rather than costing an extra request."""
    _seed_spec(proto_env["s3"])
    for name in ("r1", "r2", "r3"):
        proto_env["s3"].blobs[
            f"prototypes/{SLUG}/survey/responses/{name}.json"] = "{}"

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["response_count"] == 3


def test_list_reports_zero_responses_when_there_is_no_survey(proto_env):
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["response_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -k response_count -v`
Expected: FAIL — `KeyError: 'response_count'`

- [ ] **Step 3: Write minimal implementation**

`routes/prototypes.py`의 `list_prototypes` 루프에서 `out.append(...)` 직전에 추가한다:

```python
        # Rides the list so the reset confirmation can name the number of
        # answers about to be destroyed without a second round trip.
        from pathfinder.survey.store import responses_prefix
        response_count = len(await s3.list(responses_prefix(slug)))
```

그리고 `out.append`를 고친다:

```python
        out.append({"slug": slug, "spec_path": spec_path,
                    "state": state, "port": port,
                    "response_count": response_count})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_prototypes.py -q`
Expected: 전체 passed (Task 4의 `test_reset_leaves_the_card_listable_as_none`도 이제 통과)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: 전체 passed. 기존 목록 테스트가 dict 전체를 비교하면(`test_list_state_none`처럼) `response_count`를 추가해야 한다 — 실패하면 그 단정에 `"response_count": 0`을 넣는다.

- [ ] **Step 6: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add backend/pathfinder/routes/prototypes.py backend/tests/test_routes_prototypes.py
git commit -m "feat(proto): 목록에 response_count — 초기화 확인에 응답 갯수 표시"
```

---

## Task 6: 프론트 — API 클라이언트

**Files:**
- Modify: `frontend/lib/api/prototypes.ts`
- Test: `frontend/lib/api/prototypes.test.ts`

**Interfaces:**
- Consumes: 이 파일의 내부 헬퍼 `request<T>(path, init?)`와 `sessionPath(pid, slug, suffix = "")` (둘 다 기존, `prototypes.ts:56`). `sessionPath`를 suffix 없이 호출하면 `/projects/{pid}/prototypes/{slug}`가 되어 이 태스크가 필요한 경로 그대로다 — 새 경로 문자열을 쓰지 않는다.
- Produces: `resetPrototype(pid: string, slug: string): Promise<void>`, `PrototypeInfo.response_count: number`

- [ ] **Step 1: Write the failing test**

`frontend/lib/api/prototypes.test.ts`에 추가한다. 이 파일의 기존 MSW 패턴(다른 테스트가 `server.use(http.delete(...))`를 쓰는 방식)을 그대로 따른다:

```typescript
it("resetPrototype sends DELETE to the prototype resource", async () => {
  let seen: string | null = null;
  server.use(
    http.delete("*/projects/:pid/prototypes/:slug", ({ request }) => {
      seen = new URL(request.url).pathname;
      return new HttpResponse(null, { status: 204 });
    }),
  );

  await resetPrototype("proj-1", "todo-app");

  expect(seen).toContain("/projects/proj-1/prototypes/todo-app");
});

it("resetPrototype surfaces a 502 as an ApiError so the UI can retry", async () => {
  server.use(
    http.delete("*/projects/:pid/prototypes/:slug", () =>
      HttpResponse.json({ detail: "초기화가 완료되지 않았습니다" }, { status: 502 }),
    ),
  );

  await expect(resetPrototype("proj-1", "todo-app")).rejects.toThrow(ApiError);
});
```

import에 `resetPrototype`과 `ApiError`를 추가한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/prototypes.test.ts`
Expected: FAIL — `resetPrototype is not exported`

- [ ] **Step 3: Write minimal implementation**

`frontend/lib/api/prototypes.ts`의 `stopHost` 바로 아래에 추가한다 — 같은 `request` + `sessionPath` 형태를 따른다:

```typescript
// Wipes the prototype's build, session, transcript and survey — everything but
// the spec, so the card returns as a fresh buildable prototype. A 502 means a
// partial reset; every purge is idempotent, so retrying converges.
export async function resetPrototype(pid: string, slug: string): Promise<void> {
  await request<void>(sessionPath(pid, slug), { method: "DELETE" });
}
```

`PrototypeInfo` 타입에 필드를 추가한다:

```typescript
  /** Survey answers that a reset would destroy — shown in its confirmation. */
  response_count: number;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/prototypes.test.ts && npx tsc --noEmit`
Expected: passed, 타입 에러 없음. `response_count`가 필수 필드가 되어 기존 테스트 픽스처가 깨지면 그 픽스처에 `response_count: 0`을 추가한다.

- [ ] **Step 5: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/lib/api/prototypes.ts frontend/lib/api/prototypes.test.ts
git commit -m "feat(proto): resetPrototype API 클라이언트 + response_count 타입"
```

---

## Task 7: 프론트 — 카드 버튼과 확인 토스트

**Files:**
- Modify: `frontend/components/prototypes/PrototypeCard.tsx`
- Modify: `frontend/app/projects/[projectId]/prototypes/page.tsx`
- Test: `frontend/components/prototypes/PrototypeCard.test.tsx`

**Interfaces:**
- Consumes: Task 6의 `resetPrototype`, `PrototypeInfo.response_count`
- Produces: `PrototypeCard`에 `onReset?: (slug: string) => void` prop

- [ ] **Step 1: Write the failing test**

`frontend/components/prototypes/PrototypeCard.test.tsx`에 추가한다. 기존 테스트의 렌더 헬퍼와 `info` 픽스처 형태를 따른다:

```typescript
it("offers reset once a prototype has been built", async () => {
  const onReset = vi.fn();
  render(
    <PrototypeCard
      info={{ slug: "todo-app", spec_path: "s.md", state: "built", port: null,
              response_count: 0 }}
      onReset={onReset}
      {...noopHandlers}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: /초기화/ }));

  expect(onReset).toHaveBeenCalledWith("todo-app");
});

it("does not offer reset for a prototype with nothing to reset", () => {
  render(
    <PrototypeCard
      info={{ slug: "todo-app", spec_path: "s.md", state: "none", port: null,
              response_count: 0 }}
      onReset={vi.fn()}
      {...noopHandlers}
    />,
  );

  expect(screen.queryByRole("button", { name: /초기화/ })).toBeNull();
});
```

`noopHandlers`는 이 파일에 이미 있는 형태를 쓴다. 없으면 기존 테스트가 넘기는 prop들을 그대로 나열한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/prototypes/PrototypeCard.test.tsx`
Expected: FAIL — 초기화 버튼이 없음

- [ ] **Step 3: Write minimal implementation**

`PrototypeCard.tsx`의 props에 추가한다:

```typescript
  /** Wipe build + session + survey, keeping the spec. Absent for state "none"
   *  — there is nothing accumulated to clear. */
  onReset?: (slug: string) => void;
```

그리고 액션 영역(`state === "failed"` 블록 뒤)에 추가한다:

```tsx
{info.state !== "none" && onReset && (
  <button
    type="button"
    onClick={() => onReset(info.slug)}
    className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-rose-50 text-sm font-medium text-rose-600"
  >
    초기화
  </button>
)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/prototypes/PrototypeCard.test.tsx`
Expected: passed

- [ ] **Step 5: Wire the page handler with confirmation**

`page.tsx`의 `handleStopHost` 아래에 추가한다:

```typescript
  async function handleReset(slug: string) {
    const info = list.data?.prototypes.find((p) => p.slug === slug);
    const answers = info?.response_count ?? 0;
    // Name what is destroyed. The survey line only appears when there is
    // something irreversible to lose, so the routine case stays quiet.
    const lines = [
      `'${slug}' 프로토타입을 초기화합니다.`,
      "",
      "· 빌드 결과와 실행 중인 서버",
      "· 빌드 대화 기록",
      answers > 0
        ? `· 검증 설문과 응답 ${answers}건 (되돌릴 수 없습니다)`
        : "· 검증 설문",
      "",
      "설계 문서(PROTOTYPE-*.md)는 남으므로 다시 빌드할 수 있습니다.",
    ];
    if (!window.confirm(lines.join("\n"))) return;

    setBusySlug(slug);
    try {
      await resetPrototype(projectId, slug);
    } catch {
      window.alert("초기화가 완료되지 않았습니다. 다시 시도해 주세요.");
    } finally {
      setBusySlug(null);
      list.reload();
    }
  }
```

import에 `resetPrototype`을 추가하고, `<PrototypeCard>`에 `onReset={handleReset}`을 넘긴다.

**`list.reload()`가 `finally`에 있는 이유:** 부분 실패(502)에서도 지워진 것은 반영해야 한다. 502 후 카드가 여전히 `built`면 그것이 미완료 신호다.

- [ ] **Step 6: Verify the page still builds and tests pass**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: 전체 passed, 타입 에러 없음

- [ ] **Step 7: Commit**

```bash
cd /home/ec2-user/project/pathfinder-sp
git add frontend/components/prototypes/PrototypeCard.tsx \
        frontend/components/prototypes/PrototypeCard.test.tsx \
        "frontend/app/projects/[projectId]/prototypes/page.tsx"
git commit -m "feat(proto): 카드 초기화 버튼 + 삭제 항목 확인"
```

---

## Task 8: 전체 검증

- [ ] **Step 1: Backend suite**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: 전체 passed

- [ ] **Step 2: Frontend suite + types**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: 전체 passed, 타입 에러 없음

- [ ] **Step 3: Verify the orphan-token guard end to end**

설문을 2회 생성해 아카이브가 생긴 상태를 직접 만들고 초기화한 뒤, `surveys/by-token/`에 아무것도 남지 않는지 확인하는 통합 테스트가 Task 1에 있다. 그것이 실제로 load-bearing인지 다시 확인한다:

Run: `cd backend && .venv/bin/python -m pytest tests/test_survey_store_purge.py::test_purge_removes_archived_tokens_too -v`
Expected: PASS. `_collect_tokens`에서 archive 스캔 줄을 지우면 FAIL해야 한다.

- [ ] **Step 4: 미검증 사항을 커밋 메시지에 남긴다**

실제 S3와 실제 프로토타입에서는 검증되지 않는다. 다음을 커밋 메시지 또는 후속 이슈에 남긴다:

- 실제 S3의 `delete_prefix` 페이지네이션(1000개 초과 `node_modules` 트리)
- `shutil.rmtree`가 실행 중 프로세스의 열린 파일 핸들과 겹칠 때의 동작
- 배포 후 실제 프로토타입에서 초기화 → 재빌드가 `basePath`를 반영하는지(`2c3fe03`의 미검증 사항과 연결된다)

---

## Self-Review

**스펙 커버리지:**

| 스펙 요구사항 | 구현 태스크 |
|---|---|
| #1 설문 트리 삭제 | Task 1 |
| #2 토큰 인덱스 N개 삭제 (아카이브 포함) | Task 1 (`_collect_tokens`) |
| #3 `validation-questionnaire.md` 삭제 | Task 1 |
| #4 `session.json` 삭제 | Task 2 |
| #5 `transcript/` 삭제 | Task 2 |
| #6 `bundle/` 삭제 | Task 2 |
| #7 로컬 빌드 트리 삭제 | Task 3 |
| 스펙 보존 | Task 2 Step 1 테스트, Task 4 테스트 |
| `validation-results.md` 보존 | Task 1 테스트, Task 4 테스트 |
| 라이브 자동 정리 | Task 4 (세션 close, host purge) |
| 세마포어 반납 | Task 4 테스트 |
| 세션 없어도 성공 | Task 4 테스트 |
| idempotent | Task 1·2·3·4 각 테스트 |
| S3 먼저, 로컬 나중 | Task 4 Step 5 (되돌려 확인) |
| 실패 수집 후 502 | Task 4 |
| 토큰 → 트리 순서 | Task 1 docstring, Task 2 Step 5 docstring |
| UI 확인 + 응답 갯수 | Task 5·6·7 |
| 성공 후 목록 갱신 | Task 7 |

**타입 일관성:** `purge_session_state(s3, slug)`는 Task 2에서 정의되고 Task 4에서 같은 시그니처로 호출된다. `ProtoHost.purge(pid, slug)`, `SurveyStore.purge()`도 동일. `response_count`는 Task 5(백엔드)와 Task 6(타입)·7(사용)에서 같은 이름이다.

**미해결 위험:** Task 2의 `purge_session_state`가 `prototypes/{slug}/`를 통째로 지우므로 설문 트리와 겹친다. 순서(Task 1 먼저)가 지켜지지 않으면 토큰이 고아가 된다. 이것을 코드 주석으로만 막고 있어 — 호출 순서를 강제하는 구조적 장치는 없다. 라우트가 유일한 호출자이므로 현재는 충분하지만, 다른 호출자가 생기면 위험하다.
