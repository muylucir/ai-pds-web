# Pathfinder Frontend A — Scaffold + Typed API Client + Project List/Create

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Next.js (App Router) + React + TypeScript + Tailwind frontend under a new `frontend/` tree, with (1) a Tailwind theme matching the mockups (Noto Sans KR, violet/slate), (2) a single typed API-client module that owns the base URL, every backend fetch call, and TypeScript types that mirror the backend Pydantic models exactly, (3) an SSE helper plus a synchronous message helper (with a documented decision on which the document-review screen uses), and (4) the first screen — Project List / Create — backed by `GET /projects` and `POST /projects`. This plan is the foundation the Discovery screens (Plan B) build on.

**Architecture:** The frontend is a thin client over the FastAPI backend defined in the Phase 1 + API Completion plans. It **renders whatever the backend returns and posts user input back** — it contains no methodology logic (no hardcoded stage lists, no question wording). One module tree, `frontend/lib/api/`, owns the base URL, the fetch functions, and the types; nothing else in the app constructs a URL or calls `fetch` directly. Screens are client components (`"use client"`) that call the API client and render **presentational** components which take plain props — so presentational components are unit-tested against pilot1-derived fixtures, the API client is tested against a mocked network (MSW), and neither needs a live backend. A live-backend Playwright suite exists but is labelled INTEGRATION and kept out of the unit CI path.

**Tech Stack:**
- **Next.js 15 (App Router)**, **React 19**, **TypeScript 5.7**. App Router is the spec's stated stack (§"스택: Next.js 프론트엔드").
- **Tailwind CSS 3.4** (not v4): the mockups (`files/ui/01–03`) are written against Tailwind v3 utility semantics (arbitrary values like `w-[62%]`, `shadow-violet-100`, `ring-4`), and v3's `tailwind.config.ts` `content`/`theme.extend` model lets us reproduce the exact palette. Tailwind v4's config-in-CSS model would force re-deriving every class; v3 guarantees mockup parity. Pinned to avoid surprise majors.
- **next/font/google** for Noto Sans KR (the mockups `@import` it from Google Fonts; `next/font` self-hosts + avoids layout shift while keeping the same family).
- **Testing — Vitest 3 + @testing-library/react 16 + jsdom + MSW 2.** Vitest over Jest because it runs the project's native ESM/TS with almost no config (shares esbuild transform with the Vite ecosystem Next tooling already assumes), is materially faster, and its `vi.mock`/`vi.fn` API is ergonomic for the API-client tests. RTL drives component behavior the way a user does. **MSW** mocks the backend at the network boundary (intercepts real `fetch`), so the API client's request shaping (method, URL, JSON body, path encoding) and response typing are exercised for real rather than stubbed — this is the one dep that lets us test "does the client talk to the contract correctly" without a live backend. `jsdom` provides the DOM for RTL.
- **@playwright/test 1.49** for the INTEGRATION e2e suite (needs a live backend; excluded from unit CI).

**Global Constraints:**
- The frontend NEVER re-implements methodology logic. No hardcoded stage lists, question wording, or contradiction rules. It renders backend payloads (`ProjectState`, `QuestionFile`, `AuditEntry`, document markdown) and posts user input back.
- **Types mirror the backend models exactly, including field names.** The backend serializes Pydantic models as JSON with **snake_case** keys (`project_type`, `is_other`, `parse_ok`, `raw_markdown`, `user_input`, `ai_response`). The TS types therefore use snake_case too — no camelCase remapping layer — so a field rename in the backend surfaces as a TS error here. One module (`lib/api/types.ts`) owns them.
- Korean UI copy from the mockups is the source of truth for user-facing text. Static chrome copy (nav labels, banner headings) is ported verbatim; dynamic content is rendered from backend data.
- Graceful handling of `404`/`400`/`409` and of `parse_ok=false`. Every client call maps non-2xx to a typed `ApiError` carrying `status` + `detail`; screens render a Korean error state, never a blank page or an unhandled throw.
- **Auth is a project-token placeholder only.** The spec defers SSO (§5 "인증: … SSO는 이후 단계"). The client attaches an optional `X-Project-Token` header from a single `getAuthToken()` seam; today it returns `undefined` (no-op). SSO/session-token wiring slots into that one function later — no call site changes.
- This plan DEPENDS on Phase 1 (merged) + API Completion (must be merged before the frontend is RUN against a real backend). Unit tests mock the API, so drafting/impl proceed regardless.

---

## File Structure

```
frontend/
  package.json
  next.config.mjs
  tsconfig.json
  tailwind.config.ts
  postcss.config.mjs
  vitest.config.ts
  vitest.setup.ts
  playwright.config.ts            # INTEGRATION only
  .env.local.example
  next-env.d.ts                   # generated by next
  app/
    layout.tsx                    # root layout: Noto Sans KR, <AppHeader/>, slate bg
    globals.css                   # @tailwind + mockup keyframes/utilities
    page.tsx                      # Project List / Create screen (home)
  components/
    AppHeader.tsx                 # shared top nav (violet/slate), ported from mockups
  lib/
    api/
      types.ts                    # TS mirrors of backend Pydantic models
      client.ts                   # base URL + ApiError + all fetch functions
      sse.ts                      # EventSource-based streamEvents helper
    auth.ts                       # getAuthToken() placeholder seam
  test/
    msw/
      handlers.ts                 # default MSW handlers for the backend contract
      server.ts                   # setupServer(...) for node/vitest
  e2e/
    projects.spec.ts              # INTEGRATION: create + list against a live backend
```

Rationale: `lib/api/` is the single source of truth for the contract (types + calls + SSE), satisfying "one API-client module owns them." `components/` holds presentational, prop-driven React (unit-testable); `app/` holds the App-Router client pages that wire fetch → components. `test/msw/` centralizes the mocked backend so every client/screen test shares one contract definition. `e2e/` is physically separate and only run by the integration script.

---

### Task 1: Scaffold Next.js app, Tailwind theme, test harness, and shared header

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/next.config.mjs`
- Create: `frontend/tsconfig.json`
- Create: `frontend/postcss.config.mjs`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/.env.local.example`
- Create: `frontend/app/globals.css`
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/page.tsx` (temporary placeholder, replaced in Task 6)
- Create: `frontend/components/AppHeader.tsx`
- Test: `frontend/components/AppHeader.test.tsx`

**Interfaces:**
- Produces the runnable app shell and the Vitest+RTL+jsdom harness. `AppHeader({ activeTab })` renders the ported violet/slate top nav (Pathfinder logo, 대시보드 / 질문 답변 / 문서 리뷰 links, "Bedrock 연결됨" pill, 김PM avatar) with `activeTab: "dashboard" | "questions" | "review" | "projects"` controlling the highlighted link.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/AppHeader.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  it("renders the brand and the three Korean nav labels", () => {
    render(<AppHeader activeTab="dashboard" />);
    expect(screen.getByText("Pathfinder")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "질문 답변" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "문서 리뷰" })).toBeInTheDocument();
  });

  it("marks the active tab with aria-current", () => {
    render(<AppHeader activeTab="questions" />);
    expect(screen.getByRole("link", { name: "질문 답변" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "대시보드" })).not.toHaveAttribute("aria-current");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm install && npx vitest run components/AppHeader.test.tsx`
Expected: FAIL — `Failed to resolve import "./AppHeader"` (module does not exist yet).

- [ ] **Step 3: Write the implementation**

```json
// frontend/package.json
{
  "name": "pathfinder-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "next": "^15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.49.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/node": "^22.10.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.1",
    "msw": "^2.7.0",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.7.0",
    "vitest": "^3.0.5"
  }
}
```

```js
// frontend/next.config.mjs
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};
export default nextConfig;
```

```json
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "types": ["vitest/globals", "@testing-library/jest-dom"],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "e2e"]
}
```

```js
// frontend/postcss.config.mjs
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

```ts
// frontend/tailwind.config.ts
import type { Config } from "tailwindcss";

// Palette + font are lifted directly from files/ui/01–03 so ported components
// render pixel-faithfully. Violet is the primary; slate is the neutral. We rely
// on Tailwind's default violet/slate/emerald/amber/rose/sky scales (the mockups
// use them unmodified) and only pin the font family here.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-noto-sans-kr)", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
```

```ts
// frontend/vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Unit suite only — the live-backend Playwright specs live in e2e/ and are
    // run by `npm run test:e2e`, never by vitest.
    exclude: ["e2e/**", "node_modules/**"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
});
```

```ts
// frontend/vitest.setup.ts
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./test/msw/server";

// MSW: assert on unhandled requests so a test that forgets a handler fails loudly
// instead of hitting the network. Handlers are reset between tests so per-test
// overrides don't leak.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

```
// frontend/.env.local.example
# Base URL of the FastAPI backend (Phase 1 + API Completion). No trailing slash.
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

```css
/* frontend/app/globals.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  @apply bg-slate-50 text-slate-800;
}

/* Stage-timeline connector line (ported from files/ui/01-dashboard.html <style>). */
.stage-line::before {
  content: "";
  position: absolute;
  left: 19px;
  top: 40px;
  bottom: -8px;
  width: 2px;
  background: #e5e7eb;
}
.stage-line:last-child::before {
  display: none;
}

/* Rendered-document typography (ported from files/ui/03-document-review.html). */
.doc-content h1 { font-weight: 700; font-size: 1.25rem; margin: 1.25rem 0 0.5rem; }
.doc-content h2 { font-weight: 700; font-size: 1.1rem; margin: 1.25rem 0 0.5rem; }
.doc-content h3 { font-weight: 700; font-size: 1.05rem; margin: 1.5rem 0 0.5rem; }
.doc-content p { margin: 0.5rem 0; line-height: 1.7; }
.doc-content ul { list-style: disc; margin: 0.5rem 0; padding-left: 1.25rem; }
.doc-content table { border-collapse: collapse; margin: 0.75rem 0; font-size: 0.85rem; }
.doc-content th, .doc-content td { border: 1px solid #e2e8f0; padding: 0.4rem 0.6rem; }
```

```tsx
// frontend/app/layout.tsx
import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-noto-sans-kr",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pathfinder",
  description: "AI-PLC Discovery 웹 서비스",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className={notoSansKr.variable}>
      <body className="font-sans">{children}</body>
    </html>
  );
}
```

```tsx
// frontend/components/AppHeader.tsx
import Link from "next/link";

export type HeaderTab = "dashboard" | "questions" | "review" | "projects";

// Ported from the shared <header> in files/ui/01–03. `projectId` is optional so
// the project-list screen (no project chosen yet) can render the header with
// disabled/href-less per-project tabs. When a project is selected the tabs link
// into that project's routes.
export function AppHeader({
  activeTab,
  projectId,
}: {
  activeTab: HeaderTab;
  projectId?: string;
}) {
  const tab = (key: HeaderTab, label: string, href: string) => {
    const active = key === activeTab;
    const base = "px-3 py-2 rounded-lg text-sm";
    const cls = active
      ? `${base} bg-violet-50 text-violet-700 font-medium`
      : `${base} hover:bg-slate-100 text-slate-600`;
    return (
      <Link href={href} className={cls} aria-current={active ? "page" : undefined}>
        {label}
      </Link>
    );
  };

  const base = projectId ? `/projects/${projectId}` : "#";
  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Link href="/" className="flex items-center gap-2 font-bold text-lg text-violet-700">
            <span className="w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-sm font-bold">
              AI
            </span>
            Pathfinder
          </Link>
          <nav className="hidden md:flex items-center gap-1" aria-label="주요 메뉴">
            {tab("dashboard", "대시보드", `${base}/dashboard`)}
            {tab("questions", "질문 답변", `${base}/questions`)}
            {tab("review", "문서 리뷰", `${base}/review`)}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Bedrock 연결됨
          </span>
          <button
            className="w-9 h-9 rounded-full bg-violet-100 text-violet-700 font-bold text-sm"
            aria-label="사용자 메뉴"
          >
            김PM
          </button>
        </div>
      </div>
    </header>
  );
}
```

Also create the placeholder home + empty MSW server so the harness imports resolve (both are replaced/filled in later tasks; the empty handler array is intentional here):

```tsx
// frontend/app/page.tsx  (placeholder — replaced in Task 6)
export default function Home() {
  return <main className="max-w-7xl mx-auto px-6 py-8">Pathfinder</main>;
}
```

```ts
// frontend/test/msw/handlers.ts
import type { RequestHandler } from "msw";
// Default handlers are added per contract in Task 3. Start empty so vitest.setup
// can import a real (if empty) array today; Task 3 fills this in.
export const handlers: RequestHandler[] = [];
```

```ts
// frontend/test/msw/server.ts
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/AppHeader.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Verify the app builds**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: `tsc` prints nothing (no type errors); `next build` finishes with `✓ Compiled successfully` and lists the `/` route. (This confirms the scaffold is a runnable Next app, not just testable files.)

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/next.config.mjs frontend/tsconfig.json \
  frontend/postcss.config.mjs frontend/tailwind.config.ts frontend/vitest.config.ts \
  frontend/vitest.setup.ts frontend/.env.local.example frontend/app frontend/components \
  frontend/test/msw
git commit -m "feat(frontend): scaffold Next.js + Tailwind + Vitest harness and shared header"
```

---

### Task 2: API types mirroring the backend models

**Files:**
- Create: `frontend/lib/api/types.ts`
- Test: `frontend/lib/api/types.test.ts`

**Interfaces:**
- Produces TypeScript types that mirror the backend Pydantic models **field-for-field, snake_case included** (`backend/pathfinder/models.py` + `backend/pathfinder/sandbox/base.py`):
  - `QuestionOption { letter: string; text: string; is_other: boolean; recommended: boolean }`
  - `Question { number: number; category: string | null; text: string; options: QuestionOption[]; answer: string | null }`
  - `QuestionFile { name: string; preamble: string | null; questions: Question[]; parse_ok: boolean; raw_markdown: string | null }`
  - `StageStatus = "pending" | "in_progress" | "completed"` and `StageState { name: string; status: StageStatus; note: string | null }`
  - `ProjectState { project_type: string | null; current_stage: string | null; stages: StageState[] }`
  - `AuditEntry { index: number; timestamp: string; user_input: string; ai_response: string; context: string | null }`
  - `AgentEventKind = "message" | "file_changed" | "status" | "done" | "error"` and `AgentEvent { kind: AgentEventKind; text: string | null; path: string | null }`
  - `TurnResult { events: AgentEvent[] }`
  - `ProjectSummary { project_id: string; name: string | null }` (shape of each item in `GET /projects` → `{projects: ProjectSummary[]}`)
- The test is a compile-time + shape check: it constructs a literal of each type and asserts a couple of values, so a backend field rename (mirrored incorrectly) surfaces as a TS error under `tsc`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/api/types.test.ts
import { describe, it, expect } from "vitest";
import type {
  QuestionFile,
  ProjectState,
  AuditEntry,
  AgentEvent,
  TurnResult,
  ProjectSummary,
} from "./types";

describe("api types mirror the backend models", () => {
  it("QuestionFile carries snake_case parse_ok / raw_markdown and option is_other", () => {
    const qf: QuestionFile = {
      name: "strategy-questions.md",
      preamble: null,
      parse_ok: true,
      raw_markdown: null,
      questions: [
        {
          number: 1,
          category: "Positioning",
          text: "포지셔닝?",
          answer: "A",
          options: [
            { letter: "A", text: "Niche", is_other: false, recommended: true },
            { letter: "X", text: "Other", is_other: true, recommended: false },
          ],
        },
      ],
    };
    expect(qf.parse_ok).toBe(true);
    expect(qf.questions[0].options[0].recommended).toBe(true);
    expect(qf.questions[0].options[1].is_other).toBe(true);
  });

  it("ProjectState uses project_type/current_stage and the three stage statuses", () => {
    const st: ProjectState = {
      project_type: "Greenfield",
      current_stage: "Product Strategy",
      stages: [
        { name: "Workspace Detection", status: "completed", note: null },
        { name: "Product Strategy", status: "in_progress", note: null },
        { name: "Go-to-Market", status: "pending", note: null },
      ],
    };
    expect(st.stages.map((s) => s.status)).toEqual(["completed", "in_progress", "pending"]);
  });

  it("AuditEntry / AgentEvent / TurnResult / ProjectSummary shapes", () => {
    const e: AuditEntry = {
      index: 1,
      timestamp: "2026-07-04T00:00:00Z",
      user_input: "ai-plc를 시작하고 싶어",
      ai_response: "Starting…",
      context: "Session start",
    };
    const ev: AgentEvent = { kind: "done", text: null, path: null };
    const tr: TurnResult = { events: [ev] };
    const p: ProjectSummary = { project_id: "pilot1", name: "기획전 AI 어시스턴트" };
    expect(e.user_input).toContain("ai-plc");
    expect(tr.events[0].kind).toBe("done");
    expect(p.name).toContain("기획전");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/types.test.ts`
Expected: FAIL — `Failed to resolve import "./types"`.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/lib/api/types.ts
// These types mirror the backend Pydantic models EXACTLY, including snake_case
// field names, because the backend serializes JSON with those keys and the
// client does no key remapping. Sources:
//   backend/pathfinder/models.py       (QuestionOption, Question, QuestionFile,
//                                        StageState, ProjectState, AuditEntry)
//   backend/pathfinder/sandbox/base.py (AgentEvent, TurnResult)
//   API Completion plan                (GET /projects item shape)

export interface QuestionOption {
  letter: string;
  text: string;
  is_other: boolean;
  recommended: boolean;
}

export interface Question {
  number: number;
  category: string | null;
  text: string;
  options: QuestionOption[];
  answer: string | null;
}

export interface QuestionFile {
  name: string;
  preamble: string | null;
  questions: Question[];
  parse_ok: boolean;
  raw_markdown: string | null;
}

export type StageStatus = "pending" | "in_progress" | "completed";

export interface StageState {
  name: string;
  status: StageStatus;
  note: string | null;
}

export interface ProjectState {
  project_type: string | null;
  current_stage: string | null;
  stages: StageState[];
}

export interface AuditEntry {
  index: number;
  timestamp: string;
  user_input: string;
  ai_response: string;
  context: string | null;
}

export type AgentEventKind = "message" | "file_changed" | "status" | "done" | "error";

export interface AgentEvent {
  kind: AgentEventKind;
  text: string | null;
  path: string | null;
}

export interface TurnResult {
  events: AgentEvent[];
}

// GET /projects → { projects: ProjectSummary[] }; POST /projects → ProjectSummary.
export interface ProjectSummary {
  project_id: string;
  name: string | null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/types.test.ts && npx tsc --noEmit`
Expected: PASS (3 tests); `tsc` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/types.ts frontend/lib/api/types.test.ts
git commit -m "feat(frontend): API types mirroring backend Pydantic models (snake_case)"
```

---

### Task 3: Typed API client (fetch functions + ApiError) with MSW tests

**Files:**
- Create: `frontend/lib/auth.ts`
- Create: `frontend/lib/api/client.ts`
- Modify: `frontend/test/msw/handlers.ts` (fill in default contract handlers)
- Test: `frontend/lib/api/client.test.ts`

**Interfaces:**
- Consumes: `lib/api/types.ts`, `lib/auth.ts::getAuthToken()`.
- Produces the **only** module that constructs backend URLs / calls `fetch`:
  - `API_BASE_URL` — from `process.env.NEXT_PUBLIC_API_BASE_URL`, defaulting to `http://localhost:8000` (no trailing slash).
  - `class ApiError extends Error { status: number; detail: string }` — thrown for any non-2xx response, `detail` taken from the backend's `{detail: ...}` body when present.
  - Functions (all `async`, all return typed payloads, all throw `ApiError` on non-2xx):
    - `createProject(projectId: string, name?: string): Promise<ProjectSummary>` — `POST /projects` body `{project_id, name?}`. 409 → `ApiError(409)`.
    - `listProjects(): Promise<ProjectSummary[]>` — `GET /projects`, unwraps `{projects: [...]}`.
    - `getState(pid: string): Promise<ProjectState>` — `GET /projects/{pid}/state`.
    - `getAudit(pid: string): Promise<AuditEntry[]>` — `GET /projects/{pid}/audit`.
    - `getDocument(pid: string): Promise<string>` — `GET /projects/{pid}/document`, unwraps `{markdown}`.
    - `listQuestionFiles(pid: string): Promise<string[]>` — `GET /projects/{pid}/questions`, unwraps `{questions: [...]}`.
    - `getQuestionFile(pid: string, name: string): Promise<QuestionFile>` — `GET /projects/{pid}/questions/{name}`; `name` is a slash-bearing workspace path, encoded per-segment (so the backend `{name:path}` route receives it intact). 404 → `ApiError(404)`.
    - `putAnswers(pid: string, name: string, answers: Record<string, string>): Promise<QuestionFile>` — `PUT .../questions/{name}` body `{answers}`. 400 → `ApiError(400)`, 404 → `ApiError(404)`.
    - `listArtifacts(pid: string): Promise<string[]>` — `GET /projects/{pid}/artifacts`, unwraps `{artifacts: [...]}`.
    - `postMessage(pid: string, text: string): Promise<TurnResult>` — `POST /projects/{pid}/message` body `{text}`.
- Path encoding rule (documented in code): a `{name:path}` value like `aiplc-docs/discovery/product-strategy/strategy-questions.md` is joined from `encodeURIComponent` of each segment, preserving `/` separators — so `strategy-questions.md` isn't percent-mangled but a stray `?`/`#`/space in a segment is escaped.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/api/client.test.ts
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import {
  API_BASE_URL,
  ApiError,
  createProject,
  listProjects,
  getState,
  getDocument,
  listQuestionFiles,
  getQuestionFile,
  putAnswers,
  listArtifacts,
  postMessage,
} from "./client";

describe("api client request shaping + response typing", () => {
  it("createProject POSTs {project_id,name} and returns the summary", async () => {
    let seenBody: unknown;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ project_id: "p1", name: "기획전 AI 어시스턴트" });
      }),
    );
    const r = await createProject("p1", "기획전 AI 어시스턴트");
    expect(seenBody).toEqual({ project_id: "p1", name: "기획전 AI 어시스턴트" });
    expect(r).toEqual({ project_id: "p1", name: "기획전 AI 어시스턴트" });
  });

  it("createProject omits name when not given", async () => {
    let seenBody: any;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ project_id: "p2", name: null });
      }),
    );
    await createProject("p2");
    expect(seenBody).toEqual({ project_id: "p2" });
  });

  it("createProject maps 409 to ApiError(409)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ detail: "project exists" }, { status: 409 }),
      ),
    );
    await expect(createProject("dup")).rejects.toMatchObject({ status: 409, detail: "project exists" });
    await expect(createProject("dup")).rejects.toBeInstanceOf(ApiError);
  });

  it("listProjects unwraps {projects:[...]}", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ projects: [{ project_id: "a", name: "A" }, { project_id: "b", name: null }] }),
      ),
    );
    const r = await listProjects();
    expect(r.map((p) => p.project_id)).toEqual(["a", "b"]);
  });

  it("getState returns ProjectState", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, () =>
        HttpResponse.json({ project_type: "Greenfield", current_stage: "Product Strategy", stages: [] }),
      ),
    );
    expect((await getState("p1")).project_type).toBe("Greenfield");
  });

  it("getDocument unwraps {markdown}", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/document`, () => HttpResponse.json({ markdown: "# Doc" })),
    );
    expect(await getDocument("p1")).toBe("# Doc");
  });

  it("listQuestionFiles / listArtifacts unwrap their arrays", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions`, () =>
        HttpResponse.json({ questions: ["aiplc-docs/a-questions.md"] }),
      ),
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/audit.md"] }),
      ),
    );
    expect(await listQuestionFiles("p1")).toEqual(["aiplc-docs/a-questions.md"]);
    expect(await listArtifacts("p1")).toEqual(["aiplc-docs/audit.md"]);
  });

  it("getQuestionFile encodes a slash-bearing name path but keeps separators", async () => {
    const name = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions/${name}`, () =>
        HttpResponse.json({
          name: "strategy-questions.md",
          preamble: null,
          questions: [],
          parse_ok: true,
          raw_markdown: null,
        }),
      ),
    );
    const qf = await getQuestionFile("p1", name);
    expect(qf.parse_ok).toBe(true);
  });

  it("getQuestionFile maps 404 to ApiError(404)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/questions/missing.md`, () =>
        HttpResponse.json({ detail: "question file not found" }, { status: 404 }),
      ),
    );
    await expect(getQuestionFile("p1", "missing.md")).rejects.toMatchObject({ status: 404 });
  });

  it("putAnswers PUTs {answers} and returns reparsed QuestionFile; 400 → ApiError(400)", async () => {
    const name = "aiplc-docs/strategy-questions.md";
    let seenBody: unknown;
    server.use(
      http.put(`${API_BASE_URL}/projects/p1/questions/${name}`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({
          name: "strategy-questions.md",
          preamble: null,
          questions: [{ number: 1, category: null, text: "?", options: [], answer: "B" }],
          parse_ok: true,
          raw_markdown: null,
        });
      }),
    );
    const qf = await putAnswers("p1", name, { "1": "B" });
    expect(seenBody).toEqual({ answers: { "1": "B" } });
    expect(qf.questions[0].answer).toBe("B");

    server.use(
      http.put(`${API_BASE_URL}/projects/p1/questions/${name}`, () =>
        HttpResponse.json({ detail: "bad key" }, { status: 400 }),
      ),
    );
    await expect(putAnswers("p1", name, { "99": "A" })).rejects.toMatchObject({ status: 400 });
  });

  it("postMessage POSTs {text} and returns TurnResult", async () => {
    let seenBody: unknown;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/message`, async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ events: [{ kind: "message", text: "ok", path: null }, { kind: "done", text: null, path: null }] });
      }),
    );
    const tr = await postMessage("p1", "승인");
    expect(seenBody).toEqual({ text: "승인" });
    expect(tr.events.map((e) => e.kind)).toEqual(["message", "done"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/client.test.ts`
Expected: FAIL — `Failed to resolve import "./client"`.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/lib/auth.ts
// Auth placeholder seam. The spec defers SSO ("인증: … SSO는 이후 단계"); today
// this returns undefined so no auth header is sent. When session tokens / SSO
// land, return the token here and every client call picks it up automatically —
// no call-site changes.
export function getAuthToken(): string | undefined {
  return undefined;
}
```

```ts
// frontend/lib/api/client.ts
import { getAuthToken } from "@/lib/auth";
import type {
  AuditEntry,
  ProjectState,
  ProjectSummary,
  QuestionFile,
  TurnResult,
} from "./types";

// The ONE place the base URL lives. No trailing slash.
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Encode a {name:path} value segment-by-segment: escape each segment but keep
// the "/" separators so the backend's `{name:path}` route receives the full
// relative workspace path intact.
function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { "X-Project-Token": token } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  // 204/empty bodies aren't used by this contract; every 2xx here returns JSON.
  return (await res.json()) as T;
}

export async function createProject(projectId: string, name?: string): Promise<ProjectSummary> {
  const body: { project_id: string; name?: string } = { project_id: projectId };
  if (name !== undefined) body.name = name;
  return request<ProjectSummary>("/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const r = await request<{ projects: ProjectSummary[] }>("/projects");
  return r.projects;
}

export async function getState(pid: string): Promise<ProjectState> {
  return request<ProjectState>(`/projects/${encodeURIComponent(pid)}/state`);
}

export async function getAudit(pid: string): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(`/projects/${encodeURIComponent(pid)}/audit`);
}

export async function getDocument(pid: string): Promise<string> {
  const r = await request<{ markdown: string }>(`/projects/${encodeURIComponent(pid)}/document`);
  return r.markdown;
}

export async function listQuestionFiles(pid: string): Promise<string[]> {
  const r = await request<{ questions: string[] }>(`/projects/${encodeURIComponent(pid)}/questions`);
  return r.questions;
}

export async function getQuestionFile(pid: string, name: string): Promise<QuestionFile> {
  return request<QuestionFile>(`/projects/${encodeURIComponent(pid)}/questions/${encodePath(name)}`);
}

export async function putAnswers(
  pid: string,
  name: string,
  answers: Record<string, string>,
): Promise<QuestionFile> {
  return request<QuestionFile>(`/projects/${encodeURIComponent(pid)}/questions/${encodePath(name)}`, {
    method: "PUT",
    body: JSON.stringify({ answers }),
  });
}

export async function listArtifacts(pid: string): Promise<string[]> {
  const r = await request<{ artifacts: string[] }>(`/projects/${encodeURIComponent(pid)}/artifacts`);
  return r.artifacts;
}

export async function postMessage(pid: string, text: string): Promise<TurnResult> {
  return request<TurnResult>(`/projects/${encodeURIComponent(pid)}/message`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
```

```ts
// frontend/test/msw/handlers.ts
// Default "happy" handlers describing the backend contract. Individual tests
// override specific routes with server.use(...); these defaults keep a screen
// test from erroring on an unhandled request it doesn't care about.
import { http, HttpResponse } from "msw";
import { API_BASE_URL } from "@/lib/api/client";

export const handlers = [
  http.get(`${API_BASE_URL}/projects`, () => HttpResponse.json({ projects: [] })),
  http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
    const body = (await request.json()) as { project_id: string; name?: string };
    return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null });
  }),
];
```

Note: `handlers.ts` now imports `API_BASE_URL` from the client, so the mocked base URL and the real one can never drift.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/client.test.ts && npx tsc --noEmit`
Expected: PASS (all client tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/auth.ts frontend/lib/api/client.ts frontend/test/msw/handlers.ts frontend/lib/api/client.test.ts
git commit -m "feat(frontend): typed API client with ApiError and MSW contract tests"
```

---

### Task 4: SSE helper + document-review transport decision

**Files:**
- Create: `frontend/lib/api/sse.ts`
- Test: `frontend/lib/api/sse.test.ts`

**Interfaces:**
- Consumes: `API_BASE_URL` (client), `AgentEvent` (types).
- Produces `streamEvents(pid, text, handlers): () => void` — opens an `EventSource` against `GET /projects/{pid}/events?text=...`, parses each SSE frame's `data` as JSON into an `AgentEvent`, and dispatches:
  - `handlers.onEvent(ev: AgentEvent)` for every frame,
  - closes the stream and calls `handlers.onDone()` when an event with `kind === "done"` (or `"error"`) arrives, or on the browser `error` event,
  - returns an unsubscribe function that closes the `EventSource` (so a React effect can clean up).
- **Decision documented here (satisfies the plan's "decide sync POST vs SSE" requirement):** The **document-review** approve/revise round-trip (Plan B) uses the **synchronous `postMessage` (POST /message)** path, NOT SSE. Rationale: (a) approve/revise is a single request→response where the UI shows a spinner, then refetches `getDocument` + `getState` + `getAudit` to reflect the new gate state — there is no per-token UI to stream into in this slice; (b) sync POST is trivially testable with MSW and has no `EventSource` lifecycle to leak; (c) the Conversational Canvas (screen 04) — the screen that genuinely needs live token/log streaming — is explicitly OUT OF SCOPE, so SSE has no in-scope consumer yet. `streamEvents` is nonetheless built and unit-tested **now** so the canvas plan inherits a tested helper, and doc-review can be upgraded to SSE later (long revisions + a progress indicator) by swapping the one transport call, with no contract change.
- Testing note: `EventSource` isn't in jsdom, so the test installs a minimal fake `EventSource` on `globalThis` and drives frames through it.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/api/sse.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { API_BASE_URL } from "./client";
import { streamEvents } from "./sse";

// Minimal fake EventSource: records the URL, lets the test push data/error.
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
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

describe("streamEvents", () => {
  it("opens the events URL with the text query param", () => {
    streamEvents("p1", "안녕", { onEvent: () => {}, onDone: () => {} });
    expect(FakeEventSource.last!.url).toBe(`${API_BASE_URL}/projects/p1/events?text=${encodeURIComponent("안녕")}`);
  });

  it("dispatches each frame and finishes on a done event", () => {
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamEvents("p1", "go", { onEvent, onDone });
    const es = FakeEventSource.last!;
    es.emit({ kind: "status", text: "working", path: null });
    es.emit({ kind: "message", text: "ok", path: null });
    es.emit({ kind: "done", text: null, path: null });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onEvent).toHaveBeenNthCalledWith(1, { kind: "status", text: "working", path: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("unsubscribe closes the stream", () => {
    const stop = streamEvents("p1", "go", { onEvent: () => {}, onDone: () => {} });
    stop();
    expect(FakeEventSource.last!.closed).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/sse.test.ts`
Expected: FAIL — `Failed to resolve import "./sse"`.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/lib/api/sse.ts
import { API_BASE_URL } from "./client";
import type { AgentEvent } from "./types";

export interface StreamHandlers {
  onEvent: (ev: AgentEvent) => void;
  onDone: () => void;
  onError?: (err: unknown) => void;
}

// Opens GET /projects/{pid}/events?text=... as an SSE stream. Each frame's
// `data` is a JSON-encoded AgentEvent (matches backend turns.py). Finishes on a
// "done"/"error" event or a transport error, closing the EventSource. Returns an
// unsubscribe function for React effect cleanup.
//
// NOTE: In this slice SSE has no in-scope consumer — document-review uses the
// synchronous postMessage path (see Task 4 Interfaces). This helper exists for
// the Conversational Canvas plan (out of scope here) and as a future upgrade
// path for long doc revisions.
export function streamEvents(pid: string, text: string, handlers: StreamHandlers): () => void {
  const url = `${API_BASE_URL}/projects/${encodeURIComponent(pid)}/events?text=${encodeURIComponent(text)}`;
  const es = new EventSource(url);

  const close = () => es.close();

  es.onmessage = (ev: MessageEvent) => {
    let parsed: AgentEvent;
    try {
      parsed = JSON.parse(ev.data) as AgentEvent;
    } catch (err) {
      handlers.onError?.(err);
      return;
    }
    handlers.onEvent(parsed);
    if (parsed.kind === "done" || parsed.kind === "error") {
      close();
      handlers.onDone();
    }
  };

  es.onerror = (err) => {
    close();
    handlers.onError?.(err);
    handlers.onDone();
  };

  return close;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/sse.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/sse.ts frontend/lib/api/sse.test.ts
git commit -m "feat(frontend): SSE streamEvents helper (canvas-bound); doc-review uses sync POST"
```

---

### Task 5: `useAsync` data-loading hook

**Files:**
- Create: `frontend/lib/useAsync.ts`
- Test: `frontend/lib/useAsync.test.tsx`

**Interfaces:**
- Produces `useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: ApiError | Error | null; loading: boolean; reload: () => void }` — a tiny hook every screen uses to load API data with consistent loading/error/reload states, so no screen re-implements `useEffect`+`useState`+try/catch. Re-runs when `deps` change or `reload()` is called; ignores results from a stale run (guards against out-of-order resolution on rapid dep changes).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/lib/useAsync.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { useAsync } from "./useAsync";

function Probe({ fn }: { fn: () => Promise<string> }) {
  const { data, error, loading, reload } = useAsync(fn, []);
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="data">{data ?? ""}</span>
      <span data-testid="error">{error ? error.message : ""}</span>
      <button onClick={reload}>reload</button>
    </div>
  );
}

describe("useAsync", () => {
  it("goes loading → data", async () => {
    render(<Probe fn={async () => "hello"} />);
    expect(screen.getByTestId("loading").textContent).toBe("true");
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("hello"));
    expect(screen.getByTestId("loading").textContent).toBe("false");
  });

  it("captures errors", async () => {
    render(<Probe fn={async () => { throw new Error("boom"); }} />);
    await waitFor(() => expect(screen.getByTestId("error").textContent).toBe("boom"));
  });

  it("reload re-invokes fn", async () => {
    const fn = vi.fn(async () => "x");
    render(<Probe fn={fn} />);
    await waitFor(() => expect(screen.getByTestId("data").textContent).toBe("x"));
    await act(async () => {
      screen.getByText("reload").click();
    });
    await waitFor(() => expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/useAsync.test.tsx`
Expected: FAIL — `Failed to resolve import "./useAsync"`.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/lib/useAsync.ts
"use client";
import { useCallback, useEffect, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      // Ignore a stale run's result if deps changed / component unmounted.
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/useAsync.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/useAsync.ts frontend/lib/useAsync.test.tsx
git commit -m "feat(frontend): useAsync data-loading hook with stale-run guard"
```

---

### Task 6: Project List / Create screen

**Files:**
- Create: `frontend/components/ProjectList.tsx`
- Create: `frontend/components/CreateProjectForm.tsx`
- Modify: `frontend/app/page.tsx` (replace placeholder with the real screen)
- Test: `frontend/components/CreateProjectForm.test.tsx`
- Test: `frontend/app/page.test.tsx`

**Interfaces:**
- Consumes: `listProjects`, `createProject`, `ApiError`, `useAsync`, `AppHeader`.
- Produces the home screen (`/`): the workshop session opener from spec §5 ("프로젝트 목록/생성 — 워크숍 세션 개설"). It has no mockup HTML of its own (marked "신규" in the spec), so it uses the shared violet/slate chrome and matches the mockups' card/panel visual language.
  - `ProjectList({ projects })` — presentational: renders each `ProjectSummary` as a card linking to `/projects/{project_id}/dashboard`, showing the name (or the id when name is null) and the id as a sub-label. Empty state: "아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요."
  - `CreateProjectForm({ onCreated })` — presentational + local state: `project_id` (required) and `name` (optional) inputs and a "프로젝트 생성" button; on submit calls `createProject`, and on success calls `onCreated(summary)`; renders a Korean error under the form on `ApiError` (409 → "이미 존재하는 프로젝트 ID입니다.").
  - `app/page.tsx` — client component: `useAsync(listProjects, [])`, renders `<AppHeader activeTab="projects" />`, the create form (reloading the list on success), loading/error states, and `<ProjectList/>`.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/CreateProjectForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { CreateProjectForm } from "./CreateProjectForm";

describe("CreateProjectForm", () => {
  it("submits project_id + name and calls onCreated", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ project_id: body.project_id, name: body.name ?? null });
      }),
    );
    render(<CreateProjectForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "pilot2");
    await user.type(screen.getByLabelText("프로젝트 이름 (선택)"), "신규 세션");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(body).toEqual({ project_id: "pilot2", name: "신규 세션" });
    expect(onCreated).toHaveBeenCalledWith({ project_id: "pilot2", name: "신규 세션" });
  });

  it("shows a Korean conflict message on 409", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ detail: "project exists" }, { status: 409 }),
      ),
    );
    render(<CreateProjectForm onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText("프로젝트 ID"), "dup");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    expect(await screen.findByText("이미 존재하는 프로젝트 ID입니다.")).toBeInTheDocument();
  });
});
```

```tsx
// frontend/app/page.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import Home from "./page";

describe("Project list screen", () => {
  it("lists projects from GET /projects", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({
          projects: [
            { project_id: "pilot1", name: "기획전 AI 어시스턴트" },
            { project_id: "bare", name: null },
          ],
        }),
      ),
    );
    render(<Home />);
    expect(await screen.findByText("기획전 AI 어시스턴트")).toBeInTheDocument();
    // Name-less project falls back to showing its id as the title.
    expect(screen.getByText("bare")).toBeInTheDocument();
  });

  it("renders the empty state when there are no projects", async () => {
    server.use(http.get(`${API_BASE_URL}/projects`, () => HttpResponse.json({ projects: [] })));
    render(<Home />);
    expect(
      await screen.findByText(/아직 생성된 프로젝트가 없습니다/),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/CreateProjectForm.test.tsx app/page.test.tsx`
Expected: FAIL — imports for `./CreateProjectForm` / new `page` symbols do not resolve.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/components/ProjectList.tsx
import Link from "next/link";
import type { ProjectSummary } from "@/lib/api/types";

export function ProjectList({ projects }: { projects: ProjectSummary[] }) {
  if (projects.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }
  return (
    <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {projects.map((p) => (
        <li key={p.project_id}>
          <Link
            href={`/projects/${p.project_id}/dashboard`}
            className="block bg-white rounded-xl border border-slate-200 p-5 hover:border-violet-300 hover:shadow-sm transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="w-8 h-8 rounded-lg bg-violet-100 text-violet-700 flex items-center justify-center text-sm font-bold">
                🟣
              </span>
              <p className="font-bold truncate">{p.name ?? p.project_id}</p>
            </div>
            <p className="text-xs text-slate-400 mt-2">ID: {p.project_id}</p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
```

```tsx
// frontend/components/CreateProjectForm.tsx
"use client";
import { useState } from "react";
import { createProject, ApiError } from "@/lib/api/client";
import type { ProjectSummary } from "@/lib/api/types";

export function CreateProjectForm({ onCreated }: { onCreated: (p: ProjectSummary) => void }) {
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject(projectId.trim(), name.trim() || undefined);
      onCreated(created);
      setProjectId("");
      setName("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("이미 존재하는 프로젝트 ID입니다.");
      } else if (err instanceof ApiError) {
        setError(`프로젝트 생성에 실패했습니다. (${err.status})`);
      } else {
        setError("네트워크 오류로 프로젝트를 생성하지 못했습니다.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-xl border border-slate-200 p-5 mb-8 flex flex-col sm:flex-row sm:items-end gap-3"
    >
      <div className="flex-1">
        <label htmlFor="pid" className="block text-xs text-slate-500 mb-1">
          프로젝트 ID
        </label>
        <input
          id="pid"
          required
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          placeholder="예: pilot2"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <div className="flex-1">
        <label htmlFor="pname" className="block text-xs text-slate-500 mb-1">
          프로젝트 이름 (선택)
        </label>
        <input
          id="pname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="예: 기획전 AI 어시스턴트"
          className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
      </div>
      <button
        type="submit"
        disabled={submitting || projectId.trim() === ""}
        className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold"
      >
        프로젝트 생성
      </button>
      {error && <p className="text-sm text-rose-600 w-full sm:w-auto">{error}</p>}
    </form>
  );
}
```

```tsx
// frontend/app/page.tsx  (replaces the Task 1 placeholder)
"use client";
import { AppHeader } from "@/components/AppHeader";
import { CreateProjectForm } from "@/components/CreateProjectForm";
import { ProjectList } from "@/components/ProjectList";
import { listProjects } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function Home() {
  const { data, error, loading, reload } = useAsync(listProjects, []);
  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">프로젝트</h1>
          <p className="text-sm text-slate-500 mt-1">
            워크숍 세션을 개설하고 Discovery를 시작하세요.
          </p>
        </div>
        <CreateProjectForm onCreated={reload} />
        {loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {error && (
          <p className="text-sm text-rose-600">
            프로젝트 목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요.
          </p>
        )}
        {data && <ProjectList projects={data} />}
      </main>
    </>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/CreateProjectForm.test.tsx app/page.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ProjectList.tsx frontend/components/CreateProjectForm.tsx \
  frontend/app/page.tsx frontend/components/CreateProjectForm.test.tsx frontend/app/page.test.tsx
git commit -m "feat(frontend): project list/create screen backed by GET/POST /projects"
```

---

### Task 7: Full unit suite + INTEGRATION Playwright skeleton

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/projects.spec.ts`
- Test: full Vitest suite

**Interfaces:**
- Produces the labelled INTEGRATION e2e entry point (needs a live backend on `NEXT_PUBLIC_API_BASE_URL` and `npm run dev`), kept out of the unit path (`vitest.config.ts` already excludes `e2e/**`). This task also runs the whole unit suite green as the plan's exit gate.

- [ ] **Step 1: Write the Playwright config + spec**

```ts
// frontend/playwright.config.ts
import { defineConfig } from "@playwright/test";

// INTEGRATION ONLY — requires a live FastAPI backend (Phase 1 + API Completion
// merged & running) reachable at NEXT_PUBLIC_API_BASE_URL, plus `npm run dev`.
// Never run by the unit CI job; run explicitly with `npm run test:e2e`.
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000" },
  webServer: {
    command: "npm run dev",
    url: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
```

```ts
// frontend/e2e/projects.spec.ts
import { test, expect } from "@playwright/test";

// INTEGRATION: drives the real UI against a real backend. Creates a uniquely
// named project and verifies it appears in the list.
test("create a project and see it in the list", async ({ page }) => {
  const pid = `e2e-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("프로젝트 ID").fill(pid);
  await page.getByLabel("프로젝트 이름 (선택)").fill("E2E 세션");
  await page.getByRole("button", { name: "프로젝트 생성" }).click();
  await expect(page.getByText("E2E 세션")).toBeVisible();
});
```

- [ ] **Step 2: Run the full unit suite**

Run: `cd frontend && npm run test`
Expected: PASS — all Vitest tests from Tasks 1–6 (AppHeader ×2, types ×3, client ×~11, sse ×3, useAsync ×3, CreateProjectForm ×2, page ×2). The `e2e/` spec is excluded from this run.

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: `tsc` clean; `next build` succeeds (route `/` listed).

- [ ] **Step 4: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/projects.spec.ts
git commit -m "test(frontend): INTEGRATION Playwright skeleton for project create/list"
```

---

## Self-Review

**Scope coverage (Plan A — foundation):**
- Framework scaffold (Next.js App Router + React + TS + Tailwind v3, Noto Sans KR, violet/slate) → Task 1.
- Typed API client (one module owns base URL + every fetch call + snake_case types mirroring backend models) → Tasks 2 (types) + 3 (client). All wizard-slice endpoints wrapped: `POST/GET /projects`, `GET .../state|audit|document|questions|questions/{name}|artifacts`, `PUT .../questions/{name}`, `POST .../message`.
- SSE helper + sync-vs-SSE decision for doc-review → Task 4 (helper built + tested; decision = sync `postMessage` for doc-review, justified; SSE reserved for the out-of-scope canvas).
- Project list / create screen (GET/POST /projects) → Task 6.
- Shared header + data-loading hook (needed by every Plan B screen) → Tasks 1 + 5.

**Testing strategy realized:** Vitest + RTL + jsdom for components/hooks; MSW for client request-shaping/response-typing against the contract (no live backend); Playwright labelled INTEGRATION and excluded from the unit run (`vitest.config.ts` `exclude: ["e2e/**"]`). Justified dep-by-dep in Tech Stack.

**Type consistency with backend:** `types.ts` mirrors `backend/pathfinder/models.py` + `sandbox/base.py` field-for-field in snake_case (`parse_ok`, `raw_markdown`, `is_other`, `project_type`, `current_stage`, `user_input`, `ai_response`, `AgentEvent.kind` literals). `tsc --noEmit` is run in Tasks 1/2/3/7 to catch drift.

**Placeholder scan:** No TBD/TODO; every file shown in full; the Task 1 `page.tsx`/`handlers.ts` are explicitly labelled temporary and are actually replaced/filled in Tasks 6/3.

**Constraint checks:** No methodology logic anywhere (client just serializes JSON; no stage/question strings). One API-client module owns URL + calls + types. Korean chrome copy ported verbatim from mockups. 404/400/409 → typed `ApiError` + Korean error states. Auth is a single `getAuthToken()` no-op seam with the SSO slot documented.

**Depends on:** Phase 1 (merged) + API Completion (must be merged before the app runs against a real backend / before `npm run test:e2e`). Unit tests mock the API, so implementation can proceed now.

**This plan feeds Plan B** (`2026-07-17-pathfinder-frontend-b-dashboard-wizard-review.md`), which ports mockups 01/02/03 (dashboard, question wizard, document review) on top of this scaffold, client, hooks, and header.
