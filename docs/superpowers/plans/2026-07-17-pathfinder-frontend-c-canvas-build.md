# Pathfinder Frontend C1 — Conversational Canvas Shell + Live SSE Chat

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **DEPENDS ON:** `2026-07-17-pathfinder-frontend-a-scaffold-client-list.md` (Plan A) AND `2026-07-17-pathfinder-frontend-b-dashboard-wizard-review.md` (Plan B), both merged. Plan A delivers the Next.js scaffold, Tailwind theme, the typed API client (`lib/api/client.ts` + `types.ts`), **the SSE helper (`lib/api/sse.ts` `streamEvents`)**, the `useAsync` hook, the `AppHeader`, and the MSW+Vitest+RTL test harness. Plan B delivers `lib/stageProgress.ts` (`progressPercent`/`stageCounts`) and the `test/fixtures/projectState.ts` fixture, both reused here. This plan builds only the canvas screen on top of them and adds **no new dependencies**.
>
> **SCOPE SPLIT (this document is C1 of 2):** The full "Conversational Canvas + Prototype Preview + Build-Log Streaming" slice is split into two plans because the mockup (`04-conversational-canvas.html`) spans a 3-pane app shell, six+ structured timeline cards, a switchable Living-Document/iframe right panel, AND the first real SSE-streaming consumer. **This document (C1)** delivers the functional core: the 3-pane canvas shell, the progress sidebar, the live-turn SSE chat (the first real `streamEvents` consumer), the basic user/AI/reasoning-trace bubbles, and the right-pane **prototype-preview placeholder** with its deferred backend seam. **Plan C2 (described in "Deferred to Plan C2" below, not drafted here)** enriches the timeline with the structured cards (question-answer summary, contradiction/clarification, approval-gate, artifact) and replaces the placeholder right pane with the switchable Living-Document view + real iframe preview. C1 renders and is fully functional (chat + SSE + graceful deferred-preview) without C2.

**Goal:** Port the shell of `files/ui/04-conversational-canvas.html` to a React App-Router client page at `app/projects/[projectId]/canvas/page.tsx`, backed by the Plan A/B API client + SSE helper. Deliver: the **left progress sidebar** (from `GET /state`), the **center chat timeline** driven by **live agent turns streamed over the existing `GET /events?text=` SSE** (`streamEvents`' first real consumer — status/file_changed frames fold into a "추론 과정" collapsible, message frames accumulate into an AI bubble, done/error terminate the turn), the **bottom chat input** that opens the SSE stream, and the **right artifact panel** showing a documented **"프로토타입 빌드 대기 중" preview placeholder** until the Phase 2/3 build backend lands.

**Architecture:** The canvas is one App-Router client page that (a) loads `GET /state` via `useAsync` (Plan A) to render the presentational `CanvasSidebar`, (b) drives the chat timeline through a new `useTurnStream` hook that wraps the **existing** `streamEvents(pid, text, handlers)` SSE helper and folds `AgentEvent` frames into a `ChatItem[]` UI-state list, and (c) renders the right pane via a presentational `PreviewPanel` fed by a single typed **preview-URL seam** (`lib/api/preview.ts`). All timeline/sidebar/input pieces are **presentational, prop-driven components** unit-tested against fixtures; the SSE consumer logic lives in the `useTurnStream` hook, unit-tested with the same fake-`EventSource`-on-`globalThis` technique used in `lib/api/sse.test.ts`. **The frontend renders whatever the backend/agent streams and posts user input back** — it computes no methodology (no stage lists, no contradiction detection, no question wording); the chat timeline renders exactly the frames the agent emits.

**Tech Stack:** Same as Plan A/B — Next.js 15 App Router, React 19, TypeScript 5.7, Tailwind 3.4, Vitest 3 + RTL 16 + jsdom + MSW 2, Playwright (integration only). **No new libraries.** (`react-markdown`/`remark-gfm` are already present from Plan B and are used by C2's Living-Document panel, not by C1.)

**Global Constraints:** (carried from Plan A/B)
- **No methodology logic in the frontend.** Stage names/notes come only from `GET /state`; chat content comes only from streamed `AgentEvent` frames. The sidebar's `progressPercent`/`stageCounts` (Plan B) are presentational math only — they count `completed` stages, they do not know stage order or meaning. No contradiction, approval, or question logic is computed here.
- **`lib/api/client.ts` owns every HTTP fetch/URL; `lib/api/sse.ts` owns SSE.** The canvas adds **no new fetch call sites** outside the client. The one exception the slice requires — the prototype preview URL — is isolated behind a **single typed helper** (`lib/api/preview.ts` `previewUrl(...)`), not scattered string-building; it defaults to a safe disabled state (see "Deferred backend contracts").
- **Types come from `lib/api/types.ts` (Plan A)** — `AgentEvent` (`kind`/`text`/`path`, snake_case-agnostic), `TurnResult`, `ProjectState`/`StageState`. This plan imports them and does not redefine them. The chat-timeline UI item types (`ChatItem`/`TraceEntry`) are **UI view-state**, not a backend contract, so they are defined locally in `lib/useTurnStream.ts`.
- **Korean UI copy from mockup 04 is the source of truth** for user-facing static chrome (sidebar labels, input placeholder, audit note, preview placeholder). Dynamic content renders from backend/agent data.
- **Graceful error/empty states**, including the **deferred-build preview placeholder**: a transport/`error`-kind frame terminates the turn with a Korean error line in the AI bubble; a `404`/`500` on `GET /state` renders a Korean sidebar error; an absent preview URL renders the "프로토타입 빌드 대기 중" placeholder. Typed `ApiError` from the client. No blank panes, no unhandled throws.
- **Carry Plan A's `useAsync` stale-data-on-reload awareness** (it keeps previous `data` while a new fetch is in flight) and **the `await act(async () => render())` Suspense-test pattern** (Plan B) for the App-Router page whose test `params` is a plain `Promise.resolve(...)`.
- Auth remains the Plan A `getAuthToken()` placeholder; no new auth here.

**EXPLICITLY OUT OF SCOPE (→ Plan C2, and later plans):**
- Structured timeline cards: collapsed/expandable **question-answer summary card**, **contradiction/clarification card** (with option buttons), **approval-gate card**, **artifact card** (opens right panel). C1 renders user/AI/reasoning bubbles only.
- The **switchable right panel**: Living-Document (`react-markdown`) view, part tabs, and the **real `<iframe>` preview**. C1 renders only the `PreviewPanel` placeholder + iframe *when a (mocked) URL exists*, wired to the seam.
- Any **prototype build/publish backend** (see "Deferred backend contracts"): build status, build logs beyond the generic `/events` relay, `/preview/*` reverse-proxy, ECR/traefik publish. NOT invented here.
- Handoff/export screen, facilitator session-management screen, SSO beyond the token placeholder.

**Deferred backend contracts (this slice ASSUMES vs. DEFERS):**

*ASSUMED to exist today (used now, all verified in the backend):*
- `POST /projects/{pid}/message` (`routes/turns.py`) — synchronous turn; not used by C1's live path but part of the same relay.
- `GET /projects/{pid}/events?text=...` (`routes/turns.py`, SSE via `EventSourceResponse`) — **the live-turn transport C1 consumes** through `streamEvents`. Frames are JSON `AgentEvent` `{kind,text,path}` with `kind ∈ {message,file_changed,status,done,error}` (`sandbox/base.py`).
- `GET /projects/{pid}/state` (`routes/artifacts.py`) → `ProjectState` — feeds the sidebar.

*DEFERRED — do NOT invent; C1 degrades gracefully without them (blockers for a future "prototype build backend" plan, spec §2 `/preview/*` + §3 build→iterate→publish):*
1. **Prototype preview URL** — there is **no** backend route today that returns a running prototype's preview URL. C1's `previewUrl(projectId, prototypeId?)` seam returns `null` by default → the right pane shows the documented **"프로토타입 빌드 대기 중"** placeholder. When the build backend lands and sets `NEXT_PUBLIC_PREVIEW_BASE_URL` (or the seam is re-pointed at a `/preview/*` route), the same panel renders a live `<iframe>`. Unit-tested both ways with a mocked value.
2. **Build status / build-log endpoint** — there is **no** dedicated build-progress or build-log endpoint beyond the generic `/message` + `/events` SSE turn relay. C1 surfaces build/agent progress **only** as it arrives in that SSE stream (`status`/`file_changed` frames → the "추론 과정" reasoning trace; `message` frames → the AI bubble). No `/build/status` or `/build/logs` route is assumed or called.
3. **Prototype list / `prototypeId`** — there is **no** endpoint that lists prototypes or returns a `prototypeId`. The seam accepts an optional caller-supplied/placeholder id and never fabricates one.

---

## File Structure

```
frontend/
  app/projects/[projectId]/
    canvas/page.tsx               # Screen 04 (shell) — 3-pane canvas, SSE-driven chat
  components/canvas/
    CanvasSidebar.tsx             # left progress sidebar from ProjectState
    ChatTimeline.tsx              # maps ChatItem[] → bubbles (+ empty state)
    UserMessage.tsx               # violet right-aligned user bubble
    AiMessage.tsx                 # white left AI bubble (streaming indicator, error, trace)
    ReasoningTrace.tsx            # "추론 과정" <details> collapsible (status/file_changed)
    ChatInput.tsx                 # bottom textarea + send button (+ audit note)
    PreviewPanel.tsx              # right pane: iframe when URL, else deferred placeholder
  lib/
    useTurnStream.ts              # streamEvents consumer hook → ChatItem[] view-state
    api/preview.ts                # DEFERRED preview-URL seam (single typed owner)
  test/fixtures/
    agentEventStreams.ts          # AgentEvent[] sequences (normal turn, error turn)
  e2e/
    canvas.spec.ts                # INTEGRATION: live SSE turn against a real backend
```

Rationale: the canvas page is the only file that touches the API (via `useAsync` for `GET /state` and via `useTurnStream` for the SSE turn), keeping every component pure and prop-tested. `useTurnStream` isolates the sole piece of real logic (folding `AgentEvent` frames into UI state) so it is unit-tested directly with a fake `EventSource` — mirroring `lib/api/sse.test.ts`. `lib/api/preview.ts` is the single typed owner of the deferred preview URL, satisfying "no scattered string-building." `components/canvas/` groups the screen's presentational pieces for locality. `agentEventStreams.ts` gives every streaming test a realistic frame sequence.

---

### Task 1: Deferred preview-URL seam + streaming fixtures

**Files:**
- Create: `frontend/lib/api/preview.ts`
- Create: `frontend/test/fixtures/agentEventStreams.ts`
- Test: `frontend/lib/api/preview.test.ts`

**Interfaces:**
- `previewUrl(projectId: string, prototypeId?: string | null): string | null` — the **single typed seam** for the (deferred) prototype preview URL. Reads `process.env.NEXT_PUBLIC_PREVIEW_BASE_URL` at call time:
  - unset (the state today — no build backend) → returns `null` (caller shows the placeholder);
  - set (future build backend / a test mock) → returns `${base}/projects/{pid}/preview/{prototypeId ?? "default"}` with each path segment URL-encoded, no double slashes.
  This is the ONLY place a preview URL is constructed. Documented as "awaiting the Phase 2/3 build backend."
- `agentEventStreams.ts` produces two typed `AgentEvent[]` fixtures used by the streaming tests:
  - `normalTurn` — `status` → `file_changed` (path set) → two `message` frames (accumulate) → `done`.
  - `errorTurn` — `status` → `error` (text set) — the agent-reported failure path (distinct from a transport error).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/api/preview.test.ts
import { describe, it, expect, afterEach, vi } from "vitest";
import { previewUrl } from "./preview";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("previewUrl (deferred build-backend seam)", () => {
  it("returns null when no preview base URL is configured (the state today)", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "");
    expect(previewUrl("pilot1")).toBeNull();
    expect(previewUrl("pilot1", "proto-1")).toBeNull();
  });

  it("builds a preview URL from the configured base when the build backend is present", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com");
    expect(previewUrl("pilot1", "proto-1")).toBe(
      "https://preview.example.com/projects/pilot1/preview/proto-1",
    );
  });

  it("defaults the prototype id to 'default' and strips a trailing slash on the base", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com/");
    expect(previewUrl("pilot1")).toBe(
      "https://preview.example.com/projects/pilot1/preview/default",
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/preview.test.ts`
Expected: FAIL — `./preview` does not resolve.

- [ ] **Step 3: Write the seam + fixtures**

```ts
// frontend/lib/api/preview.ts
// DEFERRED BACKEND SEAM — the single typed owner of the prototype preview URL.
//
// There is NO backend route today that returns a running prototype's preview
// URL: the prototype build/preview/publish pipeline is spec Phase 2/3 and is
// NOT implemented (the generic POST /message + GET /events SSE relay is all
// that exists). Until a build backend lands and exposes a /preview/* reverse
// proxy (spec §2), this returns null and the canvas renders the
// "프로토타입 빌드 대기 중" placeholder. When the build backend is present it
// sets NEXT_PUBLIC_PREVIEW_BASE_URL (or this helper is re-pointed at the real
// route), and the SAME panel renders a live <iframe> — no other code changes.
//
// This is the ONLY place a preview URL is constructed (Global Constraint:
// no scattered string-building outside the API client / this seam).
export function previewUrl(projectId: string, prototypeId?: string | null): string | null {
  const base = (process.env.NEXT_PUBLIC_PREVIEW_BASE_URL ?? "").replace(/\/$/, "");
  if (base === "") return null; // no build backend configured — deferred state
  const pid = encodeURIComponent(projectId);
  const proto = encodeURIComponent(prototypeId ?? "default");
  return `${base}/projects/${pid}/preview/${proto}`;
}
```

```ts
// frontend/test/fixtures/agentEventStreams.ts
import type { AgentEvent } from "@/lib/api/types";

// Realistic SSE frame sequences (shape matches backend turns.py / sandbox base
// AgentEvent). During a prototype build/iterate turn the agent emits status +
// file_changed frames (surfaced as the "추론 과정" trace / build log) and
// message frames (the AI reply), terminated by a done frame.
export const normalTurn: AgentEvent[] = [
  { kind: "status", text: "요청을 분석하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "prototype/src/components/FilterBar.tsx" },
  { kind: "message", text: "기획전 필터 기능을 추가했습니다.", path: null },
  { kind: "message", text: " 우측 프리뷰에서 확인해 주세요.", path: null },
  { kind: "done", text: null, path: null },
];

// The agent-reported failure path (an "error"-KIND frame), distinct from a
// transport error. streamEvents dispatches this via onEvent AND then terminates
// the stream (onDone), so useTurnStream must handle kind==="error" in onEvent.
export const errorTurn: AgentEvent[] = [
  { kind: "status", text: "프로토타입 빌드를 시작합니다…", path: null },
  { kind: "error", text: "빌드에 실패했습니다: 의존성 설치 오류", path: null },
];
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/preview.test.ts && npx tsc --noEmit`
Expected: PASS (3 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/preview.ts frontend/lib/api/preview.test.ts frontend/test/fixtures/agentEventStreams.ts
git commit -m "feat(frontend): deferred preview-URL seam + agent-event stream fixtures"
```

---

### Task 2: `useTurnStream` hook — the live SSE chat consumer

**Files:**
- Create: `frontend/lib/useTurnStream.ts`
- Test: `frontend/lib/useTurnStream.test.tsx`

**Interfaces:**
- Exports UI view-state types (NOT backend contract types):
  - `TraceEntry = { kind: "status" | "file_changed"; text: string | null; path: string | null }`
  - `UserItem = { id: string; role: "user"; text: string }`
  - `AiItem = { id: string; role: "ai"; text: string; trace: TraceEntry[]; streaming: boolean; error: string | null }`
  - `ChatItem = UserItem | AiItem`
- `useTurnStream(projectId: string, initial?: ChatItem[])` → `{ items: ChatItem[]; streaming: boolean; send: (text: string) => void }`.
  - `send(text)`: no-op on empty text or while a turn is in flight (guarded by a live-stream ref, so no stale-closure double-send). Otherwise appends a `UserItem` + a streaming `AiItem`, then opens the SSE stream via the **existing** `streamEvents(projectId, text, handlers)` (Plan A). Frame folding into the current `AiItem` (by id, via functional `setState`):
    - `message` → append `ev.text` to `AiItem.text` (frames accumulate into one bubble);
    - `status` / `file_changed` → push a `TraceEntry` onto `AiItem.trace`;
    - `error` (agent-reported) → set `AiItem.error`;
    - `done` → handled by `streamEvents`' `onDone` (the helper closes the stream on `done`/`error` kinds).
  - `onDone` sets the `AiItem.streaming = false` and `streaming = false`; `onError` (transport/parse) sets a Korean connection-error line on the bubble and clears `streaming`.
  - Cleans up (`stop()`) on unmount via `useEffect`.
- Testing note: `EventSource` isn't in jsdom, so the test installs a minimal fake `EventSource` on `globalThis` (same technique as `lib/api/sse.test.ts`) and drives frames through `streamEvents`. State updates are wrapped in `act(...)`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/lib/useTurnStream.test.tsx
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTurnStream, type AiItem } from "./useTurnStream";
import { normalTurn, errorTurn } from "@/test/fixtures/agentEventStreams";
import type { AgentEvent } from "@/lib/api/types";

// Minimal fake EventSource (mirrors lib/api/sse.test.ts): records URL, lets the
// test push frames / trigger a transport error.
class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  close() {
    this.closed = true;
  }
  emit(obj: AgentEvent) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

const ai = (items: ReturnType<typeof useTurnStream>["items"]) =>
  items.filter((i): i is AiItem => i.role === "ai");

describe("useTurnStream", () => {
  it("appends a user bubble + a streaming AI bubble on send and opens the events stream", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("필터 기능 추가해줘"));
    expect(result.current.items[0]).toMatchObject({ role: "user", text: "필터 기능 추가해줘" });
    expect(result.current.items[1]).toMatchObject({ role: "ai", streaming: true });
    expect(result.current.streaming).toBe(true);
    expect(FakeEventSource.last!.url).toContain("/projects/pilot1/events?text=");
  });

  it("folds message frames into the AI bubble and trace frames into the reasoning trace, then finishes on done", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = FakeEventSource.last!;
    for (const frame of normalTurn) act(() => es.emit(frame));

    const last = ai(result.current.items)[0];
    expect(last.text).toBe("기획전 필터 기능을 추가했습니다. 우측 프리뷰에서 확인해 주세요.");
    expect(last.trace.map((t) => t.kind)).toEqual(["status", "file_changed"]);
    expect(last.trace[1].path).toBe("prototype/src/components/FilterBar.tsx");
    expect(last.streaming).toBe(false);
    expect(result.current.streaming).toBe(false);
    expect(es.closed).toBe(true);
  });

  it("surfaces an agent-reported error-kind frame on the AI bubble", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("build"));
    const es = FakeEventSource.last!;
    for (const frame of errorTurn) act(() => es.emit(frame));
    expect(ai(result.current.items)[0].error).toMatch(/빌드에 실패했습니다/);
    expect(result.current.streaming).toBe(false);
  });

  it("surfaces a transport error and ignores empty / concurrent sends", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("   ")); // empty after trim → ignored
    expect(result.current.items).toHaveLength(0);

    act(() => result.current.send("go"));
    act(() => result.current.send("두 번째")); // in-flight → ignored
    expect(result.current.items.filter((i) => i.role === "user")).toHaveLength(1);

    act(() => FakeEventSource.last!.fail());
    expect(ai(result.current.items)[0].error).toMatch(/연결/);
    expect(result.current.streaming).toBe(false);
  });

  it("closes the stream if the component unmounts mid-turn", () => {
    const { result, unmount } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = FakeEventSource.last!;
    act(() => es.emit({ kind: "status", text: "진행 중…", path: null })); // stream still live, not done/error
    expect(es.closed).toBe(false);

    unmount();

    expect(es.closed).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/useTurnStream.test.tsx`
Expected: FAIL — `./useTurnStream` does not resolve.

- [ ] **Step 3: Write the hook**

```ts
// frontend/lib/useTurnStream.ts
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
export type ChatItem = UserItem | AiItem;

let counter = 0;
const nextId = () => `item-${counter++}`;

export interface TurnStream {
  items: ChatItem[];
  streaming: boolean;
  send: (text: string) => void;
}

// Drives one live agent turn at a time over the EXISTING GET /events SSE
// (Plan A's streamEvents). status/file_changed frames become the AI bubble's
// "추론 과정" trace; message frames accumulate into its text; an error-KIND
// frame sets its error; done/transport-close finish the turn.
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
Expected: PASS (5 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/useTurnStream.ts frontend/lib/useTurnStream.test.tsx
git commit -m "feat(frontend): useTurnStream — live SSE chat consumer (first streamEvents user)"
```

---

### Task 3: Chat-bubble + reasoning-trace + input components

**Files:**
- Create: `frontend/components/canvas/UserMessage.tsx`
- Create: `frontend/components/canvas/ReasoningTrace.tsx`
- Create: `frontend/components/canvas/AiMessage.tsx`
- Create: `frontend/components/canvas/ChatInput.tsx`
- Create: `frontend/components/canvas/ChatTimeline.tsx`
- Test: `frontend/components/canvas/AiMessage.test.tsx`
- Test: `frontend/components/canvas/ChatInput.test.tsx`
- Test: `frontend/components/canvas/ChatTimeline.test.tsx`

**Interfaces:** (all presentational, prop-driven; ported from `files/ui/04-conversational-canvas.html`)
- `UserMessage({ text })` — the right-aligned violet bubble (`bg-violet-600 text-white rounded-2xl rounded-br-md`).
- `ReasoningTrace({ entries }: { entries: TraceEntry[] })` — a `<details>` collapsible titled **"추론 과정"** (mockup's collapsible reasoning idiom), listing each entry: `file_changed` → `📝 파일 변경: {path}`, `status` → its `text`. Renders nothing when `entries` is empty.
- `AiMessage({ item }: { item: AiItem })` — the AI avatar (`AI` chip) + white left bubble (`rounded-tl-md`). Renders `item.text`; when `item.streaming && item.text === ""` shows a Korean typing hint ("AI가 작성 중…") with `aria-live="polite"`; renders `<ReasoningTrace entries={item.trace} />` below the text when non-empty; renders `item.error` as a rose error line when set.
- `ChatInput({ onSend, disabled }: { onSend: (text: string) => void; disabled: boolean })` — the bottom composer ported from mockup: a `<textarea>` (`aria-label="채팅 메시지 입력"`, placeholder "메시지를 입력하세요… (질문·수정요청·되돌아가기 무엇이든)") + a send button (`aria-label="전송"`). Submits on the send button or Enter (without Shift); clears the textarea; no-ops on empty or while `disabled`. Renders the mockup's audit footer note ("모든 입력은 원문 그대로 audit.md에 기록됩니다 · 크리덴셜은 절대 기록되지 않습니다").
- `ChatTimeline({ items }: { items: ChatItem[] })` — maps `items` → `UserMessage`/`AiMessage`; renders a centered empty state ("대화를 시작해 보세요 — 아래에 메시지를 입력하세요.") when empty. Constrained to the mockup's `max-w-2xl mx-auto` column.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/AiMessage.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiMessage } from "./AiMessage";
import type { AiItem } from "@/lib/useTurnStream";

const base: AiItem = { id: "a1", role: "ai", text: "", trace: [], streaming: false, error: null };

describe("AiMessage", () => {
  it("renders the accumulated text and a reasoning trace", () => {
    render(
      <AiMessage
        item={{
          ...base,
          text: "필터를 추가했습니다.",
          trace: [
            { kind: "status", text: "분석 중…", path: null },
            { kind: "file_changed", text: null, path: "prototype/src/App.tsx" },
          ],
        }}
      />,
    );
    expect(screen.getByText("필터를 추가했습니다.")).toBeInTheDocument();
    expect(screen.getByText("추론 과정")).toBeInTheDocument();
    expect(screen.getByText(/prototype\/src\/App\.tsx/)).toBeInTheDocument();
  });

  it("shows a typing hint while streaming with no text yet", () => {
    render(<AiMessage item={{ ...base, streaming: true }} />);
    expect(screen.getByText(/작성 중/)).toBeInTheDocument();
  });

  it("shows an error line when the turn errored", () => {
    render(<AiMessage item={{ ...base, error: "빌드에 실패했습니다" }} />);
    expect(screen.getByText(/빌드에 실패했습니다/)).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/canvas/ChatInput.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInput } from "./ChatInput";

describe("ChatInput", () => {
  it("sends typed text and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByLabelText("채팅 메시지 입력");
    await user.type(box, "승인");
    await user.click(screen.getByRole("button", { name: "전송" }));
    expect(onSend).toHaveBeenCalledWith("승인");
    expect(box).toHaveValue("");
  });

  it("does not send while disabled", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={true} />);
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "안녕");
    await user.click(screen.getByRole("button", { name: "전송" }));
    expect(onSend).not.toHaveBeenCalled();
  });
});
```

```tsx
// frontend/components/canvas/ChatTimeline.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";

describe("ChatTimeline", () => {
  it("renders user and AI bubbles in order", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "필터 추가해줘" },
      { id: "a1", role: "ai", text: "추가했습니다.", trace: [], streaming: false, error: null },
    ];
    render(<ChatTimeline items={items} />);
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas`
Expected: FAIL — component imports do not resolve.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/canvas/UserMessage.tsx
export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] bg-violet-600 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/canvas/ReasoningTrace.tsx
import type { TraceEntry } from "@/lib/useTurnStream";

// Collapsible "추론 과정" (mockup 04's reasoning idiom). Also serves as the
// build-log surface: status frames are progress lines, file_changed frames are
// touched paths — all arriving over the existing /events SSE.
export function ReasoningTrace({ entries }: { entries: TraceEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px]">
      <summary className="cursor-pointer text-slate-500 font-medium">추론 과정</summary>
      <ul className="mt-1.5 space-y-1 text-slate-500">
        {entries.map((e, i) => (
          <li key={i} className="font-mono">
            {e.kind === "file_changed" ? `📝 파일 변경: ${e.path ?? ""}` : e.text}
          </li>
        ))}
      </ul>
    </details>
  );
}
```

```tsx
// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { ReasoningTrace } from "./ReasoningTrace";

export function AiMessage({ item }: { item: AiItem }) {
  return (
    <div className="flex gap-3">
      <span
        className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-bold"
        aria-hidden="true"
      >
        AI
      </span>
      <div className="max-w-[85%] min-w-0">
        <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-md px-4 py-3 text-sm leading-relaxed">
          {item.streaming && item.text === "" ? (
            <p className="text-slate-400" aria-live="polite">
              AI가 작성 중…
            </p>
          ) : (
            <p className="whitespace-pre-wrap">{item.text}</p>
          )}
          {item.error && <p className="mt-2 text-rose-600">{item.error}</p>}
        </div>
        <ReasoningTrace entries={item.trace} />
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/canvas/ChatInput.tsx
"use client";
import { useState } from "react";

export function ChatInput({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  function submit() {
    const trimmed = text.trim();
    if (disabled || trimmed === "") return;
    onSend(trimmed);
    setText("");
  }

  return (
    <div className="shrink-0 border-t border-slate-200 bg-white px-4 md:px-8 py-3">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white focus-within:ring-2 focus-within:ring-violet-400 px-4 py-2.5">
          <textarea
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="메시지를 입력하세요… (질문·수정요청·되돌아가기 무엇이든)"
            className="flex-1 resize-none text-sm focus:outline-none bg-transparent disabled:opacity-50"
            aria-label="채팅 메시지 입력"
            disabled={disabled}
          />
          <button
            type="button"
            onClick={submit}
            disabled={disabled || text.trim() === ""}
            className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white flex items-center justify-center"
            aria-label="전송"
          >
            ↑
          </button>
        </div>
        <p className="text-[10px] text-slate-400 mt-1.5 text-center">
          모든 입력은 원문 그대로 audit.md에 기록됩니다 · 크리덴셜은 절대 기록되지 않습니다
        </p>
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/canvas/ChatTimeline.tsx
import type { ChatItem } from "@/lib/useTurnStream";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";

export function ChatTimeline({ items }: { items: ChatItem[] }) {
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
          items.map((item) =>
            item.role === "user" ? (
              <UserMessage key={item.id} text={item.text} />
            ) : (
              <AiMessage key={item.id} item={item} />
            ),
          )
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas && npx tsc --noEmit`
Expected: PASS (AiMessage ×3, ChatInput ×2, ChatTimeline ×2 = 7 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/UserMessage.tsx frontend/components/canvas/ReasoningTrace.tsx \
  frontend/components/canvas/AiMessage.tsx frontend/components/canvas/ChatInput.tsx \
  frontend/components/canvas/ChatTimeline.tsx frontend/components/canvas/AiMessage.test.tsx \
  frontend/components/canvas/ChatInput.test.tsx frontend/components/canvas/ChatTimeline.test.tsx
git commit -m "feat(frontend): canvas chat bubbles, reasoning trace, input, timeline"
```

---

### Task 4: `CanvasSidebar` + `PreviewPanel` components

**Files:**
- Create: `frontend/components/canvas/CanvasSidebar.tsx`
- Create: `frontend/components/canvas/PreviewPanel.tsx`
- Test: `frontend/components/canvas/CanvasSidebar.test.tsx`
- Test: `frontend/components/canvas/PreviewPanel.test.tsx`

**Interfaces:** (presentational; sidebar ported from mockup 04's left `<aside>`, preview from the right `<aside>`)
- `CanvasSidebar({ state }: { state: ProjectState })` — the left progress rail: a "Discovery 진행" header, a progress bar sized by `progressPercent(state)` (Plan B), a "`{completed} / {total} 스테이지`" sub-label (+ `state.project_type` when present — NOT hardcoded "Path A"), and the stage list from `state.stages`: `completed` → emerald ✓ chip; `in_progress` → violet ● chip with `animate-pulse` + bold violet label + its `note` as a sub-line; `pending` → slate numbered chip, muted. Footer: mockup's adaptivity note ("워크플로우는 작업에 적응합니다. 채팅으로 되돌아가기·건너뛰기를 언제든 요청하세요."). **Stage names/notes come only from `state`.**
- `PreviewPanel({ projectId, prototypeId }: { projectId: string; prototypeId?: string | null })` — the right pane. Calls the `previewUrl` seam (Task 1). When it returns a URL → a titled panel ("프로토타입 프리뷰") with a full-height `<iframe title="프로토타입 프리뷰" src={url}>`. When it returns `null` (the deferred state today) → the documented placeholder: a 🛠️ icon + **"프로토타입 빌드 대기 중"** heading + explanatory Korean copy ("프로토타입 빌드 파이프라인이 준비되면 이곳에 실시간 프리뷰가 표시됩니다.") so the panel is never blank.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/canvas/CanvasSidebar.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CanvasSidebar } from "./CanvasSidebar";
import { projectState } from "@/test/fixtures/projectState";

describe("CanvasSidebar", () => {
  it("renders every stage name from the backend state (nothing hardcoded)", () => {
    render(<CanvasSidebar state={projectState} />);
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("shows the completed/total count from the fixture", () => {
    render(<CanvasSidebar state={projectState} />);
    // projectState fixture: 5 completed of 8 (see Plan B Task 1)
    expect(screen.getByText(/5 \/ 8 스테이지/)).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/canvas/PreviewPanel.test.tsx
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PreviewPanel } from "./PreviewPanel";

afterEach(() => vi.unstubAllEnvs());

describe("PreviewPanel", () => {
  it("renders the deferred-build placeholder when no preview URL is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "");
    render(<PreviewPanel projectId="pilot1" />);
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument();
    expect(screen.queryByTitle("프로토타입 프리뷰")).not.toBeInTheDocument();
  });

  it("renders an iframe pointed at the seam URL when a preview base is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com");
    render(<PreviewPanel projectId="pilot1" prototypeId="proto-1" />);
    const frame = screen.getByTitle("프로토타입 프리뷰") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(
      "https://preview.example.com/projects/pilot1/preview/proto-1",
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/canvas/CanvasSidebar.test.tsx components/canvas/PreviewPanel.test.tsx`
Expected: FAIL — component imports do not resolve.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/canvas/CanvasSidebar.tsx
import type { ProjectState, StageState } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";

function StageRow({ stage, index }: { stage: StageState; index: number }) {
  if (stage.status === "completed") {
    return (
      <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-500">
        <span
          className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-[10px]"
          aria-hidden="true"
        >
          ✓
        </span>
        {stage.name}
      </div>
    );
  }
  if (stage.status === "in_progress") {
    return (
      <div className="px-2.5 py-2 rounded-lg bg-violet-50 border border-violet-200">
        <div className="flex items-center gap-2.5">
          <span
            className="w-5 h-5 rounded-full bg-violet-600 text-white flex items-center justify-center text-[10px] font-bold animate-pulse"
            aria-hidden="true"
          >
            ●
          </span>
          <span className="font-bold text-violet-800">{stage.name}</span>
        </div>
        {stage.note && <p className="mt-1.5 ml-7 text-[11px] text-violet-600">{stage.note}</p>}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-400">
      <span
        className="w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center text-[10px]"
        aria-hidden="true"
      >
        {index + 1}
      </span>
      {stage.name}
    </div>
  );
}

export function CanvasSidebar({ state }: { state: ProjectState }) {
  const pct = progressPercent(state);
  const { completed, total } = stageCounts(state);
  return (
    <aside
      className="hidden lg:flex w-60 shrink-0 bg-white border-r border-slate-200 flex-col"
      aria-label="스테이지 진행 상황"
    >
      <div className="px-4 py-3 border-b border-slate-100">
        <p className="text-xs font-bold text-slate-400 uppercase tracking-wide">Discovery 진행</p>
        <div className="mt-2 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-slate-400 mt-1">
          {completed} / {total} 스테이지{state.project_type ? ` · ${state.project_type}` : ""}
        </p>
      </div>
      <nav className="flex-1 overflow-y-auto p-3 text-sm space-y-0.5">
        {state.stages.map((stage, i) => (
          <StageRow key={stage.name} stage={stage} index={i} />
        ))}
      </nav>
      <div className="p-3 border-t border-slate-100 text-[11px] text-slate-400 leading-relaxed">
        워크플로우는 작업에 적응합니다.
        <br />
        채팅으로 <b>되돌아가기·건너뛰기</b>를 언제든 요청하세요.
      </div>
    </aside>
  );
}
```

```tsx
// frontend/components/canvas/PreviewPanel.tsx
import { previewUrl } from "@/lib/api/preview";

// Right pane. The preview URL comes ONLY from the previewUrl seam (Task 1),
// which returns null until the Phase 2/3 prototype build backend exists —
// so today this renders the documented "프로토타입 빌드 대기 중" placeholder.
// When the build backend lands the same panel renders a live <iframe>.
export function PreviewPanel({
  projectId,
  prototypeId,
}: {
  projectId: string;
  prototypeId?: string | null;
}) {
  const url = previewUrl(projectId, prototypeId);
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label="프로토타입 프리뷰 패널"
    >
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
    </aside>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/canvas/CanvasSidebar.test.tsx components/canvas/PreviewPanel.test.tsx && npx tsc --noEmit`
Expected: PASS (CanvasSidebar ×2, PreviewPanel ×2 = 4 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/canvas/CanvasSidebar.tsx frontend/components/canvas/PreviewPanel.tsx \
  frontend/components/canvas/CanvasSidebar.test.tsx frontend/components/canvas/PreviewPanel.test.tsx
git commit -m "feat(frontend): canvas progress sidebar + deferred prototype-preview panel"
```

---

### Task 5: Canvas page (3-pane shell, SSE-driven chat) + AppHeader canvas tab

**Files:**
- Create: `frontend/app/projects/[projectId]/canvas/page.tsx`
- Modify: `frontend/components/AppHeader.tsx` (add the `"canvas"` tab)
- Test: `frontend/app/projects/[projectId]/canvas/page.test.tsx`
- Test (update): `frontend/components/AppHeader.test.tsx` (assert the new tab link)

**Interfaces:**
- `AppHeader` gains a `HeaderTab` value `"canvas"` and a nav tab "빌드 캔버스" linking to `${base}/canvas` (same pattern as the existing tabs; href-less when no `projectId`).
- The canvas page (`app/projects/[projectId]/canvas/page.tsx`) is a client component. It unwraps `params` with `use()` (Plan B Suspense pattern), loads `GET /state` via `useAsync`, and instantiates `useTurnStream(projectId)`. Layout ports the mockup's full-height 3-pane shell: `AppHeader` (activeTab `"canvas"`) on top, then `CanvasSidebar` (left, from `state.data`), a center `<main>` with `ChatTimeline` (scroll) + `ChatInput` (bottom, `disabled={streaming}`, `onSend={send}`), and `PreviewPanel` (right). Error/empty states: a `GET /state` error renders a Korean sidebar-load-error message in place of the sidebar (404 → "프로젝트를 찾을 수 없습니다.", other → generic load error); the chat and preview remain usable so the user can still start a turn.
- Page test drives the **SSE stream through the same fake-`EventSource`-on-`globalThis` technique** as `lib/api/sse.test.ts` / Task 2: mock `GET /state` (MSW), `await act`-render, type a message, click 전송, then push frames through `FakeEventSource.last` and assert the user bubble + streamed AI text + reasoning trace render into the timeline. Also asserts the deferred preview placeholder renders.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/AppHeader.test.tsx  (ADD this case to the existing file)
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppHeader } from "./AppHeader";

describe("AppHeader canvas tab", () => {
  it("links the 빌드 캔버스 tab into the project's canvas route", () => {
    render(<AppHeader activeTab="canvas" projectId="pilot1" />);
    const link = screen.getByRole("link", { name: "빌드 캔버스" });
    expect(link).toHaveAttribute("href", "/projects/pilot1/canvas");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
```

```tsx
// frontend/app/projects/[projectId]/canvas/page.test.tsx
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import { normalTurn } from "@/test/fixtures/agentEventStreams";
import type { AgentEvent } from "@/lib/api/types";
import CanvasPage from "./page";

// Fake EventSource (same technique as lib/api/sse.test.ts): the canvas page's
// useTurnStream opens a real streamEvents() call, which constructs this.
class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  close() {
    this.closed = true;
  }
  emit(obj: AgentEvent) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

const params = Promise.resolve({ projectId: "pilot1" });

describe("Canvas page", () => {
  it("renders the sidebar from GET /state and the deferred preview placeholder", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)));
    // use(params) suspends on first render (plain Promise.resolve params); the
    // act-wrap lets that Suspense retry flush before we query (Plan B pattern).
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    expect(await screen.findByText("Product Strategy")).toBeInTheDocument();
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument();
  });

  it("streams an agent turn into the timeline over SSE", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)));
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "필터 기능 추가해줘");
    await user.click(screen.getByRole("button", { name: "전송" }));

    // The user bubble appears immediately; the SSE URL was opened.
    expect(screen.getByText("필터 기능 추가해줘")).toBeInTheDocument();
    expect(FakeEventSource.last!.url).toContain("/projects/pilot1/events?text=");

    // Push the streamed frames; each state update is act-wrapped.
    const es = FakeEventSource.last!;
    for (const frame of normalTurn) {
      await act(async () => es.emit(frame));
    }

    expect(
      screen.getByText("기획전 필터 기능을 추가했습니다. 우측 프리뷰에서 확인해 주세요."),
    ).toBeInTheDocument();
    expect(screen.getByText("추론 과정")).toBeInTheDocument();
    expect(es.closed).toBe(true);
  });

  it("shows a not-found state on a 404 from GET /state", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ghost/state`, () =>
        HttpResponse.json({ detail: "unknown project" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<CanvasPage params={Promise.resolve({ projectId: "ghost" })} />);
    });
    expect(await screen.findByText(/프로젝트를 찾을 수 없습니다/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/canvas/page.test.tsx" components/AppHeader.test.tsx`
Expected: FAIL — `./page` does not resolve and `AppHeader` has no `"canvas"` tab.

- [ ] **Step 3: Write the implementation**

Add the canvas tab to `AppHeader` — extend the `HeaderTab` union and add one nav entry:

```tsx
// frontend/components/AppHeader.tsx  — CHANGE 1: the type
export type HeaderTab = "dashboard" | "questions" | "review" | "canvas" | "projects";
```

```tsx
// frontend/components/AppHeader.tsx  — CHANGE 2: inside the <nav>, after the "review" tab
            {tab("review", "문서 리뷰", `${base}/review`)}
            {tab("canvas", "빌드 캔버스", `${base}/canvas`)}
```

Then the page:

```tsx
// frontend/app/projects/[projectId]/canvas/page.tsx
"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CanvasSidebar } from "@/components/canvas/CanvasSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { PreviewPanel } from "@/components/canvas/PreviewPanel";
import { getState, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useTurnStream } from "@/lib/useTurnStream";

export default function CanvasPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const { items, streaming, send } = useTurnStream(projectId);

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
          <ChatTimeline items={items} />
          <ChatInput onSend={send} disabled={streaming} />
        </main>

        <PreviewPanel projectId={projectId} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/canvas/page.test.tsx" components/AppHeader.test.tsx && npx tsc --noEmit`
Expected: PASS (canvas page ×3, AppHeader canvas tab ×1, plus the pre-existing AppHeader tests still green); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/projects/[projectId]/canvas" frontend/components/AppHeader.tsx frontend/components/AppHeader.test.tsx
git commit -m "feat(frontend): conversational canvas page (3-pane shell, live SSE chat) + header tab"
```

---

### Task 6: Full suite, build, and INTEGRATION canvas e2e

**Files:**
- Create: `frontend/e2e/canvas.spec.ts`
- Test: full Vitest suite + `next build`

**Interfaces:**
- Produces an INTEGRATION Playwright spec (needs a live backend + a seeded project). It drives one live SSE turn end-to-end. Kept out of the unit path (Playwright is run only by `npm run test:e2e`, never by `vitest`).

- [ ] **Step 1: Write the e2e spec**

```ts
// frontend/e2e/canvas.spec.ts
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
  // agent turn over GET /events). We assert an AI avatar bubble materializes.
  await expect(page.getByText("프로토타입에 대해 알려줘")).toBeVisible();
  await expect(page.locator("text=AI").first()).toBeVisible();
});
```

- [ ] **Step 2: Run the full unit suite**

Run: `cd frontend && npm run test`
Expected: PASS — every Plan A + Plan B test, PLUS this plan's: preview seam ×3, useTurnStream ×4, chat components ×7, sidebar/preview ×4, canvas page ×3, AppHeader canvas tab ×1 = **22 new tests**. `e2e/` excluded from vitest.

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: `tsc` clean; `next build` succeeds and lists the new route `/projects/[projectId]/canvas` alongside the existing `/`, `/projects/[projectId]/dashboard`, `/projects/[projectId]/questions`, `/projects/[projectId]/review`.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/canvas.spec.ts
git commit -m "test(frontend): INTEGRATION canvas SSE e2e; canvas shell slice green"
```

---

## Deferred to Plan C2 (rich timeline cards + switchable preview panel)

C2 builds directly on C1's shell and hook, changing no contract. Planned scope:
- **Structured timeline cards** (ported from mockup 04, prop-driven, unit-tested against fixtures): collapsed/expandable **question-answer summary card** (the "제출됨" green summary with Q1:A chips), **contradiction/clarification card** (amber, option buttons → each posts the chosen answer as a turn), **approval-gate card** (violet gradient, ✓ 승인 / 수정 요청), **artifact card** (📕 button → opens the right panel). These map from `file_changed`/`message` frames + the workspace files (`GET /questions`, `GET /document`) to structured views; the frontend still renders whatever the backend produced (no contradiction/approval logic computed here).
- **Switchable right panel**: replace C1's `PreviewPanel` with a toggle between the **Living-Document view** (`react-markdown` + part tabs, reusing Plan B's `MarkdownView`) and the **real prototype `<iframe>`** — the iframe consuming the SAME `previewUrl` seam, so when the build backend lands the preview lights up with no further frontend change.
- Any card that needs backend data beyond `GET /state` + the SSE relay (e.g. a structured build-status object) remains **blocked on the deferred "prototype build backend" plan** and must degrade gracefully as C1 does.

---

## Self-Review

**Scope coverage (C1 — canvas shell + live SSE chat):**
- Left progress sidebar (mockup 04 `<aside>`, from `GET /state`) → Task 4 `CanvasSidebar` + Task 5 page wiring.
- Center chat timeline driven by live SSE turns (streamEvents' first real consumer; status/file_changed → 추론 과정 trace, message → AI bubble, done/error terminate) → Task 2 `useTurnStream` + Task 3 `ChatTimeline`/`AiMessage`/`ReasoningTrace`/`UserMessage`.
- Bottom chat input opening the SSE stream → Task 3 `ChatInput` + Task 5 page wiring (`onSend={send}`, `disabled={streaming}`).
- Right artifact panel with the deferred prototype-preview placeholder → Task 1 `previewUrl` seam + Task 4 `PreviewPanel` + Task 5 page.

**Every in-scope (C1) screen element mapped to a task:** sidebar → T4/T5; chat bubbles + trace → T3; input → T3/T5; SSE consumer → T2; preview placeholder + seam → T1/T4/T5; 3-pane page + header tab → T5; suite/build/e2e → T6. Structured cards + switchable Living-Document/iframe panel are explicitly deferred to C2 (documented above), which is the intentional plan split.

**DEFERRED backend contracts explicitly listed** (see "Deferred backend contracts"): (1) no prototype-preview-URL route → `previewUrl` seam returns `null` → "프로토타입 빌드 대기 중" placeholder; (2) no build-status/build-log endpoint → progress surfaced only via the existing `/events` SSE `status`/`file_changed` frames; (3) no prototype-list/`prototypeId` route → seam takes an optional placeholder id. ASSUMED-present contracts (`POST /message`, `GET /events?text=` SSE, `GET /state`, `AgentEvent`/`TurnResult`) are verified against `routes/turns.py`, `routes/artifacts.py`, and `sandbox/base.py`.

**Type consistency with backend + the SSE helper:** the hook and components import `AgentEvent`/`ProjectState`/`StageState` from `lib/api/types.ts` (which mirrors `sandbox/base.py`'s `AgentEvent{kind,text,path}` and `models.py`'s `ProjectState`) — no backend type redefined. `useTurnStream` calls `streamEvents(pid, text, { onEvent, onDone, onError })` with the EXACT Plan A signature (`lib/api/sse.ts`), including the helper's behavior that `error`-KIND frames arrive via `onEvent` then trigger `onDone` (handled: `onEvent` sets `AiItem.error`, `onDone` clears `streaming`). `ChatItem`/`TraceEntry` are UI view-state, deliberately local to the hook. `tsc --noEmit` is run in every task.

**Placeholder scan:** no TBD/TODO. The `previewUrl` "seam returns null" is a deliberately documented deferred state, not an unfinished stub, and is unit-tested both ways (null + mocked base). All component/hook/page code is shown in full.

**Constraint checks:** no methodology logic — the sidebar renders only `state.stages`; the timeline renders only streamed frames; `progressPercent`/`stageCounts` (Plan B) only count. `lib/api/client.ts` remains the sole HTTP owner, `lib/api/sse.ts` the sole SSE owner, and the one new URL (preview) is isolated in the single `lib/api/preview.ts` seam. Korean chrome ported verbatim from mockup 04 (input placeholder, audit note, sidebar labels, preview placeholder). Graceful states: transport/agent errors → Korean AI-bubble error line; 404/500 on `GET /state` → Korean sidebar messages; no preview URL → placeholder. `useAsync` stale-data awareness carried; the `await act(async () => render())` Suspense pattern used in the page test.

**Testing strategy realized:** the SSE consumer is unit-tested (`useTurnStream.test.tsx`) and the page is tested (`canvas/page.test.tsx`) via the SAME fake-`EventSource`-on-`globalThis` technique as `lib/api/sse.test.ts` — install the fake, `send`, push `agentEventStreams` frames, assert they render into the timeline; `done` closes the stream. Preview: placeholder asserted when no URL, iframe `src` asserted when a mocked `NEXT_PUBLIC_PREVIEW_BASE_URL` is set. Playwright `canvas.spec.ts` is labelled INTEGRATION (AWS/backend-required) and excluded from the vitest path.

**Scope sized appropriately:** the full slice is split; this document (C1) is drafted fully at 6 tasks (comparable to a well-sized plan half), and C2 (structured cards + switchable Living-Document/iframe panel) is scoped as the follow-up. C1 renders and is fully functional (SSE chat + graceful deferred preview) independently of C2.

**Depends on:** Plan A merged (scaffold, client, types, `streamEvents`, `useAsync`, `AppHeader`, MSW harness) + Plan B merged (`lib/stageProgress.ts`, `test/fixtures/projectState.ts`) + backend Phase 1 / API Completion merged before running against a real backend / e2e. Unit tests mock `GET /state` (MSW) and the SSE stream (fake `EventSource`). Does NOT depend on MicroVM Part 2 — the canvas talks to the `/message` + `/events` API/SSE contract, which works against `LocalSandbox` too.

**Blocker for full slice completion:** the prototype preview iframe and any build-specific status remain blocked on a future **"prototype build backend"** plan (spec Phase 2/3: `/preview/*` reverse proxy + build pipeline). C1 is designed to degrade gracefully without it and to light up with no frontend change once the seam is pointed at a real route.
