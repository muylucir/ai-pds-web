# Pathfinder Frontend C2 — Structured Timeline Cards + Switchable Document/Preview Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **DEPENDS ON:** `2026-07-17-pathfinder-frontend-c-canvas-build.md` (Plan C1), merged. C1 delivers the 3-pane canvas shell (`app/projects/[projectId]/canvas/page.tsx`), the live SSE chat consumer (`lib/useTurnStream.ts`, wrapping Plan A's `lib/api/sse.ts` `streamEvents`), the basic `ChatItem`/`TraceEntry`/`UserItem`/`AiItem` view-state types, the presentational `ChatTimeline`/`UserMessage`/`AiMessage`/`ReasoningTrace`/`ChatInput`/`CanvasSidebar`/`PreviewPanel` components, and the deferred `previewUrl` seam (`lib/api/preview.ts`). This plan (C2) enriches C1's timeline with structured cards and replaces C1's placeholder-only right pane with a switchable Document/Preview panel — **without changing C1's `useTurnStream`/`streamEvents` contract** (only extending the `ChatItem` union it produces).

**Goal:** Port the remaining structured-card and switchable-panel pieces of `files/ui/04-conversational-canvas.html` onto C1's shell — materializing question-answer summary cards, a clarification card with option buttons, and an artifact card into the chat timeline, and giving the right pane a controlled 「문서」/「프리뷰」 tab toggle that renders the Living Document (via the existing `MarkdownView`) alongside C1's existing preview pane.

**Architecture:** `useTurnStream`'s `onDone` (C1) scans the just-finished turn's accumulated `trace` for `file_changed` paths and appends zero or more new card-kind `ChatItem`s (pure filename-suffix matching — no methodology); a new `QuestionCardSlot` container fetches the referenced question file (`getQuestionFile`, Plan A) and renders it by data shape into a presentational summary/clarification/link card; a new `CanvasRightPanel` replaces C1's direct `PreviewPanel` usage in the page, wrapping it and a new `DocumentView` behind a controlled tab toggle. Every action — clicking a clarification option, approving/revising the document, opening an artifact card — routes through the single existing `send()` from `useTurnStream`; nothing here adds a new fetch/SSE call site outside `lib/api/client.ts`/`lib/api/sse.ts`.

**Tech Stack:** Same as C1 — Next.js 15 App Router, React 19, TypeScript 5.7, Tailwind 3.4, Vitest 3 + RTL 16 + jsdom + MSW 2, Playwright (integration only). **No new libraries.** `react-markdown`/`remark-gfm` (already present, used by Plan B's `MarkdownView`) are reused as-is for the Document tab.

> **PROMINENT DEVIATION FROM C1'S DEFERRED-SCOPE SKETCH:** C1's "Deferred to Plan C2" section sketched a structured **approval-gate timeline card** (violet gradient, ✓ 승인 / ✏️ 수정 요청, ported from the mockup's `<aside>`-adjacent gate widget). **This plan does NOT build that card.** Rationale: the backend exposes no structured gate/approval signal — no field on `ProjectState`, no `AgentEvent` kind, nothing in `TurnResult` that says "a gate is open, here is what it is waiting on." The mockup's gate card is triggered by the *human designer* reading document content; building it in the frontend today would mean inferring "a gate is pending" from message prose (regex/keyword sniffing) or from the mere existence of `discovery-document.md`, which is exactly the kind of methodology logic every prior plan (A/B/C1) explicitly forbids the frontend from computing. Instead, approval UX is relocated to the **right panel's Document tab**: a always-available 「✓ 이 문서 승인」 button + an 「✏️ 수정 요請」 textarea (mirroring Plan B's already-shipped `ApprovalGate`/`review` page pattern), which simply calls `send("승인")` / `send(text)` through the SAME turn pipe C1 built — the backend agent, not the frontend, decides whether that message actually satisfies a gate. This is re-deferred as backend contract **#4** below, alongside C1's three.

**Global Constraints:** (carried verbatim from C1, which carried them from Plan A/B)
- **No methodology logic in the frontend.** Stage names/notes come only from `GET /state`; chat content comes only from streamed `AgentEvent` frames. The sidebar's `progressPercent`/`stageCounts` (Plan B) are presentational math only — they count `completed` stages, they do not know stage order or meaning. No contradiction, approval, or question logic is computed here.
- **`lib/api/client.ts` owns every HTTP fetch/URL; `lib/api/sse.ts` owns SSE.** The canvas adds **no new fetch call sites** outside the client. The one exception the slice requires — the prototype preview URL — is isolated behind a **single typed helper** (`lib/api/preview.ts` `previewUrl(...)`), not scattered string-building; it defaults to a safe disabled state (see "Deferred backend contracts").
- **Types come from `lib/api/types.ts` (Plan A)** — `AgentEvent` (`kind`/`text`/`path`, snake_case-agnostic), `TurnResult`, `ProjectState`/`StageState`, `QuestionFile`/`Question`/`QuestionOption`. This plan imports them and does not redefine them. The chat-timeline UI item types (`ChatItem`/`TraceEntry`, plus C2's new card variants) are **UI view-state**, not a backend contract, so they stay defined locally in `lib/useTurnStream.ts`.
- **Korean UI copy from mockup 04 is the source of truth** for user-facing static chrome (sidebar labels, input placeholder, audit note, preview placeholder, card copy). Dynamic content renders from backend/agent data.
- **Graceful error/empty states**, including the **deferred-build preview placeholder**: a transport/`error`-kind frame terminates the turn with a Korean error line in the AI bubble; a `404`/`500` on `GET /state` renders a Korean sidebar error; an absent preview URL renders the "프로토타입 빌드 대기 중" placeholder. Typed `ApiError` from the client. No blank panes, no unhandled throws.
- **Carry Plan A's `useAsync` stale-data-on-reload awareness** (it keeps previous `data` while a new fetch is in flight) and **the `await act(async () => render())` Suspense-test pattern** (Plan B) for the App-Router page whose test `params` is a plain `Promise.resolve(...)`.
- Auth remains the Plan A `getAuthToken()` placeholder; no new auth here.
- **(C2 addition) Card materialization is pure filename mapping, not content sniffing.** `useTurnStream` decides "this turn produced a question file" / "this turn produced the document" by checking the SUFFIX of `file_changed` paths only (`-questions.md`, `discovery-document.md`) — the exact same class of zero-methodology string check Plan B's `isClarification` (`questions/page.tsx`) already established for `-clarification-questions.md`. It never reads file contents to decide whether to show a card.
- **(C2 addition) No part-tab UI.** The mockup's right-panel "Part 1/2/3/4" tabs assume a backend that segments the document into parts; `GET /projects/{pid}/document` returns one markdown blob. C2 renders the whole blob and does not fabricate parts.

**Deferred backend contracts** (C1's three, carried forward, plus this plan's #4):

*ASSUMED to exist today (used now, all verified in the backend):*
- `POST /projects/{pid}/message` (`routes/turns.py`) — synchronous turn; not used by the live SSE path but part of the same relay.
- `GET /projects/{pid}/events?text=...` (`routes/turns.py`, SSE via `EventSourceResponse`) — the live-turn transport consumed through `streamEvents`. Frames are JSON `AgentEvent` `{kind,text,path}` with `kind ∈ {message,file_changed,status,done,error}` (`sandbox/base.py`).
- `GET /projects/{pid}/state` (`routes/artifacts.py`) → `ProjectState` — feeds the sidebar.
- `GET /projects/{pid}/questions/{name}` (`routes/artifacts.py`) → `QuestionFile` — feeds `QuestionCardSlot`.
- `GET /projects/{pid}/document` (`routes/artifacts.py`) → `{markdown}` — feeds `DocumentView`.

*DEFERRED — do NOT invent; this slice degrades gracefully without them:*
1. **Prototype preview URL** — no backend route returns a running prototype's preview URL. `previewUrl(projectId, prototypeId?)` (C1) returns `null` by default → the Preview tab shows the "프로토타입 빌드 대기 중" placeholder. Unchanged from C1.
2. **Build status / build-log endpoint** — no dedicated build-progress endpoint beyond the generic `/message` + `/events` SSE relay. Build/agent progress surfaces only via that stream. Unchanged from C1.
3. **Prototype list / `prototypeId`** — no endpoint lists prototypes or returns a `prototypeId`. The seam accepts an optional caller-supplied/placeholder id and never fabricates one. Unchanged from C1.
4. **(NEW) Structured approval-gate / build-status signal** — there is no field, event kind, or endpoint that tells the frontend "a gate is open" or "here is what must be approved." The mockup's structured gate card (violet gradient, checklist of gate criteria, ✓ 승인 / ✏️ 수정 요청 / 문서 먼저 검토 buttons) is **NOT built** in this plan (see the prominent deviation note above) because building it would require the frontend to infer gate state from message prose or artifact existence — forbidden methodology logic. This is the blocker for a future "structured turn/gate" backend plan that would add e.g. a `gate: {open: bool, title: str, checklist: [...]}`-shaped field to `TurnResult` or `ProjectState`. Until then, approval flows through the Document tab's always-visible 승인/수정 요청 controls, which simply relay natural-language turns.

---

## File Structure

```
frontend/
  app/projects/[projectId]/
    canvas/page.tsx               # MODIFIED: panelTab state, artifact→document wiring, ChatTimeline new props
  components/canvas/
    ChatTimeline.tsx              # MODIFIED: {items; projectId; onChoose; onOpenArtifact; busy} — routes card items
    QuestionSummaryCard.tsx       # NEW presentational: collapsed green "제출됨" summary + expand
    ClarificationCard.tsx         # NEW presentational: amber contradiction/clarification card, option buttons
    ArtifactCard.tsx              # NEW presentational: 📕 button card → opens right panel Document tab
    QuestionCardSlot.tsx          # NEW container: fetches QuestionFile, renders by data shape
    DocumentView.tsx              # NEW: useAsync(getDocument) + MarkdownView + approve/revise row
    CanvasRightPanel.tsx          # NEW: controlled 문서/프리뷰 tablist wrapping DocumentView + PreviewPanelBody
    PreviewPanel.tsx              # MODIFIED: split into exported PreviewPanelBody (inner) + PreviewPanel (aside wrapper, unchanged behavior/tests)
  lib/
    useTurnStream.ts              # MODIFIED: ChatItem union gains QuestionsCardItem/ArtifactCardItem; onDone derivation
  test/fixtures/
    agentEventStreams.ts          # MODIFIED: adds questionsTurn, documentTurn fixtures
```

Rationale: card components stay presentational and prop-driven (unit-tested against fixtures), matching C1's established pattern. `QuestionCardSlot` is the ONLY new data-fetching container, isolating the one piece of real async logic (loading a question file by path) the same way C1 isolated SSE consumption in `useTurnStream`. `CanvasRightPanel` is a thin controlled wrapper so the page keeps owning `panelTab` state (matching C1's page-owns-state style) while `DocumentView`/`PreviewPanel` stay independently testable. `PreviewPanel.tsx` is untouched — C2 nests it inside the new panel rather than duplicating its placeholder/iframe logic, honoring "no new fetch call sites."

---

### Task 1: `ChatItem` card variants + `onDone` derivation in `useTurnStream` + new fixture turns

**Files:**
- Modify: `frontend/lib/useTurnStream.ts`
- Modify: `frontend/test/fixtures/agentEventStreams.ts` (add `questionsTurn`, `documentTurn`)
- Modify: `frontend/lib/useTurnStream.test.tsx` (add a new `describe` block)

**Interfaces:**
- `ChatItem` union grows: `export type ChatItem = UserItem | AiItem | CardItem;` where `CardItem = QuestionsCardItem | ArtifactCardItem`:
  - `QuestionsCardItem = { id: string; role: "card"; card: "questions"; path: string }`
  - `ArtifactCardItem = { id: string; role: "card"; card: "artifact"; path: string }`
- `useTurnStream`'s `onDone` handler (unchanged signature/return type — `{ items; streaming; send }`) now also derives cards: it tracks the `file_changed` paths seen during the in-flight turn in a local array (not React state — read synchronously at `onDone`, independent of batching), and on `done` calls a pure helper `deriveCardsFromPaths(paths: string[]): CardItem[]` that maps each **unique** path to a card by suffix — `-questions.md` → `QuestionsCardItem`; `discovery-document.md` → `ArtifactCardItem`; anything else → no card (e.g. prototype source files touched during a build turn). Appends any derived cards to `items` after the AI bubble.
- `agentEventStreams.ts` gains two more typed `AgentEvent[]` fixtures:
  - `questionsTurn` — `status` → `file_changed` (`aiplc-docs/discovery/product-strategy/strategy-questions.md`) → `message` → `done`.
  - `documentTurn` — `status` → `file_changed` (`aiplc-docs/discovery/discovery-document.md`) → `message` → `done`.
  (Paths match the real pilot1 workspace tree under `files/pilot1/aiplc-docs/discovery/`.)

- [ ] **Step 1: Write the failing test**

```ts
// ADD to frontend/test/fixtures/agentEventStreams.ts (append at the end of the file)

// C2: turns whose file_changed path should materialize a structured timeline
// card in useTurnStream's onDone derivation (see deriveCardsFromPaths).
export const questionsTurn: AgentEvent[] = [
  { kind: "status", text: "Product Strategy 질문지를 생성하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "aiplc-docs/discovery/product-strategy/strategy-questions.md" },
  { kind: "message", text: "포지셔닝·차별화·비즈니스 모델에 관한 13개 질문을 준비했습니다.", path: null },
  { kind: "done", text: null, path: null },
];

export const documentTurn: AgentEvent[] = [
  { kind: "status", text: "Discovery Document를 갱신하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "aiplc-docs/discovery/discovery-document.md" },
  { kind: "message", text: "PR/FAQ를 Discovery Document에 작성했습니다.", path: null },
  { kind: "done", text: null, path: null },
];
```

```tsx
// ADD to frontend/lib/useTurnStream.test.tsx (append a new describe block; also
// extend the existing fixture import line to pull in questionsTurn/documentTurn)
//
// CHANGE the existing import line:
//   import { normalTurn, errorTurn } from "@/test/fixtures/agentEventStreams";
// to:
//   import { normalTurn, errorTurn, questionsTurn, documentTurn } from "@/test/fixtures/agentEventStreams";

const cards = (items: ReturnType<typeof useTurnStream>["items"]) =>
  items.filter((i) => i.role === "card");

describe("useTurnStream — structured timeline cards (C2)", () => {
  it("appends a QuestionsCardItem when a turn's file_changed path ends in -questions.md", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("Product Strategy 질문 만들어줘"));
    const es = FakeEventSource.last!;
    for (const frame of questionsTurn) act(() => es.emit(frame));

    const found = cards(result.current.items);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({
      role: "card",
      card: "questions",
      path: "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    });
  });

  it("appends an ArtifactCardItem when a turn's file_changed path ends in discovery-document.md", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("문서 갱신해줘"));
    const es = FakeEventSource.last!;
    for (const frame of documentTurn) act(() => es.emit(frame));

    const found = cards(result.current.items);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({
      role: "card",
      card: "artifact",
      path: "aiplc-docs/discovery/discovery-document.md",
    });
  });

  it("dedupes multiple file_changed events for the same path into a single card", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = FakeEventSource.last!;
    const repeated = [
      { kind: "file_changed" as const, text: null, path: "aiplc-docs/discovery/discovery-document.md" },
      { kind: "file_changed" as const, text: null, path: "aiplc-docs/discovery/discovery-document.md" },
      { kind: "done" as const, text: null, path: null },
    ];
    for (const frame of repeated) act(() => es.emit(frame));
    expect(cards(result.current.items)).toHaveLength(1);
  });

  it("does not append a card for file_changed paths matching neither suffix (e.g. prototype source files)", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("필터 추가"));
    const es = FakeEventSource.last!;
    for (const frame of normalTurn) act(() => es.emit(frame)); // normalTurn's path is prototype/src/components/FilterBar.tsx
    expect(cards(result.current.items)).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/useTurnStream.test.tsx`
Expected: FAIL — `role: "card"` items are never produced (`cards(...)` is always empty; `QuestionsCardItem`/`ArtifactCardItem`/`CardItem` don't exist yet).

- [ ] **Step 3: Write the implementation**

```ts
// frontend/lib/useTurnStream.ts  (full replacement)
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamEvents } from "@/lib/api/sse";
import type { AgentEvent } from "@/lib/api/types";

// UI VIEW-STATE (not a backend contract): how streamed AgentEvent frames are
// projected into the chat timeline. Backend contract types stay in
// lib/api/types.ts.
export interface TraceEntry {
  kind: "status" | "file_changed";
  text: string | null;
  path: string | null;
}
export interface UserItem {
  id: string;
  role: "user";
  text: string;
}
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
}
// C2: structured timeline cards, materialized from file_changed paths seen
// during a completed turn. Pure filename-suffix mapping (see
// deriveCardsFromPaths below) — never content sniffing, never a gate/approval
// inference (see the plan header's "PROMINENT DEVIATION" note).
export interface QuestionsCardItem {
  id: string;
  role: "card";
  card: "questions";
  path: string;
}
export interface ArtifactCardItem {
  id: string;
  role: "card";
  card: "artifact";
  path: string;
}
export type CardItem = QuestionsCardItem | ArtifactCardItem;
export type ChatItem = UserItem | AiItem | CardItem;

let counter = 0;
const nextId = () => `item-${counter++}`;

// Pure filename mapping (zero methodology — same class of check as Plan B's
// established `isClarification` endsWith check in questions/page.tsx): a
// `-questions.md` path materializes a QuestionsCardItem (QuestionCardSlot
// decides AT RENDER TIME, by data shape, whether it's answered/clarification/
// unparsed); a `discovery-document.md` path materializes an ArtifactCardItem.
// One card per UNIQUE path per turn — a turn that touches the same file twice
// still yields a single card. Order follows first-seen order within the turn.
function deriveCardsFromPaths(paths: string[]): CardItem[] {
  const seen = new Set<string>();
  const cards: CardItem[] = [];
  for (const path of paths) {
    if (seen.has(path)) continue;
    seen.add(path);
    if (path.endsWith("-questions.md")) {
      cards.push({ id: nextId(), role: "card", card: "questions", path });
    } else if (path.endsWith("discovery-document.md")) {
      cards.push({ id: nextId(), role: "card", card: "artifact", path });
    }
  }
  return cards;
}

export interface TurnStream {
  items: ChatItem[];
  streaming: boolean;
  send: (text: string) => void;
}

// Drives one live agent turn at a time over the EXISTING GET /events SSE
// (Plan A's streamEvents). status/file_changed frames become the AI bubble's
// "추론 과정" trace; message frames accumulate into its text; an error-KIND
// frame sets its error; done/transport-close finish the turn AND (C2) derive
// zero or more structured cards from the turn's file_changed paths.
export function useTurnStream(projectId: string, initial: ChatItem[] = []): TurnStream {
  const [items, setItems] = useState<ChatItem[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const stopRef = useRef<null | (() => void)>(null);

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard on the live-stream ref (not the `streaming` state) so a stale
      // closure can't slip a concurrent send past a not-yet-flushed setState.
      if (trimmed === "" || stopRef.current) return;

      const aiId = nextId();
      // Local accumulator for THIS turn's file_changed paths — read at onDone
      // to derive cards, independent of React's async state batching.
      const turnPaths: string[] = [];

      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: trimmed },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      setStreaming(true);

      const finish = () => {
        setStreaming(false);
        stopRef.current = null;
      };

      stopRef.current = streamEvents(projectId, trimmed, {
        onEvent: (ev: AgentEvent) => {
          if (ev.kind === "file_changed" && ev.path) turnPaths.push(ev.path);
          patchAi(aiId, (it) => {
            if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
            if (ev.kind === "status" || ev.kind === "file_changed")
              return { ...it, trace: [...it.trace, { kind: ev.kind, text: ev.text, path: ev.path }] };
            if (ev.kind === "error")
              return { ...it, error: ev.text ?? "턴 처리 중 오류가 발생했습니다." };
            return it; // "done" is handled by onDone
          });
        },
        onDone: () => {
          patchAi(aiId, (it) => ({ ...it, streaming: false }));
          const derived = deriveCardsFromPaths(turnPaths);
          if (derived.length > 0) setItems((prev) => [...prev, ...derived]);
          finish();
        },
        onError: () => {
          patchAi(aiId, (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? "연결이 끊어졌습니다. 다시 시도해 주세요.",
          }));
          finish();
        },
      });
    },
    [projectId, patchAi],
  );

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return { items, streaming, send };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/useTurnStream.test.tsx && npx tsc --noEmit`
Expected: PASS (9 tests: the 5 pre-existing C1 tests + 4 new C2 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/useTurnStream.ts frontend/lib/useTurnStream.test.tsx frontend/test/fixtures/agentEventStreams.ts
git commit -m "feat(frontend): materialize structured timeline cards from file_changed paths"
```

---

### Task 2: `QuestionSummaryCard` + `ClarificationCard` presentational components

**Files:**
- Create: `frontend/components/canvas/QuestionSummaryCard.tsx`
- Create: `frontend/components/canvas/ClarificationCard.tsx`
- Test: `frontend/components/canvas/QuestionSummaryCard.test.tsx`
- Test: `frontend/components/canvas/ClarificationCard.test.tsx`

**Interfaces:** (both presentational, prop-driven; ported from mockup 04's collapsed-summary and contradiction cards)
- `QuestionSummaryCard({ file }: { file: QuestionFile })` — the green "제출됨" collapsed summary (rendered when ALL of `file.questions` are answered — the caller, `QuestionCardSlot` in Task 3, decides that; this component just renders a `QuestionFile`). Collapsed row: emerald ✓, a title line `"{basename(file.name)} · {answered}개 답변 완료"` (uses `answeredCount(file)` from `lib/stageProgress.ts`, Plan B — presentational count only, no methodology) + the mockup's verbatim submitted note `"제출됨 · audit.md Entry 3 · 변경하려면 채팅으로 요청하세요"` (static chrome, matching the project's existing pattern of un-backed static copy like the header's "audit.md 기록 중" badge and the input's audit footnote) + a `"펼치기"` toggle button. Below: Q-chips `"Q{n}:{answer}"` for every question. Clicking `"펼치기"` toggles an expanded `<ul>` listing each question's `"Q{n}. {text}"` and `"답변: {answer}"`.
- `ClarificationCard({ file, onChoose, busy }: { file: QuestionFile; onChoose: (text: string) => void; busy: boolean })` — the amber contradiction/clarification card (rendered when `file` has at least one unanswered question AND its path ends in `-clarification-questions.md` — again, the caller decides that; this component renders whatever `QuestionFile` it's given). Header icon + the mockup's verbatim idiom `"답변 간 모순 감지 — 게이트 보류"`; `file.preamble` (when set) below it; then per question in `file.questions`: its `category` (when set) and `text`, followed by one button per `option` in `q.options` labeled `"{letter}. {text}"`. Clicking an option button calls `onChoose(\`${letter} — ${text}\`)` (em dash, exact format the page relays into `send(...)`) and is disabled while `busy`.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/QuestionSummaryCard.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionSummaryCard } from "./QuestionSummaryCard";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

describe("QuestionSummaryCard", () => {
  it("renders the collapsed summary with Q-chips and the verbatim submitted note", () => {
    render(<QuestionSummaryCard file={strategyQuestions} />);
    expect(screen.getByText(/13개 답변 완료/)).toBeInTheDocument();
    expect(
      screen.getByText("제출됨 · audit.md Entry 3 · 변경하려면 채팅으로 요청하세요"),
    ).toBeInTheDocument();
    expect(screen.getByText("Q1:A")).toBeInTheDocument();
    expect(screen.getByText("Q11:C")).toBeInTheDocument();
    // Question text is hidden until expanded.
    expect(
      screen.queryByText("Q1. 이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?"),
    ).not.toBeInTheDocument();
  });

  it("expands to show each question's text and answer on 펼치기 click", async () => {
    const user = userEvent.setup();
    render(<QuestionSummaryCard file={strategyQuestions} />);
    await user.click(screen.getByRole("button", { name: "펼치기" }));
    expect(
      screen.getByText("Q1. 이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?"),
    ).toBeInTheDocument();
    expect(screen.getByText("답변: A")).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/canvas/ClarificationCard.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClarificationCard } from "./ClarificationCard";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";

// The fixture's single question already carries answer:"C" (the pilot's
// resolved history from Plan A/B's wizard fixtures) — an unanswered variant is
// derived here so the card renders as the still-open interaction the mockup
// depicts (an unresolved contradiction with live option buttons).
const unanswered = {
  ...clarificationQuestions,
  questions: clarificationQuestions.questions.map((q) => ({ ...q, answer: null })),
};

describe("ClarificationCard", () => {
  it("renders the contradiction heading, preamble, and per-question category/text/options", () => {
    render(<ClarificationCard file={unanswered} onChoose={vi.fn()} busy={false} />);
    expect(screen.getByText("답변 간 모순 감지 — 게이트 보류")).toBeInTheDocument();
    expect(screen.getByText(unanswered.preamble!)).toBeInTheDocument();
    expect(screen.getByText(unanswered.questions[0].category!)).toBeInTheDocument();
    expect(screen.getByText(unanswered.questions[0].text)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /아직 정하지 않음/ })).toBeInTheDocument();
  });

  it("clicking an option calls onChoose with 'letter — text'", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(<ClarificationCard file={unanswered} onChoose={onChoose} busy={false} />);
    await user.click(screen.getByRole("button", { name: /아직 정하지 않음/ }));
    expect(onChoose).toHaveBeenCalledWith("C — 아직 정하지 않음 — 파일럿 운영 중 데이터로 결정");
  });

  it("disables option buttons while busy", () => {
    render(<ClarificationCard file={unanswered} onChoose={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /아직 정하지 않음/ })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas/QuestionSummaryCard.test.tsx components/canvas/ClarificationCard.test.tsx`
Expected: FAIL — component imports do not resolve.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/canvas/QuestionSummaryCard.tsx
"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { answeredCount } from "@/lib/stageProgress";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

// Green collapsed "제출됨" summary (mockup 04's submitted-question-set idiom).
// Rendered when the caller (QuestionCardSlot, Task 3) has determined every
// question in `file` is answered — this component just renders the data.
export function QuestionSummaryCard({ file }: { file: QuestionFile }) {
  const [expanded, setExpanded] = useState(false);
  const { answered } = answeredCount(file);

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-4 py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="text-emerald-600" aria-hidden="true">
            ✓
          </span>
          <div>
            <p className="font-medium">
              {basename(file.name)} · {answered}개 답변 완료
            </p>
            <p className="text-[11px] text-slate-400">
              제출됨 · audit.md Entry 3 · 변경하려면 채팅으로 요청하세요
            </p>
          </div>
        </div>
        <button
          type="button"
          className="text-[11px] text-slate-400 hover:text-violet-600 shrink-0"
          onClick={() => setExpanded((v) => !v)}
        >
          펼치기
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
        {file.questions.map((q) => (
          <span
            key={q.number}
            className="px-2 py-0.5 rounded bg-white border border-emerald-200 text-slate-500"
          >
            Q{q.number}:{q.answer ?? ""}
          </span>
        ))}
      </div>
      {expanded && (
        <ul className="mt-3 space-y-2 border-t border-emerald-200 pt-3">
          {file.questions.map((q) => (
            <li key={q.number} className="text-xs text-slate-600">
              <p className="font-medium">
                Q{q.number}. {q.text}
              </p>
              <p className="text-slate-400 mt-0.5">답변: {q.answer ?? "-"}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

```tsx
// frontend/components/canvas/ClarificationCard.tsx
import type { QuestionFile } from "@/lib/api/types";

// Amber contradiction/clarification card (mockup 04's "답변 간 모순 감지"
// idiom). Rendered when the caller (QuestionCardSlot, Task 3) has determined
// `file` has an unanswered question AND its path is a *-clarification-
// questions.md file — this component just renders the data + wires option
// buttons back to the single `onChoose` callback (the page relays the chosen
// text through the SAME useTurnStream `send`, Task 5 — no separate submit path).
export function ClarificationCard({
  file,
  onChoose,
  busy,
}: {
  file: QuestionFile;
  onChoose: (text: string) => void;
  busy: boolean;
}) {
  return (
    <div role="alert" className="rounded-xl border-2 border-amber-300 bg-amber-50 px-4 py-3.5">
      <div className="flex items-center gap-2">
        <span aria-hidden="true">⚠️</span>
        <p className="text-sm font-bold text-amber-900">답변 간 모순 감지 — 게이트 보류</p>
      </div>
      {file.preamble && <p className="text-sm text-amber-800 mt-1.5 leading-relaxed">{file.preamble}</p>}
      {file.questions.map((q) => (
        <div key={q.number} className="mt-3">
          {q.category && <p className="text-xs font-medium text-amber-700">{q.category}</p>}
          <p className="text-sm text-amber-800 mt-1 leading-relaxed">{q.text}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {q.options.map((opt) => (
              <button
                key={opt.letter}
                type="button"
                disabled={busy}
                onClick={() => onChoose(`${opt.letter} — ${opt.text}`)}
                className="px-3 py-1.5 rounded-lg bg-white border border-amber-300 text-amber-900 text-xs font-medium hover:bg-amber-100 disabled:opacity-50"
              >
                {opt.letter}. {opt.text}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas/QuestionSummaryCard.test.tsx components/canvas/ClarificationCard.test.tsx && npx tsc --noEmit`
Expected: PASS (QuestionSummaryCard ×2, ClarificationCard ×3 = 5 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/QuestionSummaryCard.tsx frontend/components/canvas/ClarificationCard.tsx \
  frontend/components/canvas/QuestionSummaryCard.test.tsx frontend/components/canvas/ClarificationCard.test.tsx
git commit -m "feat(frontend): question-answer summary card + amber clarification card"
```

---

### Task 3: `ArtifactCard` presentational + `QuestionCardSlot` container

**Files:**
- Create: `frontend/components/canvas/ArtifactCard.tsx`
- Create: `frontend/components/canvas/QuestionCardSlot.tsx`
- Test: `frontend/components/canvas/ArtifactCard.test.tsx`
- Test: `frontend/components/canvas/QuestionCardSlot.test.tsx`

**Interfaces:**
- `ArtifactCard({ path, onOpen }: { path: string; onOpen: () => void })` — presentational, ported from mockup 04's `📕` artifact button. A full-width button: 📕 icon chip, title line `"discovery-document.md — Part 1: Envision"` (the mockup's verbatim title — static chrome; the backend document is one blob with no part metadata, so this line is fixed copy, not derived from `path`), sub-line `"패널에서 열기 →"` shown on hover/focus (mockup's `group-hover:opacity-100` idiom, kept as always-visible here since there is no hover state in tests — rendered plainly, not conditionally hidden, so it's always in the accessibility tree). Clicking anywhere on the button calls `onOpen()`. `aria-label="discovery-document.md을 우측 패널에서 열기"` (mockup's aria-label pattern, adjusted to the always-open Document tab rather than "Part 1").
- `QuestionCardSlot({ projectId, path, onChoose, busy }: { projectId: string; path: string; onChoose: (text: string) => void; busy: boolean })` — thin data container. `useAsync(() => getQuestionFile(projectId, path), [projectId, path])` (Plan A `getQuestionFile`, Plan A `useAsync`). Renders **by data shape**, not by filename alone (filename already routed us here via `useTurnStream`'s `card: "questions"`, but which PRESENTATIONAL card to show depends on the fetched content):
  - loading → a small skeleton line (`"불러오는 중…"`, muted, same idiom as other pages' loading states).
  - error → a compact Korean error line (`"질문을 불러오지 못했습니다."`, rose-600, same idiom as `questions/page.tsx`'s error lines).
  - loaded, `parse_ok` true, every question answered (`answeredCount(file).answered === answeredCount(file).total`, Plan B) → `QuestionSummaryCard`.
  - loaded, some unanswered AND `path.endsWith("-clarification-questions.md")` → `ClarificationCard`, wired `onChoose={onChoose}` `busy={busy}`.
  - anything else unanswered (a non-clarification question file still open, or `parse_ok===false`) → a compact link card: `"{basename}에 답변이 필요합니다"` + a link to `/projects/{projectId}/questions?file={path}` labeled `"질문 답변하러 가기 →"` (does NOT rebuild the wizard inline — reuses the existing Plan A `/questions` route).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/ArtifactCard.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactCard } from "./ArtifactCard";

describe("ArtifactCard", () => {
  it("renders the mockup's verbatim title and opens the panel on click", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ArtifactCard path="aiplc-docs/discovery/discovery-document.md" onOpen={onOpen} />);
    expect(screen.getByText("discovery-document.md — Part 1: Envision")).toBeInTheDocument();
    expect(screen.getByText("패널에서 열기 →")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
```

```tsx
// frontend/components/canvas/QuestionCardSlot.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { QuestionCardSlot } from "./QuestionCardSlot";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const CLAR = "aiplc-docs/discovery/envision/prfaq-clarification-questions.md";
const UNPARSED = "aiplc-docs/discovery/go-to-market/gtm-questions.md";

describe("QuestionCardSlot", () => {
  it("renders QuestionSummaryCard when every question in the fetched file is answered", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={STRAT} onChoose={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("renders ClarificationCard when the file has an unanswered clarification question", async () => {
    const unanswered = {
      ...clarificationQuestions,
      questions: clarificationQuestions.questions.map((q) => ({ ...q, answer: null })),
    };
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/questions/${CLAR}`, () => HttpResponse.json(unanswered)));
    const onChoose = vi.fn();
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={CLAR} onChoose={onChoose} busy={false} />);
    });
    expect(await screen.findByText("답변 간 모순 감지 — 게이트 보류")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /아직 정하지 않음/ }));
    expect(onChoose).toHaveBeenCalledWith("C — 아직 정하지 않음 — 파일럿 운영 중 데이터로 결정");
  });

  it("renders a compact link card for an unanswered non-clarification / unparsed file", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${UNPARSED}`, () => HttpResponse.json(unparsedQuestions)),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={UNPARSED} onChoose={vi.fn()} busy={false} />);
    });
    const link = await screen.findByRole("link", { name: /질문 답변하러 가기/ });
    expect(link.getAttribute("href")).toContain(encodeURIComponent(UNPARSED));
  });

  it("renders a Korean error line when the fetch fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<QuestionCardSlot projectId="pilot1" path={STRAT} onChoose={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText("질문을 불러오지 못했습니다.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas/ArtifactCard.test.tsx components/canvas/QuestionCardSlot.test.tsx`
Expected: FAIL — component imports do not resolve.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/canvas/ArtifactCard.tsx
// Presentational 📕 artifact button (mockup 04's inline artifact-card idiom).
// The mockup's title/sub-copy is verbatim static chrome: the backend document
// is one markdown blob (no part metadata), so "Part 1: Envision" is NOT
// derived from `path` — it's the mockup's fixed label for the one artifact
// card kind this slice produces (discovery-document.md).
export function ArtifactCard({ path, onOpen }: { path: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="discovery-document.md을 우측 패널에서 열기"
      className="w-full text-left rounded-xl border border-slate-200 bg-white hover:border-violet-300 hover:shadow-sm transition-all px-4 py-3 flex items-center gap-3"
    >
      <span
        className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center text-lg shrink-0"
        aria-hidden="true"
      >
        📕
      </span>
      <span className="flex-1 min-w-0">
        <span className="block font-medium text-sm">discovery-document.md — Part 1: Envision</span>
        <span className="block text-[11px] text-slate-400 mt-0.5">{path}</span>
      </span>
      <span className="text-xs text-violet-600 shrink-0">패널에서 열기 →</span>
    </button>
  );
}
```

```tsx
// frontend/components/canvas/QuestionCardSlot.tsx
"use client";
import { getQuestionFile } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { answeredCount } from "@/lib/stageProgress";
import { QuestionSummaryCard } from "./QuestionSummaryCard";
import { ClarificationCard } from "./ClarificationCard";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

// Thin data container: fetches the QuestionFile the file_changed path pointed
// to, then picks a PRESENTATIONAL card by data shape (not by filename alone —
// the filename already routed us here via useTurnStream's card:"questions").
export function QuestionCardSlot({
  projectId,
  path,
  onChoose,
  busy,
}: {
  projectId: string;
  path: string;
  onChoose: (text: string) => void;
  busy: boolean;
}) {
  const { data: file, loading, error } = useAsync(() => getQuestionFile(projectId, path), [projectId, path]);

  if (loading && !file) return <p className="text-xs text-slate-400 ml-1">불러오는 중…</p>;
  if (error) return <p className="text-xs text-rose-600 ml-1">질문을 불러오지 못했습니다.</p>;
  if (!file) return null;

  const { answered, total } = answeredCount(file);
  const allAnswered = total > 0 && answered === total;

  if (allAnswered) return <QuestionSummaryCard file={file} />;

  if (path.endsWith("-clarification-questions.md")) {
    return <ClarificationCard file={file} onChoose={onChoose} busy={busy} />;
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm flex items-center justify-between gap-3">
      <p className="text-slate-600">{basename(path)}에 답변이 필요합니다</p>
      <a
        href={`/projects/${projectId}/questions?file=${encodeURIComponent(path)}`}
        className="text-xs text-violet-600 font-medium shrink-0 hover:text-violet-700"
      >
        질문 답변하러 가기 →
      </a>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas/ArtifactCard.test.tsx components/canvas/QuestionCardSlot.test.tsx && npx tsc --noEmit`
Expected: PASS (ArtifactCard ×1, QuestionCardSlot ×4 = 5 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/ArtifactCard.tsx frontend/components/canvas/QuestionCardSlot.tsx \
  frontend/components/canvas/ArtifactCard.test.tsx frontend/components/canvas/QuestionCardSlot.test.tsx
git commit -m "feat(frontend): artifact card + question-card data container"
```

---

### Task 4: `DocumentView` + `CanvasRightPanel` (+ `PreviewPanel` inner refactor)

**Files:**
- Create: `frontend/components/canvas/DocumentView.tsx`
- Create: `frontend/components/canvas/CanvasRightPanel.tsx`
- Modify: `frontend/components/canvas/PreviewPanel.tsx` (split into an exported inner `PreviewPanelBody` + the existing aside-wrapped `PreviewPanel`)
- Test: `frontend/components/canvas/DocumentView.test.tsx`
- Test: `frontend/components/canvas/CanvasRightPanel.test.tsx`
- Test (unchanged, must still pass as-is): `frontend/components/canvas/PreviewPanel.test.tsx`

**Interfaces:**
- **`PreviewPanel.tsx` refactor** (minimal, preserves the existing `PreviewPanel.test.tsx` byte-for-byte): the current body (header + iframe-or-placeholder) is extracted into `export function PreviewPanelBody({ projectId, prototypeId }: { projectId: string; prototypeId?: string | null })` — same JSX, no `<aside>` wrapper. `PreviewPanel({ projectId, prototypeId })` becomes a thin wrapper: the same `<aside className="hidden xl:flex w-[420px] ...">` C1 had, now containing `<PreviewPanelBody .../>`. Net rendered output for `PreviewPanel` is byte-identical to C1, so its existing test needs zero changes. `CanvasRightPanel` (below) uses `PreviewPanelBody` directly so the Preview tab doesn't nest a second `<aside>` inside `CanvasRightPanel`'s own.
- `DocumentView({ projectId, onApprove, onRevise, busy }: { projectId: string; onApprove: () => void; onRevise: (text: string) => void; busy: boolean })` — self-contained content (no `<aside>`; it lives inside `CanvasRightPanel`'s). Header row ported verbatim from mockup 04's right-panel header: 📕 + `"discovery-document.md"` + a `"Living"` badge + a decorative `".md"` button (matches the existing, also-decorative `.md 내보내기` button in `components/review/DocumentPanel.tsx` — no `onClick`, no export backend exists). Body: `useAsync(() => getDocument(projectId), [projectId])` (Plan A) → `MarkdownView` (Plan B, read-only reuse) when markdown is loaded; `loading` (and no data yet) → `"불러오는 중…"`; a `404` `ApiError` → `"문서가 아직 없습니다."` (not an error state — an empty-artifact state, matching `review/page.tsx`'s existing 404-tolerant pattern); any other error → `"문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요."`. Footer row ported verbatim from the mockup: `"✏️ 수정 요청"` toggles an inline textarea (`aria-label="수정 요청 사항"`) with `"취소"` / `"수정 요청 제출"` buttons — submitting calls `onRevise(text)`, clears the textarea, and closes it; `"✓ 이 문서 승인"` calls `onApprove()` directly. Both footer buttons `disabled={busy}`; the submit button also disabled on empty text.
- `CanvasRightPanel({ projectId, tab, onTabChange, onApprove, onRevise, busy }: { projectId: string; tab: "document" | "preview"; onTabChange: (tab: "document" | "preview") => void; onApprove: () => void; onRevise: (text: string) => void; busy: boolean })` — the right `<aside>` (C1's `hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col` geometry, unchanged), `aria-label="아티팩트 패널"`. A `role="tablist" aria-label="아티팩트 패널 탭"` row with two `role="tab"` buttons — `"문서"` (`tab==="document"`) and `"프리뷰"` (`tab==="preview"`) — each `aria-selected` matching `tab`, `onClick` calling `onTabChange(...)` with the clicked value. Below: `tab==="document"` renders `<DocumentView projectId={projectId} onApprove={onApprove} onRevise={onRevise} busy={busy} />`; `tab==="preview"` renders `<PreviewPanelBody projectId={projectId} />`. **No part tabs** — the mockup's disabled "Part 2/3/4 🔒" row is NOT ported (conscious simplification: the backend document is one markdown blob with no part segmentation — see Global Constraints).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/DocumentView.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { DocumentView } from "./DocumentView";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

describe("DocumentView", () => {
  it("renders the mockup's document header and the fetched markdown", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    expect(screen.getByText("Living")).toBeInTheDocument();
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
  });

  it('shows "문서가 아직 없습니다." on a 404 (no document yet)', async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () =>
        HttpResponse.json({ detail: "none" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText("문서가 아직 없습니다.")).toBeInTheDocument();
  });

  it("shows a Korean load-error line on a non-404 error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={vi.fn()} busy={false} />);
    });
    expect(await screen.findByText(/문서를 불러오지 못했습니다/)).toBeInTheDocument();
  });

  it("clicking 이 문서 승인 calls onApprove", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    const onApprove = vi.fn();
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={onApprove} onRevise={vi.fn()} busy={false} />);
    });
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: "✓ 이 문서 승인" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("submitting a revision calls onRevise with the typed text", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    const onRevise = vi.fn();
    await act(async () => {
      render(<DocumentView projectId="pilot1" onApprove={vi.fn()} onRevise={onRevise} busy={false} />);
    });
    await screen.findByText("Press Release");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "✏️ 수정 요청" }));
    await user.type(screen.getByLabelText("수정 요청 사항"), "FAQ에 다국어 지원 추가");
    await user.click(screen.getByRole("button", { name: "수정 요청 제출" }));
    expect(onRevise).toHaveBeenCalledWith("FAQ에 다국어 지원 추가");
  });
});
```

```tsx
// frontend/components/canvas/CanvasRightPanel.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { CanvasRightPanel } from "./CanvasRightPanel";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

describe("CanvasRightPanel", () => {
  it("renders the Document tab's content and marks it selected when tab='document'", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(
        <CanvasRightPanel
          projectId="pilot1"
          tab="document"
          onTabChange={vi.fn()}
          onApprove={vi.fn()}
          onRevise={vi.fn()}
          busy={false}
        />,
      );
    });
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "프리뷰" })).toHaveAttribute("aria-selected", "false");
  });

  it("renders the Preview tab's deferred placeholder when tab='preview' (no document fetch)", () => {
    render(
      <CanvasRightPanel
        projectId="pilot1"
        tab="preview"
        onTabChange={vi.fn()}
        onApprove={vi.fn()}
        onRevise={vi.fn()}
        busy={false}
      />,
    );
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "프리뷰" })).toHaveAttribute("aria-selected", "true");
  });

  it("clicking a tab calls onTabChange with the clicked tab", async () => {
    const onTabChange = vi.fn();
    render(
      <CanvasRightPanel
        projectId="pilot1"
        tab="document"
        onTabChange={onTabChange}
        onApprove={vi.fn()}
        onRevise={vi.fn()}
        busy={false}
      />,
    );
    // getDocument fires but this test doesn't await it — only the click matters.
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: "" })));
    await userEvent.click(screen.getByRole("tab", { name: "프리뷰" }));
    expect(onTabChange).toHaveBeenCalledWith("preview");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas/DocumentView.test.tsx components/canvas/CanvasRightPanel.test.tsx components/canvas/PreviewPanel.test.tsx`
Expected: FAIL — `DocumentView`/`CanvasRightPanel` imports do not resolve; `PreviewPanel.test.tsx` still PASSES unchanged (nothing has been edited in `PreviewPanel.tsx` yet).

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/canvas/PreviewPanel.tsx  (full replacement)
import { previewUrl } from "@/lib/api/preview";

// Inner content (no <aside> wrapper) — reused by CanvasRightPanel's Preview
// tab so the switchable right panel doesn't nest two <aside> elements. The
// preview URL comes ONLY from the previewUrl seam (C1 Task 1), which returns
// null until the Phase 2/3 prototype build backend exists — so today this
// renders the documented "프로토타입 빌드 대기 중" placeholder. When the build
// backend lands the same body renders a live <iframe>, no other change.
export function PreviewPanelBody({
  projectId,
  prototypeId,
}: {
  projectId: string;
  prototypeId?: string | null;
}) {
  const url = previewUrl(projectId, prototypeId);
  return (
    <>
      <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2 shrink-0">
        <span aria-hidden="true">🖥️</span>
        <p className="font-bold text-sm">프로토타입 프리뷰</p>
      </div>
      {url ? (
        <iframe title="프로토타입 프리뷰" src={url} className="flex-1 w-full border-0" />
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-center p-8 gap-3">
          <span className="text-4xl" aria-hidden="true">
            🛠️
          </span>
          <p className="font-bold text-slate-600">프로토타입 빌드 대기 중</p>
          <p className="text-xs text-slate-400 leading-relaxed max-w-[16rem]">
            프로토타입 빌드 파이프라인이 준비되면 이곳에 실시간 프리뷰가 표시됩니다. 지금은 채팅으로
            프로토타입 요청·수정을 진행할 수 있습니다.
          </p>
        </div>
      )}
    </>
  );
}

// C1's original aside-wrapped shape — kept for backward compatibility (its
// test is unchanged). C2's canvas page (Task 5) renders CanvasRightPanel
// instead of this directly; CanvasRightPanel nests PreviewPanelBody inside
// its own single <aside>.
export function PreviewPanel({
  projectId,
  prototypeId,
}: {
  projectId: string;
  prototypeId?: string | null;
}) {
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label="프로토타입 프리뷰 패널"
    >
      <PreviewPanelBody projectId={projectId} prototypeId={prototypeId} />
    </aside>
  );
}
```

```tsx
// frontend/components/canvas/DocumentView.tsx
"use client";
import { useState } from "react";
import { getDocument, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { MarkdownView } from "@/components/review/MarkdownView";

// Living-Document view for the right panel's "문서" tab. No part tabs (the
// mockup's Part 1/2/3/4 row is NOT ported — GET /document returns one
// markdown blob with no part segmentation; see Global Constraints). Approval
// UX (re-deferred from a structured gate card — see plan header) lives here:
// both buttons simply relay natural-language turns through the caller's
// onApprove/onRevise, which the canvas page wires to the SAME useTurnStream
// `send` the chat input uses.
export function DocumentView({
  projectId,
  onApprove,
  onRevise,
  busy,
}: {
  projectId: string;
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  const [revising, setRevising] = useState(false);
  const [text, setText] = useState("");
  const { data: markdown, loading, error } = useAsync(() => getDocument(projectId), [projectId]);

  const notFound = error instanceof ApiError && error.status === 404;
  const loadError = error !== null && !notFound;

  function submitRevision() {
    const trimmed = text.trim();
    if (trimmed === "") return;
    onRevise(trimmed);
    setText("");
    setRevising(false);
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span aria-hidden="true">📕</span>
          <p className="font-bold text-sm">discovery-document.md</p>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-600">Living</span>
        </div>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-slate-500"
        >
          .md
        </button>
      </div>

      <div className="flex-1 overflow-y-auto chat-scroll p-5 text-sm text-slate-700">
        {loading && !markdown && <p className="text-slate-400">불러오는 중…</p>}
        {notFound && <p className="text-slate-400">문서가 아직 없습니다.</p>}
        {loadError && (
          <p className="text-rose-600">문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {markdown && <MarkdownView markdown={markdown} />}
      </div>

      <div className="p-3 border-t border-slate-100 shrink-0 space-y-2">
        {revising && (
          <div className="space-y-2">
            <textarea
              aria-label="수정 요청 사항"
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="예: FAQ에 다국어 지원 계획 항목을 추가해줘."
              className="w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setRevising(false)}
                className="px-3 py-2 text-sm rounded-lg border border-slate-300 hover:bg-slate-50"
              >
                취소
              </button>
              <button
                type="button"
                disabled={busy || text.trim() === ""}
                onClick={submitRevision}
                className="px-3 py-2 text-sm rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50"
              >
                수정 요청 제출
              </button>
            </div>
          </div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => setRevising((v) => !v)}
            className="flex-1 py-2.5 rounded-lg border border-slate-300 hover:bg-slate-50 text-sm font-medium disabled:opacity-50"
          >
            ✏️ 수정 요청
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onApprove}
            className="flex-1 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-bold disabled:opacity-50"
          >
            ✓ 이 문서 승인
          </button>
        </div>
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/canvas/CanvasRightPanel.tsx
import { DocumentView } from "./DocumentView";
import { PreviewPanelBody } from "./PreviewPanel";

const TABS: { key: "document" | "preview"; label: string }[] = [
  { key: "document", label: "문서" },
  { key: "preview", label: "프리뷰" },
];

// The switchable right panel (C1's PreviewPanel-only pane, now a controlled
// 문서/프리뷰 toggle). Geometry unchanged from C1: hidden xl:flex w-[420px].
export function CanvasRightPanel({
  projectId,
  tab,
  onTabChange,
  onApprove,
  onRevise,
  busy,
}: {
  projectId: string;
  tab: "document" | "preview";
  onTabChange: (tab: "document" | "preview") => void;
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label="아티팩트 패널"
    >
      <div
        className="px-4 pt-3 flex gap-1 border-b border-slate-100 text-xs shrink-0"
        role="tablist"
        aria-label="아티팩트 패널 탭"
      >
        {TABS.map((t) => {
          const active = t.key === tab;
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(t.key)}
              className={
                active
                  ? "px-3 py-2 rounded-t-lg bg-violet-50 text-violet-700 font-bold border-b-2 border-violet-600"
                  : "px-3 py-2 text-slate-400 hover:text-slate-600"
              }
            >
              {t.label}
            </button>
          );
        })}
      </div>
      {tab === "document" ? (
        <DocumentView projectId={projectId} onApprove={onApprove} onRevise={onRevise} busy={busy} />
      ) : (
        <PreviewPanelBody projectId={projectId} />
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas/DocumentView.test.tsx components/canvas/CanvasRightPanel.test.tsx components/canvas/PreviewPanel.test.tsx && npx tsc --noEmit`
Expected: PASS (DocumentView ×5, CanvasRightPanel ×3, PreviewPanel ×2 unchanged = 10 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/DocumentView.tsx frontend/components/canvas/CanvasRightPanel.tsx \
  frontend/components/canvas/PreviewPanel.tsx frontend/components/canvas/DocumentView.test.tsx \
  frontend/components/canvas/CanvasRightPanel.test.tsx
git commit -m "feat(frontend): Living-Document view + switchable document/preview right panel"
```

---

### Task 5: Page + `ChatTimeline` wiring

**Files:**
- Modify: `frontend/components/canvas/ChatTimeline.tsx`
- Modify: `frontend/components/canvas/ChatTimeline.test.tsx`
- Modify: `frontend/app/projects/[projectId]/canvas/page.tsx`
- Modify: `frontend/app/projects/[projectId]/canvas/page.test.tsx` (add C2 cases; existing C1 cases are unaffected since `PreviewPanel`'s placeholder text is still reachable, now via the default Preview tab)

**Interfaces:**
- `ChatTimeline({ items, projectId, onChoose, onOpenArtifact, busy }: { items: ChatItem[]; projectId: string; onChoose: (text: string) => void; onOpenArtifact: () => void; busy: boolean })` — grows from C1's `{items}`-only signature (prop-driven, matching C1's established style of pushing all wiring down from the page). Maps `items`: `role==="user"` → `UserMessage` (unchanged); `role==="ai"` → `AiMessage` (unchanged); `role==="card"` → dispatch on `item.card`: `"questions"` → `<QuestionCardSlot projectId={projectId} path={item.path} onChoose={onChoose} busy={busy} />`; `"artifact"` → `<ArtifactCard path={item.path} onOpen={onOpenArtifact} />`. Card items are wrapped the same `ml-11 max-w-[85%]` indent the mockup uses for its inline "제출됨"/artifact widgets (aligning them under the AI avatar's bubble column, not full-width). Empty state and `max-w-2xl mx-auto` column, `aria-label="대화 타임라인"` — unchanged from C1. **(C2 addition)** When `items.length > 0`, renders the mockup's verbatim typing-hint chrome (static, backend-independent — same class of always-visible copy as C1's `ChatInput` audit footnote) BELOW the mapped items: `"버튼 대신 채팅으로 답해도 됩니다 — "승인", "고객 인용문을 파트장 관점으로 바꿔줘", "이전 단계로 돌아가고 싶어""` (mockup's exact three italicized examples, rendered as one Korean sentence with the three example phrases in `<span className="italic">`). Not rendered on the empty state (nothing to hint about yet).
- Canvas page (`app/projects/[projectId]/canvas/page.tsx`) gains `panelTab` state, `useState<"document" | "preview">("preview")` (default matches C1's original always-preview behavior, so a fresh session looks identical to C1 until a card/action switches it). Renders `<CanvasRightPanel projectId={projectId} tab={panelTab} onTabChange={setPanelTab} onApprove={() => send("승인")} onRevise={(text) => send(text)} busy={streaming} />` in place of C1's bare `<PreviewPanel projectId={projectId} />`. `<ChatTimeline items={items} projectId={projectId} onChoose={send} onOpenArtifact={() => setPanelTab("document")} busy={streaming} />` replaces C1's `<ChatTimeline items={items} />`. Every action funnels through the ONE existing `send` from `useTurnStream` (Global Constraint: no new fetch/SSE call sites) — `onChoose` IS `send` (a clarification option's chosen text becomes the next turn's user message, exactly like typing it); `onApprove`/`onRevise` call `send("승인")`/`send(text)` (mirroring Plan B's already-shipped `review/page.tsx` `sendTurn` pattern, but through the live SSE pipe instead of `postMessage`). Sidebar/error states, `AppHeader`, and the `GET /state` wiring are otherwise unchanged from C1.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/ChatTimeline.test.tsx  (full replacement)
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const DOC = "aiplc-docs/discovery/discovery-document.md";

describe("ChatTimeline", () => {
  it("renders user and AI bubbles in order", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "필터 추가해줘" },
      { id: "a1", role: "ai", text: "추가했습니다.", trace: [], streaming: false, error: null },
    ];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });

  it("renders the verbatim typing-hint chrome once there is at least one item, but not on the empty state", () => {
    const items: ChatItem[] = [{ id: "u1", role: "user", text: "필터 추가해줘" }];
    const { rerender } = render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/버튼 대신 채팅으로 답해도 됩니다/)).toBeInTheDocument();
    rerender(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.queryByText(/버튼 대신 채팅으로 답해도 됩니다/)).not.toBeInTheDocument();
  });

  it("renders a questions card item via QuestionCardSlot", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    const items: ChatItem[] = [{ id: "c1", role: "card", card: "questions", path: STRAT }];
    await act(async () => {
      render(
        <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
      );
    });
    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("renders an artifact card item that calls onOpenArtifact when clicked", async () => {
    const onOpenArtifact = vi.fn();
    const items: ChatItem[] = [{ id: "c2", role: "card", card: "artifact", path: DOC }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={onOpenArtifact} busy={false} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(onOpenArtifact).toHaveBeenCalledTimes(1);
  });
});
```

```tsx
// ADD to frontend/app/projects/[projectId]/canvas/page.test.tsx (new cases;
// existing C1 cases in this file are unaffected — the default panelTab is
// "preview" so "프로토타입 빌드 대기 중" is still found the same way. Add these
// imports alongside the existing ones:
//   import { questionsTurn, documentTurn } from "@/test/fixtures/agentEventStreams";  (extend the normalTurn import line)

describe("Canvas page — C2 structured cards + switchable panel", () => {
  it("materializes a questions card after a turn touches a *-questions.md file, and it renders via QuestionCardSlot", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(
        `${API_BASE_URL}/projects/pilot1/questions/aiplc-docs/discovery/product-strategy/strategy-questions.md`,
        () => HttpResponse.json(strategyQuestions),
      ),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "질문 만들어줘");
    await user.click(screen.getByRole("button", { name: "전송" }));
    const es = FakeEventSource.last!;
    for (const frame of questionsTurn) await act(async () => es.emit(frame));

    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("clicking an artifact card switches the right panel to the 문서 tab and loads the document", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument(); // default tab is preview

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "문서 갱신해줘");
    await user.click(screen.getByRole("button", { name: "전송" }));
    const es = FakeEventSource.last!;
    for (const frame of documentTurn) await act(async () => es.emit(frame));

    await user.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
  });

  it("approving from the Document tab sends '승인' as the next turn's text", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "문서" }));
    await screen.findByText("Press Release");
    await user.click(screen.getByRole("button", { name: "✓ 이 문서 승인" }));
    expect(FakeEventSource.last!.url).toContain(`text=${encodeURIComponent("승인")}`);
  });
});
```

Add the required fixture imports to `page.test.tsx`'s top-of-file import block (extending, not replacing, the existing block):

```tsx
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";
// extend: import { normalTurn, questionsTurn, documentTurn } from "@/test/fixtures/agentEventStreams";
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx "app/projects/[projectId]/canvas/page.test.tsx"`
Expected: FAIL — `ChatTimeline` rejects the new required props under the old signature (TS) / the page has no `CanvasRightPanel`/card wiring yet, so the new cases can't find the queried text/roles.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/components/canvas/ChatTimeline.tsx  (full replacement)
import type { ChatItem } from "@/lib/useTurnStream";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";
import { QuestionCardSlot } from "./QuestionCardSlot";
import { ArtifactCard } from "./ArtifactCard";

export function ChatTimeline({
  items,
  projectId,
  onChoose,
  onOpenArtifact,
  busy,
}: {
  items: ChatItem[];
  projectId: string;
  onChoose: (text: string) => void;
  onOpenArtifact: () => void;
  busy: boolean;
}) {
  return (
    <div
      className="chat-scroll flex-1 overflow-y-auto px-4 md:px-8 py-6"
      aria-label="대화 타임라인"
    >
      <div className="max-w-2xl mx-auto space-y-5">
        {items.length === 0 ? (
          <p className="text-center text-sm text-slate-400 mt-10">
            대화를 시작해 보세요 — 아래에 메시지를 입력하세요.
          </p>
        ) : (
          items.map((item) => {
            if (item.role === "user") return <UserMessage key={item.id} text={item.text} />;
            if (item.role === "ai") return <AiMessage key={item.id} item={item} />;
            // role === "card" — inline widget, indented under the AI avatar
            // column (mockup 04's ml-11 idiom for its submitted/artifact cards).
            return (
              <div key={item.id} className="ml-11 max-w-[85%]">
                {item.card === "questions" ? (
                  <QuestionCardSlot projectId={projectId} path={item.path} onChoose={onChoose} busy={busy} />
                ) : (
                  <ArtifactCard path={item.path} onOpen={onOpenArtifact} />
                )}
              </div>
            );
          })
        )}
        {items.length > 0 && (
          <p className="text-center text-[11px] text-slate-400">
            버튼 대신 채팅으로 답해도 됩니다 — <span className="italic">&quot;승인&quot;</span>,{" "}
            <span className="italic">&quot;고객 인용문을 파트장 관점으로 바꿔줘&quot;</span>,{" "}
            <span className="italic">&quot;이전 단계로 돌아가고 싶어&quot;</span>
          </p>
        )}
      </div>
    </div>
  );
}
```

```tsx
// frontend/app/projects/[projectId]/canvas/page.tsx  (full replacement)
"use client";
import { use, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CanvasSidebar } from "@/components/canvas/CanvasSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { CanvasRightPanel } from "@/components/canvas/CanvasRightPanel";
import { getState, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useTurnStream } from "@/lib/useTurnStream";

export default function CanvasPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const { items, streaming, send } = useTurnStream(projectId);
  // Default "preview" matches C1's original always-preview behavior — a fresh
  // session looks identical to C1 until a card/approval action switches it.
  const [panelTab, setPanelTab] = useState<"document" | "preview">("preview");

  const notFound = state.error instanceof ApiError && state.error.status === 404;
  const loadError = state.error && !notFound;

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <AppHeader activeTab="canvas" projectId={projectId} />
      <div className="flex-1 flex min-h-0">
        {state.data ? (
          <CanvasSidebar state={state.data} />
        ) : (
          <aside
            className="hidden lg:flex w-60 shrink-0 bg-white border-r border-slate-200 flex-col p-4 text-sm"
            aria-label="스테이지 진행 상황"
          >
            {state.loading && <p className="text-slate-400">불러오는 중…</p>}
            {notFound && <p className="text-rose-600">프로젝트를 찾을 수 없습니다.</p>}
            {loadError && (
              <p className="text-rose-600">진행 상황을 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
            )}
          </aside>
        )}

        <main className="flex-1 flex flex-col min-w-0 bg-slate-50">
          <ChatTimeline
            items={items}
            projectId={projectId}
            onChoose={send}
            onOpenArtifact={() => setPanelTab("document")}
            busy={streaming}
          />
          <ChatInput onSend={send} disabled={streaming} />
        </main>

        <CanvasRightPanel
          projectId={projectId}
          tab={panelTab}
          onTabChange={setPanelTab}
          onApprove={() => send("승인")}
          onRevise={(text) => send(text)}
          busy={streaming}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas/ChatTimeline.test.tsx "app/projects/[projectId]/canvas/page.test.tsx" && npx tsc --noEmit`
Expected: PASS — `ChatTimeline.test.tsx` ×5 total (2 pre-existing C1 cases + 3 new C2 cases, including the typing-hint case); `page.test.tsx` ×6 total (3 pre-existing C1 cases + 3 new C2 cases). `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/ChatTimeline.tsx frontend/components/canvas/ChatTimeline.test.tsx \
  "frontend/app/projects/[projectId]/canvas/page.tsx" "frontend/app/projects/[projectId]/canvas/page.test.tsx"
git commit -m "feat(frontend): wire structured cards + switchable panel into the canvas page"
```

---

### Task 6: Full suite, build, and INTEGRATION canvas e2e (artifact→panel step)

**Files:**
- Modify: `frontend/e2e/canvas.spec.ts` (add an artifact→panel integration step to the existing spec)
- Test: full Vitest suite + `next build`

**Interfaces:**
- Extends C1's INTEGRATION Playwright spec with a second `test(...)` that drives one live turn expected to touch `discovery-document.md`, clicks the resulting artifact card, and asserts the right panel switches to the 문서 tab and shows the 「✓ 이 문서 승인」 button. Still needs a live backend + a seeded project; still excluded from the unit (vitest) path (`playwright.config.ts`'s `testDir: "./e2e"` is untouched, and `vitest.config.ts`'s `exclude: ["e2e/**", ...]` already keeps Playwright specs out of `vitest run`).

- [ ] **Step 1: Add the artifact→panel integration step**

```ts
// frontend/e2e/canvas.spec.ts  (ADD this second test; keep C1's existing test as-is)
import { test, expect } from "@playwright/test";

// INTEGRATION (AWS/backend-required): drives the canvas against a real backend
// + a seeded project. Sends a message and expects a streamed AI response to
// appear in the timeline. Excluded from the unit (vitest) path.
test("send a chat message and see a streamed AI reply", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/canvas`);
  await expect(page.getByLabel("채팅 메시지 입력")).toBeVisible();
  await page.getByLabel("채팅 메시지 입력").fill("프로토타입에 대해 알려줘");
  await page.getByRole("button", { name: "전송" }).click();
  // The user bubble appears; the AI bubble streams in (the backend relays the
  // agent turn over GET /events). We scope assertions to the chat timeline so
  // this doesn't false-positive against the AppHeader's "AI" logo chip, which
  // is visible before any turn.
  const timeline = page.getByLabel("대화 타임라인");
  await expect(timeline.getByText("프로토타입에 대해 알려줘")).toBeVisible();
  await expect(timeline.locator("text=AI").first()).toBeVisible({ timeout: 30_000 });
});

// C2 INTEGRATION: a turn that touches discovery-document.md materializes an
// artifact card; clicking it switches the right panel to the 문서 tab and
// shows the Living Document + approval controls. Requires a seeded project
// whose next agent turn is expected to write/update discovery-document.md
// (E2E_PROJECT_ID should point at a fixture project pre-staged for this, e.g.
// one already past Envision so "문서 갱신해줘" triggers a document rewrite).
test("an artifact card opens the right panel's 문서 tab with the Living Document", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/canvas`);
  await page.getByLabel("채팅 메시지 입력").fill("Discovery Document를 최신 내용으로 갱신해줘");
  await page.getByRole("button", { name: "전송" }).click();

  const artifactButton = page.getByRole("button", { name: /우측 패널에서 열기/ });
  await expect(artifactButton).toBeVisible({ timeout: 30_000 });
  await artifactButton.click();

  await expect(page.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "✓ 이 문서 승인" })).toBeVisible();
});
```

- [ ] **Step 2: Run the full unit suite**

Run: `cd frontend && npm run test`
Expected: PASS — every Plan A + Plan B + C1 test (90 baseline), PLUS this plan's: useTurnStream card derivation ×4, QuestionSummaryCard ×2, ClarificationCard ×3, ArtifactCard ×1, QuestionCardSlot ×4, DocumentView ×5, CanvasRightPanel ×3, ChatTimeline ×3 new (including the typing-hint case), canvas page ×3 new = **28 new tests** → **118 total**. `e2e/` excluded from vitest.

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: `tsc` clean; `next build` succeeds; the `/projects/[projectId]/canvas` route still lists (unchanged route set from C1 — no new pages were added, only components/hook logic inside the existing canvas route).

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/canvas.spec.ts
git commit -m "test(frontend): full C2 suite green; artifact→document-tab INTEGRATION step"
```

---

## Deferred to future plans

- **Structured approval-gate timeline card.** As stated prominently in the plan header: the mockup's violet-gradient gate card (checklist of gate criteria, ✓ 승인 / ✏️ 수정 요청 / 문서 먼저 검토 buttons, appearing INLINE in the timeline at the moment a gate opens) is NOT built. It requires a structured backend signal (a `gate`-shaped field on `TurnResult`/`ProjectState`, or a new `AgentEvent` kind) that does not exist today; building it from message-prose or artifact-existence heuristics would be forbidden methodology logic. Blocked on a future "structured turn/gate signal" backend plan. Today's approval flow (this plan's `DocumentView` 승인/수정 요청 controls) is a reasonable stand-in but is always-visible rather than gate-triggered.
- **Right-panel part tabs** (mockup's "Part 1 / Part 2 🔒 / Part 3 🔒 / Part 4 🔒" row). Not built — `GET /projects/{pid}/document` returns one markdown blob with no part segmentation. Blocked on a future backend change that splits the document into addressable parts (or returns part boundaries as metadata).
- **Any prototype build/publish backend** (build status beyond generic `/events` relay, `/preview/*` reverse proxy, ECR/traefik publish, prototype listing/`prototypeId`). Carried forward verbatim from C1 — still zero backend support; the Preview tab still shows the "프로토타입 빌드 대기 중" placeholder via the unchanged `previewUrl` seam.
- Inline document comments ("💬 드래그하여 특정 문장에 수정 요청" — the mockup's drag-to-comment idiom). Not built; out of scope for both C1 and C2, no backend selection/annotation contract exists.
- Handoff/export screen, facilitator session-management screen, SSO beyond the token placeholder — carried forward from C1, still out of scope.

---

## Self-Review

**Scope coverage (C2 — structured timeline cards + switchable document/preview panel):**
- Question-answer summary card (mockup's collapsed green "제출됨" widget with Q-chips) → Task 2 `QuestionSummaryCard` + Task 3 `QuestionCardSlot` (data-shape routing) + Task 1 (`useTurnStream` materializes the card item) + Task 5 (`ChatTimeline` renders it).
- Contradiction/clarification card (amber, option buttons) → Task 2 `ClarificationCard` + Task 3 `QuestionCardSlot` + Task 5 wiring (`onChoose` → `send`).
- Artifact card (📕, opens right panel) → Task 3 `ArtifactCard` + Task 1 (`useTurnStream` materializes `ArtifactCardItem`) + Task 5 (`onOpenArtifact` → `setPanelTab("document")`).
- Switchable right panel (Living-Document view + real prototype `<iframe>`/placeholder) → Task 4 `DocumentView` + `CanvasRightPanel` + `PreviewPanel` inner refactor (`PreviewPanelBody`) + Task 5 page wiring.
- Approval UX (re-deferred from a structured gate card, relocated to the Document tab per the header's PROMINENT deviation note) → Task 4 `DocumentView`'s 승인/수정 요청 controls + Task 5 (`onApprove`/`onRevise` → `send`).
- Typing hint chrome (mockup's "버튼 대신 채팅으로 답해도 됩니다…") → Task 5 `ChatTimeline`'s non-empty branch (static chrome below the mapped items, matching the binding decisions' "typing hint stays as static chrome under the timeline"), unit-tested to appear once there is at least one item and to disappear on the empty state.

**Every planned task mapped to a concrete deliverable:** T1 (card types + derivation + fixtures) → `useTurnStream.ts`, `agentEventStreams.ts`; T2 (summary + clarification cards) → `QuestionSummaryCard.tsx`, `ClarificationCard.tsx`; T3 (artifact card + data container) → `ArtifactCard.tsx`, `QuestionCardSlot.tsx`; T4 (document view + right panel) → `DocumentView.tsx`, `CanvasRightPanel.tsx`, `PreviewPanel.tsx` refactor; T5 (page + timeline wiring) → `ChatTimeline.tsx`, `canvas/page.tsx`; T6 (suite/build/e2e) → `e2e/canvas.spec.ts`.

**DEFERRED backend contracts explicitly listed** (see "Deferred backend contracts"): C1's three (preview URL, build status/log, prototype list/id) carried forward verbatim + this plan's new #4 (structured approval-gate/build-status signal), with the prominent rationale for why the mockup's gate card is not built stated in the plan header, not buried.

**Type consistency with backend + C1's hook contract:** `useTurnStream`'s exported `send`/`items`/`streaming` signature is UNCHANGED from C1 — only the `ChatItem` union it produces grows (`CardItem = QuestionsCardItem | ArtifactCardItem`), so no C1 consumer (had there been any beyond this plan's own page) breaks. `QuestionCardSlot`/`DocumentView` import `QuestionFile`/`Question`/`QuestionOption` from `lib/api/types.ts` (Plan A, mirroring `backend/pathfinder/models.py`) and call `getQuestionFile`/`getDocument` with their EXACT Plan A signatures — no backend type redefined, no new fetch call site outside `lib/api/client.ts`. `deriveCardsFromPaths` and `QuestionCardSlot`'s answered/unanswered + clarification-suffix branching are pure string/data-shape checks, deliberately documented as the same zero-methodology class as Plan B's `isClarification`. `tsc --noEmit` is run in every task.

**Placeholder scan:** no TBD/TODO in any task's code. All component/hook/page code is shown in full (no "similar to X", no elision markers except the explicitly-labeled fixture note in `strategyQuestions.ts`, which is inherited unedited from Plan A/B and not re-elided here).

**Constraint checks:** no methodology logic — card *materialization* is a pure filename-suffix map (`deriveCardsFromPaths`); card *presentation choice* (`QuestionCardSlot`) is a pure data-shape check (all-answered vs. clarification-suffix vs. other), never content parsing/keyword sniffing. `lib/api/client.ts` remains the sole HTTP owner, `lib/api/sse.ts` the sole SSE owner, `lib/api/preview.ts` the sole preview-URL owner — this plan adds zero new fetch/SSE call sites, only new call sites into the EXISTING `getQuestionFile`/`getDocument`. Korean chrome (card copy, document header, approval buttons) ported verbatim from mockup 04 per-task Interfaces sections. Graceful states: `QuestionCardSlot` loading/error lines, `DocumentView` 404→"문서가 아직 없습니다."/other-error→Korean line, Preview tab's unchanged deferred placeholder. `useAsync` stale-data awareness carried (both new `useAsync` call sites — `QuestionCardSlot`, `DocumentView` — rely on it exactly as Plan A/B's existing call sites do). The `await act(async () => render())` Suspense-adjacent async-render pattern is used in every test that renders a `useAsync`-backed component.

**Testing strategy realized:** `useTurnStream`'s card derivation is unit-tested with the SAME fake-`EventSource`-on-`globalThis` technique as C1 (Task 1). Presentational cards (`QuestionSummaryCard`, `ClarificationCard`, `ArtifactCard`) are unit-tested directly against fixtures, matching C1's `AiMessage`/`ChatInput` pattern. The one data-fetching container (`QuestionCardSlot`) and the two `useAsync`-backed views (`DocumentView`, `CanvasRightPanel`) are tested with per-test MSW `server.use(...)` overrides and `onUnhandledRequest:"error"` (inherited harness config — unchanged). The page test extends C1's existing SSE-driven page test with 3 new cases reusing the same `FakeEventSource` class already defined in that file. Playwright `canvas.spec.ts` gains one new INTEGRATION case, still excluded from `vitest run` via the untouched `vitest.config.ts` exclude list.

**Scope sized appropriately:** 6 tasks, comparable to C1's 6-task shape. Card/presentation logic (T1–T3) is front-loaded before the panel/wiring logic (T4–T5) that consumes it, matching a dependency-respecting build order. T6 closes with the full-suite count reconciliation (90 baseline + 27 new = 117) and the one additional INTEGRATION e2e step.

**Depends on:** Plan C1 merged (canvas shell, `useTurnStream`, `streamEvents` consumer, presentational chat components, `PreviewPanel`, `previewUrl` seam) which itself depends on Plan A + Plan B merged. Unit tests mock `GET /state`/`GET /questions/{name}`/`GET /document` (MSW) and the SSE stream (fake `EventSource`), exactly as C1 established. Does not depend on MicroVM Part 2 — same backend-contract-only dependency C1 already documented.

**Blocker for full mockup-04 fidelity:** the structured approval-gate card and part-tabbed document view remain blocked on future backend plans (a structured gate/build-status signal; a part-segmented document contract), both freshly listed above rather than silently dropped. The prototype `<iframe>` and any build-specific status remain blocked on the same future "prototype build backend" plan C1 already named — unchanged by this plan.
