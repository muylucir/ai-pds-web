# 히스토리 로딩 표시 · 턴 중단 버튼 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워크스페이스 히스토리 로딩 중 스켈레톤을 보여주고, 진행 중인 턴을 두 채팅 화면에서 같은 버튼으로 중단할 수 있게 한다.

**Architecture:** 로딩 표시는 이미 있는 `historyLoading` 상태를 소비하는 새 컴포넌트 하나로 끝난다. 중단은 세 겹 — `ChatInput`의 전송 버튼 자리를 ■로 전환(두 화면 공유), Discovery에 `interrupt()` + 라우트 신설(프로토타입은 이미 있음), 중단 사실을 status 이벤트로 흘려 말풍선 아래 한 줄로 렌더.

**Tech Stack:** Next.js 15 (App Router) · React · Vitest + Testing Library · FastAPI · pytest

## Global Constraints

- 한국어 UI 문구. 주석·커밋 메시지도 이 저장소의 기존 관례를 따른다(주석은 왜를 설명, 무엇을 반복하지 않는다).
- 프론트 테스트는 `npx vitest run <path>`, 백엔드는 `.venv/bin/python -m pytest <path> -q` (작업 디렉토리: 각각 `frontend/`, `backend/`).
- `ChatInput`은 워크스페이스와 프로토타입 빌드 패널이 **공유**한다. 이 파일을 고치면 두 화면에 함께 적용된다.
- 중단은 **멱등**이다. 진행 중인 턴이 없어도 에러가 아니다(202/no-op).
- 중단해도 **지금까지 만들어진 파일과 나온 텍스트는 살린다.** 롤백하지 않는다.
- Discovery의 pending 질문은 인메모리 + **S3 양쪽**에 있다(`agent/pending_store.py`). 중단 시 둘 다 지운다.

---

### Task 1: 히스토리 로딩 스켈레톤

**Files:**
- Create: `frontend/components/canvas/HistorySkeleton.tsx`
- Create: `frontend/components/canvas/HistorySkeleton.test.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.test.tsx`

**Interfaces:**
- Consumes: `historyLoading: boolean` — `useWorkspaceStream()`이 이미 반환한다(`lib/useWorkspaceStream.ts:305`).
- Produces: `HistorySkeleton` — prop 없는 컴포넌트. `role="status"` + `aria-label="이전 대화를 불러오는 중"`.

- [ ] **Step 1: 컴포넌트 테스트를 먼저 쓴다**

`frontend/components/canvas/HistorySkeleton.test.tsx`:

```tsx
// frontend/components/canvas/HistorySkeleton.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HistorySkeleton } from "./HistorySkeleton";

describe("HistorySkeleton", () => {
  it("스크린리더에 무엇을 기다리는지 알린다", () => {
    // 화면에는 형태(회색 박스)로, 스크린리더에는 문구로 같은 것을 전달한다.
    // aria-label 없이 펄스 박스만 두면 스크린리더 사용자에게는 빈 화면과 구별되지 않는다.
    render(<HistorySkeleton />);
    expect(screen.getByRole("status")).toHaveAccessibleName("이전 대화를 불러오는 중");
  });

  it("말풍선이 여러 개인 것처럼 보인다", () => {
    // 하나만 두면 "메시지 하나가 오는 중"으로 읽힌다 — 복원 중임을 예감하게
    // 하려면 여러 줄이어야 한다.
    render(<HistorySkeleton />);
    expect(screen.getAllByTestId("skeleton-line")).toHaveLength(3);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/HistorySkeleton.test.tsx`
Expected: FAIL — `Failed to resolve import "./HistorySkeleton"`

- [ ] **Step 3: 컴포넌트를 만든다**

`frontend/components/canvas/HistorySkeleton.tsx`:

```tsx
// frontend/components/canvas/HistorySkeleton.tsx
//
// 히스토리 복원 중 채팅 자리를 채우는 자리표시자. 종전에는 GET /history가 도는
// 동안 이 영역이 빈 화면이어서, 대화가 많은 프로젝트를 다시 열면 "내 대화가
// 사라졌다"로 읽혔다.
//
// AiMessage와 같은 아바타·폭을 쓰는 것이 의도적이다 — 로드가 끝나 실제 항목이
// 들어올 때 레이아웃이 튀지 않는다.
export function HistorySkeleton() {
  // 폭을 다르게 둔다: 같은 길이 박스 세 개는 로딩 바처럼 보이고, 들쭉날쭉하면
  // 대화처럼 보인다.
  const widths = ["w-3/5", "w-4/5", "w-2/5"];
  return (
    <div role="status" aria-label="이전 대화를 불러오는 중" className="space-y-4">
      {widths.map((w, i) => (
        <div key={i} className="flex gap-3">
          <span
            className="shrink-0 w-8 h-8 rounded-lg bg-slate-200 animate-pulse"
            aria-hidden="true"
          />
          <div
            data-testid="skeleton-line"
            className={`h-16 ${w} max-w-[85%] rounded-2xl rounded-tl-md bg-slate-100 animate-pulse`}
          />
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/HistorySkeleton.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: 워크스페이스에 배선하는 테스트를 쓴다**

`frontend/app/projects/[projectId]/workspace/page.test.tsx`의 `describe` 블록 안에 추가:

```tsx
  it("히스토리를 불러오는 동안 스켈레톤을 보여준다", () => {
    // 종전에는 이 구간이 빈 화면이었다. historyLoading은 이미 있었지만 웰컴
    // 카드를 가리는 데만 쓰였다(page.tsx의 showWelcome).
    mockWorkspaceStream({ items: [], historyLoading: true, pendingQuestions: null, streaming: false });
    render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
    expect(screen.getByRole("status", { name: "이전 대화를 불러오는 중" })).toBeInTheDocument();
  });

  it("로딩이 끝나면 스켈레톤이 사라진다", () => {
    mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false });
    render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
    expect(screen.queryByRole("status", { name: "이전 대화를 불러오는 중" })).not.toBeInTheDocument();
  });
```

- [ ] **Step 6: 실패를 확인한다**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/workspace/page.test.tsx" -t 스켈레톤`
Expected: FAIL — `Unable to find role="status"`

- [ ] **Step 7: 페이지에 배선한다**

`workspace/page.tsx`에서 `HistorySkeleton`을 import하고, 채팅 항목을 렌더하는 자리에 조건부로 넣는다. 기존 `showWelcome` 계산식(`page.tsx:37`)은 **손대지 않는다** — 웰컴 카드와 스켈레톤이 동시에 뜨지 않는 것을 그 조건이 이미 보장한다(`!historyLoading`).

채팅 항목 목록 바로 앞에:

```tsx
{historyLoading && <HistorySkeleton />}
```

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/workspace/page.test.tsx"`
Expected: PASS (기존 테스트 전부 + 신규 2건)

- [ ] **Step 9: 커밋**

```bash
git add frontend/components/canvas/HistorySkeleton.tsx \
        frontend/components/canvas/HistorySkeleton.test.tsx \
        "frontend/app/projects/[projectId]/workspace/page.tsx" \
        "frontend/app/projects/[projectId]/workspace/page.test.tsx"
git commit -m "feat(chat): 히스토리 복원 중 스켈레톤 표시

나갔다 들어오면 GET /history가 도는 동안 채팅 영역이 빈 화면이었다.
historyLoading 상태는 이미 있었지만 웰컴 카드를 가리는 데만 쓰였다."
```

---

### Task 2: ChatInput 중단 버튼

**Files:**
- Modify: `frontend/components/canvas/ChatInput.tsx`
- Modify: `frontend/components/canvas/ChatInput.test.tsx`

**Interfaces:**
- Consumes: 없음(순수 컴포넌트).
- Produces: `ChatInput`에 두 prop 추가 — `onInterrupt?: () => void`, `interrupting?: boolean`. 호출부는 `interrupting={streaming}`을 넘긴다. `interrupting`이 참이고 `onInterrupt`가 있으면 전송 버튼 자리에 ■이 뜨고 클릭 시 `onInterrupt()`가 불린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/components/canvas/ChatInput.test.tsx`의 `describe("ChatInput", ...)` 안에 추가:

```tsx
  it("진행 중이면 전송 버튼 자리가 중단 버튼이 된다", async () => {
    const user = userEvent.setup();
    const onInterrupt = vi.fn();
    render(
      <ChatInput onSend={vi.fn()} disabled={true} interrupting={true} onInterrupt={onInterrupt} />,
    );
    // 같은 자리를 쓴다 — 스트리밍 중에는 어차피 비활성이던 버튼이라 레이아웃이
    // 변하지 않는다.
    expect(screen.queryByRole("button", { name: "전송" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "중단" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("입력이 막혀 있어도 진행 중이 아니면 중단 버튼을 띄우지 않는다", () => {
    // 프로토타입 패널은 disabled={streaming || buildComplete !== null}이다
    // (BuildPanel.tsx). disabled로 판단하면 빌드가 끝난 뒤에도 ■이 떠서
    // 중단할 턴이 없는데 중단 버튼이 있는 상태가 된다.
    render(
      <ChatInput onSend={vi.fn()} disabled={true} interrupting={false} onInterrupt={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "전송" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "중단" })).not.toBeInTheDocument();
  });

  it("onInterrupt가 없으면 종전과 같이 동작한다", () => {
    // 이 컴포넌트를 쓰는 다른 화면이 생겼을 때 중단 버튼이 저절로 나타나지
    // 않아야 한다.
    render(<ChatInput onSend={vi.fn()} disabled={true} interrupting={true} />);
    expect(screen.getByRole("button", { name: "전송" })).toBeInTheDocument();
  });
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/ChatInput.test.tsx -t 중단`
Expected: FAIL — `Unable to find role="button" and name "중단"`

- [ ] **Step 3: 컴포넌트를 고친다**

`ChatInput.tsx`의 prop 시그니처에 두 개를 더한다:

```tsx
export function ChatInput({
  onSend,
  disabled,
  onAttach,
  initialText,
  onInterrupt,
  interrupting,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  onAttach?: (file: File) => void;
  // 마운트 시 1회 프리필 + 포커스(예: 리뷰 화면의 수정 요청 링크에서 넘어온 초안).
  initialText?: string;
  // 진행 중인 턴을 끊는다. 이 두 값은 짝이다 — 핸들러가 없으면 버튼을 띄우지
  // 않는다(이 컴포넌트를 쓰는 다른 화면에 저절로 생기지 않게).
  onInterrupt?: () => void;
  // "중단할 턴이 있는가". `disabled`("입력을 막는가")와 다르다: 프로토타입
  // 패널은 빌드가 끝난 뒤에도 disabled가 참이므로(BuildPanel.tsx의
  // `streaming || buildComplete !== null`) 그 값으로 판단하면 중단할 것이
  // 없는데 ■이 뜬다.
  interrupting?: boolean;
}) {
```

전송 버튼을 조건부로 바꾼다(기존 `<button ... aria-label="전송">↑</button>` 자리):

```tsx
          {interrupting && onInterrupt ? (
            <button
              type="button"
              onClick={onInterrupt}
              className="shrink-0 w-8 h-8 rounded-lg bg-slate-700 hover:bg-slate-800 text-white flex items-center justify-center"
              aria-label="중단"
            >
              ■
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || text.trim() === ""}
              className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white flex items-center justify-center"
              aria-label="전송"
            >
              ↑
            </button>
          )}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/ChatInput.test.tsx`
Expected: PASS (기존 테스트 전부 + 신규 3건)

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/canvas/ChatInput.tsx frontend/components/canvas/ChatInput.test.tsx
git commit -m "feat(chat): 입력창 전송 버튼 자리에 중단 버튼

스트리밍 중에는 어차피 비활성이던 자리라 레이아웃이 변하지 않는다.
disabled를 재사용하지 않는 이유는 prop 주석에 있다."
```

---

### Task 3: Discovery 드라이버 interrupt()

**Files:**
- Modify: `backend/pathfinder/agent/claude_driver.py`
- Modify: `backend/tests/test_claude_driver.py`

**Interfaces:**
- Consumes: 드라이버에 이미 있는 것들 — `self._client`(`:557`), `self._turn_token`(`:563`), `self._pending_question`(`:564`), `self._clear_pending_state()`(`:789`), `self._clear_pending_quietly()`(`:816`), `self._queue`(`:561`).
- Produces: `async def interrupt(self) -> None`. 진행 중인 턴이 없으면 no-op. 중단 시 `self._queue`에 `AgentEvent(kind="status", text="중단됨")`을 넣는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_claude_driver.py` 끝에 추가. 이 파일의 `_driver` 헬퍼는 3-튜플 `(driver, ws, captured)`을 돌려준다:

```python


# ---- 턴 중단 ----

async def test_interrupt_clears_the_pending_question_from_s3(tmp_path):
    """중단은 S3의 pending 레코드까지 지워야 한다.

    Discovery의 pending은 인메모리와 S3 양쪽에 있다(agent/pending_store.py).
    인메모리만 지우면 `GET /pending`이 답할 수 없는 질문을 복원한다 — 사용자가
    폼을 채우고 제출했는데 아무 일도 일어나지 않는다. 그 future는 중단과 함께
    버려졌기 때문이다. 프로토타입 빌더가 같은 정리를 하는 이유이고
    (proto/builder.py의 interrupt), Discovery는 durable 사본이 하나 더 있다.
    """
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert "questions" in kinds, kinds
    assert PENDING_KEY in s3.blobs, "전제: 질문이 S3에 저장돼 있다"

    await d.interrupt()

    assert PENDING_KEY not in s3.blobs
    assert d._pending_payload is None
    assert d._pending_iid is None


async def test_interrupt_without_a_live_turn_is_a_no_op(tmp_path):
    """멱등이어야 한다. 이미 끝난 턴에 대한 중단 요청은 에러가 아니고, 라우트가
    세션 유무만 보고 이 메서드를 부른다."""
    d, _, _ = _driver(tmp_path, {})
    await d.interrupt()   # 아무 턴도 돌지 않은 상태
    await d.interrupt()   # 두 번 불러도 같다


async def test_interrupt_records_that_the_turn_was_stopped(tmp_path):
    """중단 사실이 이벤트로 흘러야 화면과 트랜스크립트에 남는다.

    표시가 없으면 스크롤백을 나중에 볼 때 에이전트가 말을 마치지 못한 이유를
    알 수 없다.
    """
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]

    await d.interrupt()

    assert any(e.kind == "status" and e.text == "중단됨" for e in d._queue), d._queue
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py -q -k interrupt`
Expected: FAIL — `AttributeError: 'ClaudeDriver' object has no attribute 'interrupt'`

- [ ] **Step 3: 드라이버에 메서드를 추가한다**

`claude_driver.py`의 `_flush_transcript_mirror` 바로 다음에 넣는다(같은 "턴 수명" 묶음):

```python
    async def interrupt(self) -> None:
        """진행 중인 턴을 끊는다. 지금까지 한 작업은 살린다.

        proto/builder.py의 interrupt를 패턴으로 따르되 한 가지가 다르다 —
        Discovery의 pending 질문은 S3에도 미러링된다(agent/pending_store.py).
        인메모리만 지우면 `GET /pending`이 답할 수 없는 질문을 복원하고,
        사용자가 제출한 답변은 아무도 듣지 않는 future를 resolve한다.

        순서가 load-bearing이다: 우리 상태를 먼저 정리하고 마지막에 클라이언트를
        건드린다. client.interrupt()가 던져도 pending이 남지 않는다.

        멱등: 돌고 있는 턴이 없으면 아무것도 하지 않는다. 라우트는 세션 유무만
        보고 이 메서드를 부르므로 이미 끝난 턴에 대한 요청이 정상적으로 들어온다.
        """
        if self._client is None or self._turn_token is None:
            return
        if self._pending_question is not None and not self._pending_question.done():
            # 이 future를 기다리던 _on_can_use_tool은 턴과 함께 버려진다.
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        # 큐에 남은 questions 이벤트는 답할 수 없는 카드다 — 흘려보내면 화면에
        # 폼이 뜬다.
        self._queue = [e for e in self._queue if e.kind != "questions"]
        # 중단 사실을 남긴다. 이 이벤트가 화면의 "중단됨" 한 줄이 되고,
        # 트랜스크립트에도 들어가 복원 시 재현된다.
        self._queue.append(AgentEvent(kind="status", text="중단됨"))
        await self._client.interrupt()
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_claude_driver.py -q`
Expected: PASS (기존 전부 + 신규 3건)

`test_interrupt_clears_the_pending_question_from_s3`가 `AttributeError: 'AskingSdkClient' object has no attribute 'interrupt'`로 실패하면, 가짜 SDK 클라이언트에 no-op을 추가한다 — `tests/fakes/fake_sdk_asking.py`의 `AskingSdkClient`에:

```python
    async def interrupt(self) -> None:
        """실제 SDK 클라이언트가 갖는 메서드. 가짜에서는 할 일이 없다 —
        드라이버가 자기 상태를 정리했는지가 이 테스트의 관심사다."""
        return None
```

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/agent/claude_driver.py backend/tests/test_claude_driver.py backend/tests/fakes/fake_sdk_asking.py
git commit -m "feat(agent): ClaudeDriver.interrupt() — 진행 중인 턴 중단

프로토타입 빌더에만 있던 것을 Discovery에도 넣는다. 다른 점 하나:
Discovery의 pending은 S3에도 있어서 그것까지 지워야 답할 수 없는 질문이
GET /pending으로 복원되지 않는다."
```

---

### Task 4: Runner 위임 + 중단 라우트

**Files:**
- Modify: `backend/pathfinder/runner.py`
- Modify: `backend/pathfinder/routes/turns.py`
- Modify: `backend/tests/test_routes_turns.py`

**Interfaces:**
- Consumes: Task 3의 `ClaudeDriver.interrupt()`.
- Produces: `AgentRunner.interrupt()` → `await self._driver.interrupt()`. `POST /projects/{pid}/interrupt` → 202 `{"status": "interrupting"}`.

- [ ] **Step 1: 라우트 테스트를 쓴다**

이 파일의 러너 대역은 `ScriptRunner`(`:37`)이고, 워크스페이스는
`_install_default(monkeypatch, pid)`(`:88`)로 심는다 — 그 헬퍼가
`app_module.make_workspace`를 갈아끼우고 프로젝트까지 만든다.

먼저 `ScriptRunner`에 카운터와 메서드를 더한다(`__init__`의 `self._pending_payload`
다음, 그리고 `stop` 앞):

```python
        self.interrupts = 0
```

```python
    async def interrupt(self):
        self.interrupts += 1
```

그리고 심어진 러너를 테스트에서 집을 수 있도록 `_install_default`가 그것을
돌려주게 고친다 — 지금은 반환값이 없다:

```python
def _install_default(monkeypatch, pid):
    """Install a ScriptRunner with its default structured-demo script, so
    send_message arms a pending interrupt that send_answers/pending can then
    be exercised against.

    Returns the runner so a test can assert on what the route did to it
    (the interrupt route has no response body to check).
    """
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    runner = ScriptRunner()

    async def make(project_id):
        return Workspace(runner)

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    return runner
```

기존 호출부는 반환값을 무시하므로 그대로 둔다.

파일 끝에 추가:

```python


# ---- 턴 중단 ----

def test_interrupt_reaches_the_runner(monkeypatch):
    """라우트가 실제로 러너까지 도달하는가. 202만 돌려주고 아무것도 하지 않는
    라우트는 화면에서 구별되지 않는다 — 버튼은 눌리고 턴은 계속 돈다."""
    runner = _install_default(monkeypatch, "int-1")
    r = client.post("/projects/int-1/interrupt")
    assert r.status_code == 202
    assert runner.interrupts == 1


def test_interrupt_is_idempotent(monkeypatch):
    """두 번 눌러도 같다. 사용자가 반응이 없다고 다시 누르는 것이 정상 경로이고,
    돌고 있는 턴이 없을 때도 에러가 아니다."""
    runner = _install_default(monkeypatch, "int-2")
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert runner.interrupts == 2


def test_interrupt_on_an_unknown_project_is_404(monkeypatch):
    """ensure_workspace의 기존 계약 — 없는 프로젝트는 404다. 중단이 멱등인 것과
    별개다(있는 프로젝트의 없는 턴 ≠ 없는 프로젝트)."""
    _install_default(monkeypatch, "int-3")
    assert client.post("/projects/nope/interrupt").status_code == 404
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_turns.py -q -k interrupt`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: Runner에 위임 메서드를 추가한다**

`runner.py`의 `send_answers` 다음에:

```python
    async def interrupt(self) -> None:
        """진행 중인 턴을 끊는다. 드라이버로 위임한다.

        턴 슬롯은 건드리지 않는다 — 돌고 있는 run()이 종결 이벤트를 내며
        스스로 놓는다. 여기서 함께 놓으면 이중 해제가 된다.
        """
        await self._driver.interrupt()
```

- [ ] **Step 4: 라우트를 추가한다**

`routes/turns.py`의 `get_pending` 다음에:

```python
@router.post("/projects/{pid}/interrupt", status_code=202)
async def interrupt_turn(pid: str):
    """진행 중인 턴을 중단한다. 프로토타입 쪽
    (/prototypes/{slug}/interrupt)과 같은 계약이다.

    진행 중인 턴이 없어도 202: 중단은 멱등이고, 사용자가 반응이 없다고 다시
    누르는 것이 정상 경로다. 202(Accepted)인 이유는 실제 중단이 서브프로세스
    왕복이라 이 응답 시점에 끝나 있지 않다는 것 — 결과는 SSE 스트림이 종결
    이벤트로 알린다.
    """
    ws = await ensure_workspace(pid)   # 없는 프로젝트는 404
    await ws.runner.interrupt()
    return {"status": "interrupting"}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_turns.py -q`
Expected: PASS (기존 전부 + 신규 2건)

- [ ] **Step 6: 인증 경계 테스트를 돌린다**

새 라우트가 인증 뒤에 있는지 확인한다 — 이 저장소는 그 경계를 테스트로 고정한다.

Run: `cd backend && .venv/bin/python -m pytest tests/test_auth_route_coverage.py -q`
Expected: PASS. 실패하면 그 테스트가 요구하는 목록에 새 경로를 넣는다(무인증 허용 목록에 넣는 것이 **아니다** — `/interrupt`는 인증이 필요하다).

- [ ] **Step 7: 커밋**

```bash
git add backend/pathfinder/runner.py backend/pathfinder/routes/turns.py backend/tests/test_routes_turns.py
git commit -m "feat(api): POST /projects/{pid}/interrupt

프로토타입 쪽과 같은 계약. 진행 중인 턴이 없어도 202 — 중단은 멱등이고
사용자가 다시 누르는 것이 정상 경로다."
```

---

### Task 5: 프론트 배선 — 워크스페이스 중단

**Files:**
- Modify: `frontend/lib/api/client.ts`
- Modify: `frontend/lib/useWorkspaceStream.ts`
- Modify: `frontend/lib/useTurnStream.ts`
- Modify: `frontend/components/canvas/AiMessage.tsx`
- Modify: `frontend/components/canvas/AiMessage.test.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.tsx`
- Modify: `frontend/app/projects/[projectId]/workspace/page.test.tsx`

**Interfaces:**
- Consumes: Task 4의 `POST /projects/{pid}/interrupt`. Task 2의 `ChatInput` prop `onInterrupt`/`interrupting`.
- Produces: `interruptTurn(pid: string): Promise<void>` (client.ts). `useWorkspaceStream`이 `interrupt: () => Promise<void>`를 추가로 반환. `AiItem`에 `interrupted: boolean` 필드 추가.

- [ ] **Step 1: API 함수를 추가한다**

`frontend/lib/api/client.ts`에 (`postMessage` 근처):

```ts
// POST /projects/{pid}/interrupt → 202. 진행 중인 턴을 끊는다. 응답 시점에
// 중단이 끝나 있지 않다(202) — 스트림이 종결 이벤트로 알린다.
export async function interruptTurn(pid: string): Promise<void> {
  await request<{ status: string }>(`/projects/${encodeURIComponent(pid)}/interrupt`, {
    method: "POST",
  });
}
```

- [ ] **Step 2: AiItem에 필드를 더하고 status를 분기하는 테스트를 쓴다**

`frontend/components/canvas/AiMessage.test.tsx`에 추가. 이 파일의 `base`는
`{ id, role, text, trace, streaming, error }` 형태이므로 새 필드를 함께 넘긴다:

```tsx
  it("중단된 턴은 말풍선 아래에 그 사실을 남긴다", () => {
    // trace의 한 줄로 넣지 않는다 — trace는 도구 실행 기록이고 중단은 턴의
    // 종결 사유다. 접혀 있는 "추론 과정" 안에 두면 왜 말이 끊겼는지 보이지 않는다.
    render(
      <AiMessage
        item={{ ...base, streaming: false, text: "분석하다가", interrupted: true }}
      />,
    );
    expect(screen.getByText("중단됨")).toBeInTheDocument();
  });

  it("정상 종료된 턴에는 중단 표시가 없다", () => {
    render(<AiMessage item={{ ...base, streaming: false, text: "완료" }} />);
    expect(screen.queryByText("중단됨")).not.toBeInTheDocument();
  });
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/AiMessage.test.tsx -t 중단`
Expected: FAIL — `Unable to find an element with the text: 중단됨`

- [ ] **Step 4: 타입과 컴포넌트를 고친다**

`frontend/lib/useTurnStream.ts`의 `AiItem`에 필드를 더한다:

```ts
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
  // 사용자가 이 턴을 끊었다. trace가 아닌 별도 필드인 이유는 성격이 다르기
  // 때문 — trace는 도구 실행 기록, 이것은 턴의 종결 사유다.
  interrupted?: boolean;
}
```

`AiMessage.tsx`에서 `ReasoningTrace` 바로 앞에:

```tsx
        {item.interrupted && (
          <p className="mt-1.5 text-xs text-slate-400">중단됨</p>
        )}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/canvas/AiMessage.test.tsx`
Expected: PASS (기존 전부 + 신규 2건)

- [ ] **Step 6: 훅에 interrupt를 더하는 테스트를 쓴다**

`frontend/app/projects/[projectId]/workspace/page.test.tsx`에 추가:

```tsx
  it("진행 중이면 중단 버튼이 뜨고 누르면 훅의 interrupt를 부른다", async () => {
    const user = userEvent.setup();
    const interrupt = vi.fn();
    mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: true, interrupt });
    render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
    await user.click(screen.getByRole("button", { name: "중단" }));
    expect(interrupt).toHaveBeenCalledTimes(1);
  });
```

`mockWorkspaceStream` 헬퍼의 기본값에 `interrupt: vi.fn()`을 더한다 — 이 파일의 다른 테스트가 그 값을 넘기지 않아도 페이지가 렌더돼야 한다.

- [ ] **Step 7: 실패를 확인한다**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/workspace/page.test.tsx" -t 중단`
Expected: FAIL — `Unable to find role="button" and name "중단"`

- [ ] **Step 8: 훅과 페이지를 배선한다**

`useWorkspaceStream.ts`:

1. import에 `interruptTurn`을 더한다.
2. status 이벤트 처리에 분기를 넣는다 — 기존 `if (ev.kind === "status" || ev.kind === "file_changed")` **앞에**:

```ts
        // 중단은 turn의 종결 사유라 trace가 아니라 전용 필드로 간다.
        // 드라이버가 status로 흘리는 이유는 트랜스크립트에 남기기 위해서다
        // (claude_driver.interrupt).
        if (ev.kind === "status" && ev.text === "중단됨") {
          return { ...it, interrupted: true };
        }
```

3. 콜백을 만들고 반환에 더한다:

```ts
  const interrupt = useCallback(async () => {
    // 실패를 삼킨다: 중단은 보조 동작이고, 실패해도 턴은 그대로 돌아 화면이
    // 막히지 않는다. 사용자는 다시 누를 수 있다.
    try {
      await interruptTurn(projectId);
    } catch {
      /* 무시 */
    }
  }, [projectId]);
```

`WorkspaceStream` 타입에 `interrupt: () => Promise<void>;`를 더하고 반환 객체에 `interrupt`를 넣는다.

4. `workspace/page.tsx`에서 훅 구조분해에 `interrupt`를 더하고 `ChatInput`에 넘긴다:

```tsx
<ChatInput ... onInterrupt={() => void interrupt()} interrupting={streaming} />
```

- [ ] **Step 9: 통과를 확인한다**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/workspace/page.test.tsx" components/canvas/`
Expected: PASS

- [ ] **Step 10: 커밋**

```bash
git add frontend/lib/api/client.ts frontend/lib/useWorkspaceStream.ts frontend/lib/useTurnStream.ts \
        frontend/components/canvas/AiMessage.tsx frontend/components/canvas/AiMessage.test.tsx \
        "frontend/app/projects/[projectId]/workspace/page.tsx" \
        "frontend/app/projects/[projectId]/workspace/page.test.tsx"
git commit -m "feat(chat): 워크스페이스 턴 중단 배선

중단 사실은 trace가 아니라 전용 필드로 간다 — trace는 도구 실행 기록이고
중단은 턴의 종결 사유라, 접힌 아코디언 안에 두면 왜 끊겼는지 보이지 않는다."
```

---

### Task 6: 프로토타입 패널 중단 버튼 통일

**Files:**
- Modify: `frontend/components/prototypes/BuildPanel.tsx`
- Modify: `frontend/components/prototypes/BuildPanel.test.tsx`

**Interfaces:**
- Consumes: Task 2의 `ChatInput` prop. `usePrototypeStream`의 `interrupt`(이미 있다 — `lib/usePrototypeStream.ts:51`).
- Produces: 없음(UI 통일).

- [ ] **Step 1: 테스트를 더한다**

이 파일에는 이미 중단 버튼 테스트 2건이 있다(`BuildPanel.test.tsx:80`, `:92`) —
`getByRole("button", { name: "중단" })`으로 찾으므로 버튼이 헤더에서 입력창으로
옮겨가도 **그대로 통과한다.** 두 테스트는 손대지 않는다.

새로 고정할 것은 "버튼이 하나뿐"이라는 것이다. 이 파일의 헬퍼는 `mockStream`이고
패널은 `render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />)`로
띄운다. `:92` 테스트 다음에 추가:

```tsx
  it("중단 버튼은 하나뿐이다 — 입력창 자리로 통일", async () => {
    // 종전에는 헤더에 있었다. 입력창에 더하면서 지우지 않으면 같은 기능 버튼이
    // 한 화면에 둘이 되고, 진행자가 어느 것을 말하는지 매번 짚어야 한다.
    const interrupt = vi.fn().mockResolvedValue(undefined);
    mockStream({ streaming: true, interrupt });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);

    expect(screen.getAllByRole("button", { name: "중단" })).toHaveLength(1);
  });

  it("빌드가 끝난 뒤에는 중단 버튼이 없다", () => {
    // 입력창의 disabled는 `streaming || buildComplete !== null`이라 완료
    // 후에도 참이다. 그 값으로 버튼을 판단하면 중단할 턴이 없는데 ■이 뜬다 —
    // ChatInput에 interrupting을 따로 넘기는 이유다.
    mockStream({
      streaming: false,
      buildComplete: { summary: "할 일 앱", remaining: "" },
    });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "중단" })).not.toBeInTheDocument();
  });
```

두 번째 테스트의 `buildComplete` 형태는 이 파일의 기존 완료-카드 테스트가 쓰는
값을 그대로 따른다 — 다르면 `grep -n "buildComplete" components/prototypes/BuildPanel.test.tsx`로
확인해 맞춘다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run components/prototypes/BuildPanel.test.tsx -t 중단`
Expected: 첫 신규 테스트가 FAIL — 헤더 버튼과 입력창 버튼이 둘이라 `toHaveLength(1)`이 2를 받는다. (Step 3에서 입력창에 넘기기 전이라면 1로 통과할 수 있다 — 그 경우 Step 3의 두 변경을 함께 적용한 뒤 이 단계를 다시 돌려 2 → 1이 되는 것을 확인한다.)

- [ ] **Step 3: 헤더 버튼을 제거하고 ChatInput에 넘긴다**

`BuildPanel.tsx`의 헤더에서 이 블록을 삭제한다(`:120-128` 근처):

```tsx
            {streaming && (
              <button
                type="button"
                onClick={() => void interrupt()}
                className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 text-sm font-medium text-slate-700"
              >
                중단
              </button>
            )}
```

`ChatInput` 호출(`:168`)을 고친다:

```tsx
            <ChatInput
              onSend={send}
              disabled={streaming || buildComplete !== null}
              onInterrupt={() => void interrupt()}
              interrupting={streaming}
            />
```

`interrupting={streaming}`이 요점이다 — `disabled`를 그대로 쓰면 빌드가 끝난 뒤에도(`buildComplete !== null`) ■이 뜬다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run components/prototypes/BuildPanel.test.tsx`
Expected: PASS (기존 전부 + 신규 1건)

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/prototypes/BuildPanel.tsx frontend/components/prototypes/BuildPanel.test.tsx
git commit -m "refactor(proto): 중단 버튼을 입력창으로 통일

헤더와 입력창에 같은 기능 버튼이 둘이던 것을 하나로. 워크스페이스와 같은
자리·같은 모양이 된다."
```

---

### Task 7: 실 CLI 검증 + 전체 스위트

**Files:**
- Create: `/tmp/probe_interrupt.py` (커밋하지 않는다)

**Interfaces:**
- Consumes: Task 1–6 전부.
- Produces: 없음(검증).

- [ ] **Step 1: 프로브를 쓴다**

단위 테스트는 가짜 SDK를 쓰므로 실제 CLI가 중단되는지는 확인하지 못한다. `/tmp/probe_interrupt.py`:

```python
# 실 CLI로 중단이 턴을 끊는지 + 그 시점까지의 트랜스크립트가 S3에 남는지.
import asyncio, os, sys
sys.path.insert(0, '/home/ec2-user/project/pathfinder-sp/backend')
sys.path.insert(0, '/home/ec2-user/project/pathfinder-sp/backend/tests')
from pathlib import Path
from fakes.in_memory_s3 import FakeS3Store
from pathfinder.agent.claude_driver import ClaudeDriver

CFG, WS, RULES = "/tmp/pi/cfg", "/tmp/pi/ws", "/tmp/pi/rules"

async def main():
    for p in (CFG, WS, f"{RULES}/aws-aiplc-rules"):
        Path(p).mkdir(parents=True, exist_ok=True)
    Path(f"{RULES}/aws-aiplc-rules/core-workflow.md").write_text("WORKFLOW")
    os.environ.setdefault("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")

    s3 = FakeS3Store()
    d = ClaudeDriver(workspace=WS, rules_dir=RULES, config_dir=CFG, s3=s3,
                     anthropic_model=os.environ["ANTHROPIC_MODEL"])

    kinds = []
    async def drive():
        async for ev in d.run("1부터 30까지 세면서 각 숫자마다 한 문장씩 써라.",
                              {"session_id": "pi-1"}):
            kinds.append(ev.kind)

    task = asyncio.create_task(drive())
    await asyncio.sleep(8)          # 턴이 확실히 돌기 시작한 뒤
    await d.interrupt()
    await asyncio.wait_for(task, timeout=60)

    print("events:", kinds)
    print("중단됨 status:", any(e.kind == "status" and e.text == "중단됨" for e in d._queue))
    mirrored = [k for k in s3.blobs if k.startswith("discovery/transcript/")]
    print("mirrored objects:", len(mirrored))

asyncio.run(main())
```

- [ ] **Step 2: 프로브를 돌린다**

```bash
cd backend && rm -rf /tmp/pi && timeout 300 .venv/bin/python /tmp/probe_interrupt.py
```

기대: 턴이 `interrupt()` 후 **60초 안에 끝난다**(중단이 실제로 동작). 끊기지 않으면 `asyncio.wait_for`가 `TimeoutError`를 던지므로 실패가 명확하다. `mirrored objects`가 1 이상이어야 한다 — 중단된 턴의 대화도 복원 대상이다.

- [ ] **Step 3: 전체 스위트를 돌린다**

```bash
cd backend && timeout 900 .venv/bin/python -m pytest -q
cd ../frontend && timeout 900 npm test
```

기대: 둘 다 전부 통과. 백엔드는 신규 5건(Task 3의 3건 + Task 4의 2건), 프론트는 신규 8건(Task 1의 4건 + Task 2의 3건 + Task 5의 3건 + Task 6의 1건 = 11건이지만 일부는 기존 테스트 수정).

- [ ] **Step 4: 문서를 갱신한다**

새 라우트와 UI가 생겼으므로 문서를 맞춘다:

- `README.md` — 워크스페이스 중단이 가능해진 것을 인트로의 빌드 세션 서술 근처에 한 줄. 새 라우트(`POST /projects/{pid}/interrupt`)를 언급한다.
- `docs/workshop/facilitator.html` — "화면이 멈춘 것 같다" 항목에 "이제 입력창의 ■로 중단할 수 있다"를 더한다. 진행자가 알아야 하는 새 수단이다.
- `docs/workshop/intro.html` — §6(빌드는 대화입니다)의 "마음에 안 들면 중단할 수도 있습니다"가 이제 워크스페이스에도 해당한다 — 해당 문장 위치를 확인해 반영한다.

- [ ] **Step 5: 커밋**

```bash
git add README.md docs/workshop/facilitator.html docs/workshop/intro.html
git commit -m "docs: 워크스페이스 턴 중단 반영

진행자 자료의 '화면이 멈춘 것 같다' 항목에 새 수단을 더한다 —
종전에는 기다리는 것 외에 방법이 없었다."
```

---

## Self-Review

**Spec coverage**

| 스펙 항목 | 구현 Task |
|---|---|
기능 1 — 스켈레톤 컴포넌트 + 배선 | Task 1 |
기능 2 — `ChatInput` ■ 전환, `interrupting` 별도 prop | Task 2 |
기능 2 — `ClaudeDriver.interrupt()` + S3 pending 삭제 | Task 3 |
기능 2 — `AgentRunner.interrupt()` + `POST /interrupt` | Task 4 |
기능 2 — "중단됨" 표시(전용 필드) + 프론트 배선 | Task 5 |
기능 2 — 프로토타입 헤더 버튼 제거 | Task 6 |
테스트 표의 실 CLI 프로브 | Task 7 |
하지 않는 것(롤백·프로토타입 백엔드·확인 모달) | 해당 Task 없음 — 의도적 |

**Type consistency**

- `interrupt()`: 드라이버(Task 3) → 러너(Task 4) → 라우트(Task 4) → `interruptTurn`(Task 5) → 훅 `interrupt`(Task 5) → `onInterrupt` prop(Task 2). 이름이 일관된다.
- `interrupting` prop은 Task 2에서 정의하고 Task 5(워크스페이스)·Task 6(프로토타입)이 소비한다. 둘 다 `streaming`을 넘긴다.
- `AiItem.interrupted`는 Task 5에서 타입·렌더·이벤트 분기를 함께 다룬다.
- status 문구 `"중단됨"`이 백엔드(Task 3)와 프론트 분기(Task 5)에서 같은 문자열이다. 이 결합은 의도적이며 Task 5의 주석이 근거를 남긴다.
