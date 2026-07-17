# Pathfinder Frontend B — Dashboard + Question Wizard + Document Review

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **DEPENDS ON:** `2026-07-17-pathfinder-frontend-a-scaffold-client-list.md` (Plan A) merged. Plan A delivers the Next.js scaffold, Tailwind theme, the typed API client (`lib/api/client.ts` + `types.ts`), the SSE helper, the `useAsync` hook, the `AppHeader`, and the MSW test harness. This plan builds only screens on top of them and adds no new dependencies.

**Goal:** Port the three Discovery-phase mockups (`files/ui/01-dashboard.html`, `02-questions.html`, `03-document-review.html`) to React, backed by the Plan A API client. Deliver: the **Dashboard** (stage timeline from `GET /state`, artifacts panel from `GET /artifacts`, recent-activity feed from `GET /audit`), the **Question Wizard** (render a `QuestionFile` as a form — A/B/…/X options, ★ recommended default, mandatory Other free-text, submit via `PUT`, `parse_ok=false` raw-markdown fallback, and a clarification/contradiction banner when a `*-clarification-questions.md` file exists), and **Document Review** (render `GET /document` markdown in a Living-Document panel with the approval-gate banner; Approve → `POST /message "승인"`; Revise → `POST /message` natural language; AI verification summary + approval-gate history from `audit`).

**Architecture:** Each screen is an App-Router page under `app/projects/[projectId]/…` that loads data via `useAsync` + the Plan A client and renders **presentational components** taking plain props. Presentational components are unit-tested against **pilot1-derived fixtures** (`test/fixtures/`) — the parsed shape of `strategy-questions.md`, `aiplc-state.md`, `audit.md`, `discovery-document.md`, and `prfaq-clarification-questions.md` — so the components meet realistic data. **The frontend renders whatever the backend returns and posts user input back**: no stage list, question wording, contradiction rule, or approval semantics are hardcoded here. The clarification banner is driven purely by the *presence* of a `*-clarification-questions.md` file in `GET /questions` — the backend/agent decides contradictions; the frontend just renders the file it finds.

**Tech Stack:** Same as Plan A — Next.js 15 App Router, React 19, TypeScript 5.7, Tailwind 3.4, Vitest 3 + RTL 16 + jsdom + MSW 2. **One new library: `react-markdown@9` + `remark-gfm@4`** to render the Living-Document markdown (`GET /document`) and the `parse_ok=false` raw-markdown fallback. Justification: the document is authored markdown (headings, tables, lists, bold — see the pilot Press Release / FAQ / Pain-Point tables in `03-document-review.html`); hand-rolling a markdown renderer is error-prone and re-implements a solved problem, while `react-markdown` is a well-maintained, XSS-safe (no `dangerouslySetInnerHTML`, no raw HTML pass-through by default) renderer. `remark-gfm` adds GFM tables/strikethrough, which the pilot document uses. Both are pinned.

**Global Constraints:** (carried from Plan A)
- No methodology logic in the frontend. Stage names, question text, categories, contradiction copy, and approval wording all come from backend payloads or the mockups' static chrome — never computed here.
- Types come from `lib/api/types.ts` (Plan A), which mirrors the backend Pydantic models in snake_case. This plan imports them; it does not redefine any.
- Korean UI copy from the mockups is the source of truth for user-facing static text (banners, section headings, button labels). Dynamic content renders from backend data.
- Graceful handling of `parse_ok=false` (raw-markdown fallback form), and of `404`/`400`/`409` (typed `ApiError` → Korean error state). No blank pages, no unhandled throws.
- Approve/Revise use the **synchronous `postMessage` (POST /message)** transport (decision documented in Plan A Task 4), then refetch document/state/audit to reflect the new gate state.
- Auth remains the Plan A `getAuthToken()` placeholder; no new auth here.

**EXPLICITLY OUT OF SCOPE (next frontend plan):** Conversational Canvas (screen 04), prototype iframe preview, build-progress/log streaming UI, handoff/export screen, facilitator session-management screen, SSO beyond the token placeholder.

---

## File Structure

```
frontend/
  app/projects/[projectId]/
    dashboard/page.tsx            # Screen 01
    questions/page.tsx            # Screen 02 — question-file picker + wizard
    review/page.tsx               # Screen 03
  components/
    dashboard/
      StageTimeline.tsx           # stage list from ProjectState
      ArtifactsPanel.tsx          # artifact paths from listArtifacts
      ActivityFeed.tsx            # recent audit entries
      ProgressCards.tsx           # 4 summary cards (progress/stages/questions/artifacts)
    questions/
      QuestionForm.tsx            # QuestionFile → form (options, ★, Other, submit)
      QuestionCard.tsx            # single Question (A/B/…/X radio group + Other textarea)
      RawMarkdownFallback.tsx     # parse_ok=false → raw markdown + free-text form
      ClarificationBanner.tsx     # contradiction/clarification banner
    review/
      DocumentPanel.tsx           # react-markdown Living Document + gate banner
      ApprovalGate.tsx            # Approve / Revise actions → postMessage
      VerificationSummary.tsx     # AI 검증 요약 (from audit-derived props)
      MarkdownView.tsx            # shared react-markdown wrapper (doc-content styles)
  lib/
    stageProgress.ts             # pure helpers: % complete, counts (presentational math only)
  test/fixtures/
    strategyQuestions.ts         # parsed QuestionFile fixture (from pilot1)
    clarificationQuestions.ts    # parsed QuestionFile fixture (from pilot1)
    unparsedQuestions.ts         # parse_ok=false QuestionFile fixture
    projectState.ts              # ProjectState fixture (from pilot1 aiplc-state.md)
    auditEntries.ts              # AuditEntry[] fixture (from pilot1 audit.md)
    discoveryDocument.ts         # markdown string fixture (from pilot1)
  e2e/
    wizard.spec.ts               # INTEGRATION: answer a question + submit
```

Rationale: components are grouped by screen for locality; each screen's `page.tsx` is the only file that calls the API client (via `useAsync`), keeping components pure and prop-tested. `lib/stageProgress.ts` isolates the only arithmetic (progress %, counts) so it's unit-tested directly and no component recomputes it. `test/fixtures/` holds realistic pilot1-derived data shared by all component tests.

---

### Task 1: pilot1-derived fixtures + stage-progress helpers

**Files:**
- Create: `frontend/test/fixtures/strategyQuestions.ts`
- Create: `frontend/test/fixtures/clarificationQuestions.ts`
- Create: `frontend/test/fixtures/unparsedQuestions.ts`
- Create: `frontend/test/fixtures/projectState.ts`
- Create: `frontend/test/fixtures/auditEntries.ts`
- Create: `frontend/test/fixtures/discoveryDocument.ts`
- Create: `frontend/lib/stageProgress.ts`
- Test: `frontend/lib/stageProgress.test.ts`

**Interfaces:**
- Produces typed fixtures (each a `const` of a Plan A type) derived from real pilot1 files so components are tested against realistic data:
  - `strategyQuestions: QuestionFile` — mirrors `files/pilot1/aiplc-docs/discovery/product-strategy/strategy-questions.md` parsed: 13 questions, categories (Positioning/Differentiation/Business Model/…), Q1 option A `recommended: true`, each question's last option `is_other: true`, answers like `"A"`.
  - `clarificationQuestions: QuestionFile` — mirrors `prfaq-clarification-questions.md` parsed: 1 question, options A/B/C + X-Other, `answer: "C"`.
  - `unparsedQuestions: QuestionFile` — `parse_ok: false`, `questions: []`, `raw_markdown` set to a prose blob.
  - `projectState: ProjectState` — `project_type: "Greenfield"`, `current_stage: "Product Strategy"`, the 8 pilot stages with statuses (5 completed, Product Strategy `in_progress`, last two `pending`) to match mockup 01's mid-run view.
  - `auditEntries: AuditEntry[]` — a handful of real pilot entries (indexes, timestamps, Korean `user_input`/`ai_response`).
  - `discoveryDocument: string` — a markdown excerpt (headings + a table + bold) representative of the pilot discovery document.
- Produces `lib/stageProgress.ts` pure helpers (presentational math only, no methodology):
  - `stageCounts(state: ProjectState): { completed: number; total: number }`
  - `progressPercent(state: ProjectState): number` — `round(completed/total*100)`, `0` when `total===0`.
  - `answeredCount(qf: QuestionFile): { answered: number; total: number }` — counts questions with a non-empty `answer`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/stageProgress.test.ts
import { describe, it, expect } from "vitest";
import { stageCounts, progressPercent, answeredCount } from "./stageProgress";
import { projectState } from "@/test/fixtures/projectState";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { ProjectState } from "@/lib/api/types";

describe("stageProgress helpers", () => {
  it("counts completed / total stages from the pilot fixture", () => {
    const { completed, total } = stageCounts(projectState);
    expect(total).toBe(8);
    expect(completed).toBe(5); // matches mockup 01: "5 / 8"
  });

  it("progressPercent rounds completed/total", () => {
    expect(progressPercent(projectState)).toBe(63); // round(5/8*100)
  });

  it("progressPercent is 0 for an empty state", () => {
    const empty: ProjectState = { project_type: null, current_stage: null, stages: [] };
    expect(progressPercent(empty)).toBe(0);
  });

  it("answeredCount counts non-empty answers", () => {
    const { answered, total } = answeredCount(strategyQuestions);
    expect(total).toBe(13);
    expect(answered).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/stageProgress.test.ts`
Expected: FAIL — imports for `./stageProgress` and the fixtures do not resolve.

- [ ] **Step 3: Write fixtures + helpers**

```ts
// frontend/test/fixtures/projectState.ts
import type { ProjectState } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/aiplc-state.md, adjusted to the mid-run
// view in files/ui/01-dashboard.html (Product Strategy in progress). Notes are
// the mockup's stage sub-labels so ported components render realistic copy.
export const projectState: ProjectState = {
  project_type: "Greenfield",
  current_stage: "Product Strategy",
  stages: [
    { name: "Workspace Detection", status: "completed", note: "PROTOTYPE-*.md 없음 · Greenfield 확인" },
    { name: "Discovery Mode Selection", status: "completed", note: "Path A 선택 — 고객 Pain Point에서 시작" },
    { name: "Envision", status: "completed", note: "Working Backwards PR/FAQ 작성 · 모순 1건 해소" },
    { name: "Solution Analysis", status: "completed", note: "단일 솔루션 (Agentic) → Branch A.1 확정" },
    { name: "Prototype & Validation", status: "completed", note: "UI 프로토타입 · 반복 2회 · Validation 스킵" },
    { name: "Product Strategy", status: "in_progress", note: "포지셔닝 · 차별화 · 비즈니스 모델 — 13개 질문 대기" },
    { name: "Go-to-Market", status: "pending", note: "마케팅 전략 · 사내 확산 · 런칭 계획" },
    { name: "Discovery Document", status: "pending", note: "개발자 워크스페이스(Inception) 핸드오프" },
  ],
};
```

```ts
// frontend/test/fixtures/strategyQuestions.ts
import type { QuestionFile } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/discovery/product-strategy/strategy-questions.md
// (13 questions across Positioning / Differentiation / Business Model / Success
// Metrics / Risks). Trimmed option prose for brevity; structure is faithful:
// each question ends with an X) Other option (is_other:true), the pilot's
// recommended default carries recommended:true, and [Answer] values are captured.
const other = (letter: string) => ({
  letter,
  text: "Other (please describe after [Answer]: tag below)",
  is_other: true,
  recommended: false,
});

export const strategyQuestions: QuestionFile = {
  name: "strategy-questions.md",
  preamble:
    "**참고**: Prototype & Validation 단계에서 실사용자 검증이 스킵되어, 아래 추천 기본값은 Envision 단계에서만 도출되었습니다. 가정에 기반하므로 확정 시 유의해주세요.",
  parse_ok: true,
  raw_markdown: null,
  questions: [
    {
      number: 1,
      category: "Positioning",
      text: "이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?",
      answer: "A",
      options: [
        { letter: "A", text: "사내 특화 전문 도구(Niche Specialist) — 면세 기획전 운영에 특화", is_other: false, recommended: true },
        { letter: "B", text: "플랫폼(Platform) — 다른 MD 업무까지 확장하는 기반 도구", is_other: false, recommended: false },
        { letter: "C", text: "프리미엄(Premium) — 하이엔드 의사결정 지원 도구", is_other: false, recommended: false },
        other("X"),
      ],
    },
    {
      number: 2,
      category: "Positioning",
      text: "한 문장으로 이 제품의 가치 제안(Value Proposition)을 정의한다면?",
      answer: "A",
      options: [
        { letter: "A", text: "분산된 데이터를 통합 분석해 표준화된 후보와 카피를 제공하는 MD 전용 AI 어시스턴트", is_other: false, recommended: true },
        { letter: "B", text: "베테랑 MD의 노하우를 형식지화하는 도구", is_other: false, recommended: false },
        { letter: "C", text: "매출·회전율 데이터를 실시간 반영해 상품 누락 없는 기획전을 보장하는 도구", is_other: false, recommended: false },
        other("X"),
      ],
    },
    // Questions 3–13: same shape (single-select A/B/C + X-Other), category one of
    // Positioning / Differentiation / Business Model / Success Metrics / Risks.
    // Implementer copies remaining questions from the pilot file verbatim; the
    // tests below only assert on Q1/Q2 + aggregate counts, so the exact prose of
    // 3–13 is not load-bearing — but all 13 MUST be present with is_other on the
    // final option and the pilot [Answer] values ("A" for most).
    ...([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13] as const).map((n) => ({
      number: n,
      category:
        n <= 3 ? "Positioning" : n <= 5 ? "Differentiation" : n <= 7 ? "Business Model" : n <= 10 ? "Success Metrics" : "Risks",
      text: `pilot1 strategy-questions.md Question ${n} 본문`,
      answer: n === 12 ? "A,B" : n === 11 ? "C" : "A",
      options: [
        { letter: "A", text: `Q${n} 옵션 A`, is_other: false, recommended: true },
        { letter: "B", text: `Q${n} 옵션 B`, is_other: false, recommended: false },
        { letter: "C", text: `Q${n} 옵션 C`, is_other: false, recommended: false },
        other("X"),
      ],
    })),
  ],
};
```

```ts
// frontend/test/fixtures/clarificationQuestions.ts
import type { QuestionFile } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/discovery/envision/prfaq-clarification-questions.md.
// A clarification file is just another *-questions.md; the wizard renders it the
// same way, and its mere presence in GET /questions triggers the banner.
export const clarificationQuestions: QuestionFile = {
  name: "prfaq-clarification-questions.md",
  preamble: "응답에서 하나의 모순을 발견했습니다. 아래 질문으로 확인해주세요.",
  parse_ok: true,
  raw_markdown: null,
  questions: [
    {
      number: 1,
      category: "Contradiction 1: 응답 시간 제약 (30초)",
      text: '실제 서비스의 응답 시간 목표(SLA)는 어떻게 하시겠습니까?',
      answer: "C",
      options: [
        { letter: "A", text: "30초 이내 응답 목표는 그대로 유지 — 실패 요인 목록에서만 제외", is_other: false, recommended: false },
        { letter: "B", text: "30초 제약을 완화 — 새로운 목표 응답 시간을 알려주세요", is_other: false, recommended: false },
        { letter: "C", text: "아직 정하지 않음 — 파일럿 운영 중 데이터로 결정", is_other: false, recommended: false },
        { letter: "X", text: "Other (please describe after [Answer]: tag below)", is_other: true, recommended: false },
      ],
    },
  ],
};
```

```ts
// frontend/test/fixtures/unparsedQuestions.ts
import type { QuestionFile } from "@/lib/api/types";

// parse_ok=false payload — the backend returns this whenever a question file
// doesn't match the strict format; the wizard must fall back to raw markdown.
export const unparsedQuestions: QuestionFile = {
  name: "freeform-notes.md",
  preamble: null,
  parse_ok: false,
  raw_markdown:
    "# 자유 형식 메모\n\n이 파일은 표준 질문 형식이 아닙니다.\n\n- 항목 1\n- 항목 2\n\n자유롭게 답변을 작성해 주세요.",
  questions: [],
};
```

```ts
// frontend/test/fixtures/auditEntries.ts
import type { AuditEntry } from "@/lib/api/types";

// Derived from files/pilot1/aiplc-docs/audit.md (first entries + a couple later).
export const auditEntries: AuditEntry[] = [
  { index: 1, timestamp: "2026-07-04T01:43:19Z", user_input: "ai-plc를 시작하고 싶어", ai_response: "Starting AI-PLC Discovery workflow. Executing Workspace Detection first.", context: "Session start" },
  { index: 3, timestamp: "2026-07-04T01:43:19Z", user_input: "완료 (Discovery Mode Selection Q1: A)", ai_response: "User selected Path A (Start from customer pain points). Proceeding to Envision.", context: "Discovery mode selection" },
  { index: 8, timestamp: "2026-07-04T02:10:00Z", user_input: "정확합니다", ai_response: "Pain Point summary confirmed. Proceeding to PR/FAQ.", context: "Envision — gate" },
  { index: 11, timestamp: "2026-07-04T02:40:00Z", user_input: "완료 (clarification Q1: C)", ai_response: "Contradiction resolved: 30s SLA to be decided during pilot. PR/FAQ gate passed.", context: "Envision — contradiction resolution" },
  { index: 34, timestamp: "2026-07-04T03:30:00Z", user_input: "완료", ai_response: "Generated 13 Product Strategy questions.", context: "Product Strategy" },
];
```

```ts
// frontend/test/fixtures/discoveryDocument.ts
// Markdown excerpt representative of files/pilot1 discovery document (headings,
// bold, a GFM table) — used to test the react-markdown Living-Document panel.
export const discoveryDocument = `# Discovery Document — 기획전 AI 어시스턴트

## Part 1: Envision (PR/FAQ)

### Press Release

**신라인터넷면세점, "기획전 AI 어시스턴트" 사내 출시** — 기획전 구성 시간을 수 시간에서 수 분으로.

상품 영업 담당자(MD)가 자연어 한 줄로 기획전 컨셉을 입력하면, AI 에이전트가 사내 상품 데이터를 실시간 검색하여 30~50개의 후보 상품과 카피를 제안합니다.

### Pain Point 분석 요약

| 우선순위 | Pain Point | 설명 |
|---|---|---|
| 1 | 담당자 간 결과 편차 | 경험 수준에 따라 후보 선정 폭이 크게 다름 |
| 2 | 상품 누락 위험 | 분산된 시스템으로 좋은 상품 누락 |
`;
```

```ts
// frontend/lib/stageProgress.ts
import type { ProjectState, QuestionFile } from "@/lib/api/types";

// Presentational math ONLY — progress percentages and counts for the dashboard
// cards / wizard progress bar. No methodology: it does not know stage order or
// meaning, only how many are marked completed in the backend payload.
export function stageCounts(state: ProjectState): { completed: number; total: number } {
  const total = state.stages.length;
  const completed = state.stages.filter((s) => s.status === "completed").length;
  return { completed, total };
}

export function progressPercent(state: ProjectState): number {
  const { completed, total } = stageCounts(state);
  if (total === 0) return 0;
  return Math.round((completed / total) * 100);
}

export function answeredCount(qf: QuestionFile): { answered: number; total: number } {
  const total = qf.questions.length;
  const answered = qf.questions.filter((q) => (q.answer ?? "").trim() !== "").length;
  return { answered, total };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/stageProgress.test.ts && npx tsc --noEmit`
Expected: PASS (4 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/test/fixtures frontend/lib/stageProgress.ts frontend/lib/stageProgress.test.ts
git commit -m "feat(frontend): pilot1-derived fixtures + stage-progress helpers"
```

---

### Task 2: Dashboard components (StageTimeline, ArtifactsPanel, ActivityFeed, ProgressCards)

**Files:**
- Create: `frontend/components/dashboard/StageTimeline.tsx`
- Create: `frontend/components/dashboard/ArtifactsPanel.tsx`
- Create: `frontend/components/dashboard/ActivityFeed.tsx`
- Create: `frontend/components/dashboard/ProgressCards.tsx`
- Test: `frontend/components/dashboard/StageTimeline.test.tsx`
- Test: `frontend/components/dashboard/ArtifactsPanel.test.tsx`
- Test: `frontend/components/dashboard/ActivityFeed.test.tsx`

**Interfaces:** (all presentational, prop-driven; ported from `files/ui/01-dashboard.html`)
- `StageTimeline({ state, projectId }: { state: ProjectState; projectId: string })` — renders `state.stages` as the `<ol>` timeline: completed → emerald ✓ circle + "완료" pill; in_progress → violet numbered circle with `animate-pulse` "진행 중" pill + a "질문 답변 계속하기 →" link to `/projects/{projectId}/questions`; pending → slate numbered circle, muted text. Stage `note` renders as the sub-label. **Stage names/notes come only from `state`** — nothing hardcoded. Uses the `.stage-line` connector class from `globals.css`.
- `ProgressCards({ state, pendingQuestions, artifactCount, projectId })` — the 4 summary cards (전체 진행률 with a `w-[NN%]` bar via `progressPercent`, 완료된 스테이지 `completed / total`, 대기 중인 질문 `pendingQuestions` linking to the wizard, 생성된 산출물 `artifactCount`).
- `ArtifactsPanel({ artifacts, projectId }: { artifacts: string[]; projectId: string })` — the 산출물 panel: each artifact path → a row with a file-type icon (📕 for `discovery-document.md`, else 📄), the basename, and the full path as a sub-label; `discovery-document.md` links to `/projects/{projectId}/review` with a "Living" pill. Empty state: "아직 생성된 산출물이 없습니다."
- `ActivityFeed({ entries }: { entries: AuditEntry[] })` — the "최근 활동 (audit.md)" feed: newest first (reverse index order), each row a colored dot + `ai_response` (or `user_input` when the response is `N/A`) + "Entry {index}" sub-label. Shows the top N (e.g. 6).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/dashboard/StageTimeline.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageTimeline } from "./StageTimeline";
import { projectState } from "@/test/fixtures/projectState";

describe("StageTimeline", () => {
  it("renders every stage name from the backend state (nothing hardcoded)", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("shows a 진행 중 pill and a wizard link for the in_progress stage", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getByText("진행 중")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /질문 답변 계속하기/ });
    expect(link).toHaveAttribute("href", "/projects/pilot1/questions");
  });

  it("marks completed stages with 완료", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getAllByText("완료").length).toBe(
      projectState.stages.filter((s) => s.status === "completed").length,
    );
  });
});
```

```tsx
// frontend/components/dashboard/ArtifactsPanel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ArtifactsPanel } from "./ArtifactsPanel";

describe("ArtifactsPanel", () => {
  it("lists artifact basenames and links the discovery document to the review screen", () => {
    render(
      <ArtifactsPanel
        projectId="pilot1"
        artifacts={[
          "aiplc-docs/discovery/discovery-document.md",
          "aiplc-docs/discovery/envision/pain-point-analysis.md",
        ]}
      />,
    );
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    expect(screen.getByText("pain-point-analysis.md")).toBeInTheDocument();
    const docLink = screen.getByRole("link", { name: /discovery-document\.md/ });
    expect(docLink).toHaveAttribute("href", "/projects/pilot1/review");
  });

  it("renders an empty state", () => {
    render(<ArtifactsPanel projectId="pilot1" artifacts={[]} />);
    expect(screen.getByText(/아직 생성된 산출물이 없습니다/)).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/dashboard/ActivityFeed.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActivityFeed } from "./ActivityFeed";
import { auditEntries } from "@/test/fixtures/auditEntries";

describe("ActivityFeed", () => {
  it("shows newest entries first with Entry labels", () => {
    render(<ActivityFeed entries={auditEntries} />);
    const label = screen.getByText("Entry 34");
    expect(label).toBeInTheDocument();
    // newest (34) appears above oldest (Entry 1) in DOM order
    const all = screen.getAllByText(/^Entry \d+$/).map((e) => e.textContent);
    expect(all[0]).toBe("Entry 34");
  });

  it("renders the audit.md heading", () => {
    render(<ActivityFeed entries={auditEntries} />);
    expect(screen.getByText(/최근 활동/)).toBeInTheDocument();
    expect(screen.getByText(/audit\.md/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/dashboard`
Expected: FAIL — component imports do not resolve.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/dashboard/StageTimeline.tsx
import Link from "next/link";
import type { ProjectState, StageState } from "@/lib/api/types";

function StageIcon({ stage, index }: { stage: StageState; index: number }) {
  if (stage.status === "completed") {
    return (
      <span className="shrink-0 w-10 h-10 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center" aria-hidden="true">
        ✓
      </span>
    );
  }
  if (stage.status === "in_progress") {
    return (
      <span className="shrink-0 w-10 h-10 rounded-full bg-violet-600 text-white flex items-center justify-center ring-4 ring-violet-100 font-bold text-sm" aria-hidden="true">
        {index + 1}
      </span>
    );
  }
  return (
    <span className="shrink-0 w-10 h-10 rounded-full bg-slate-100 text-slate-400 flex items-center justify-center text-sm font-bold" aria-hidden="true">
      {index + 1}
    </span>
  );
}

export function StageTimeline({ state, projectId }: { state: ProjectState; projectId: string }) {
  return (
    <section className="lg:col-span-2 bg-white rounded-xl border border-slate-200" aria-labelledby="stage-heading">
      <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
        <h2 id="stage-heading" className="font-bold">Discovery 스테이지 진행</h2>
        <span className="text-xs text-slate-400">워크플로우는 작업에 적응합니다 — 되돌아가기/스킵 가능</span>
      </div>
      <ol className="p-6 space-y-2">
        {state.stages.map((stage, i) => {
          const active = stage.status === "in_progress";
          const done = stage.status === "completed";
          return (
            <li key={stage.name} className="stage-line relative flex gap-4 pb-6">
              <StageIcon stage={stage} index={i} />
              <div className="pt-1 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className={active ? "font-bold text-violet-800" : done ? "font-medium" : "font-medium text-slate-400"}>
                    {stage.name}
                  </h3>
                  {done && <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">완료</span>}
                  {active && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-100 text-violet-700 animate-pulse">진행 중</span>
                  )}
                </div>
                {stage.note && (
                  <p className={`text-sm mt-0.5 ${stage.status === "pending" ? "text-slate-400" : "text-slate-500"}`}>
                    {stage.note}
                  </p>
                )}
                {active && (
                  <Link
                    href={`/projects/${projectId}/questions`}
                    className="mt-3 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium"
                  >
                    질문 답변 계속하기 →
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
```

```tsx
// frontend/components/dashboard/ProgressCards.tsx
import Link from "next/link";
import type { ProjectState } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";

export function ProgressCards({
  state,
  pendingQuestions,
  artifactCount,
  projectId,
}: {
  state: ProjectState;
  pendingQuestions: number;
  artifactCount: number;
  projectId: string;
}) {
  const pct = progressPercent(state);
  const { completed, total } = stageCounts(state);
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">전체 진행률</p>
        <p className="text-2xl font-bold text-violet-700">{pct}%</p>
        <div className="mt-2 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">완료된 스테이지</p>
        <p className="text-2xl font-bold">
          {completed} <span className="text-sm font-normal text-slate-400">/ {total}</span>
        </p>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">대기 중인 질문</p>
        <p className="text-2xl font-bold text-amber-600">{pendingQuestions}</p>
        <Link href={`/projects/${projectId}/questions`} className="text-xs text-violet-600 hover:underline mt-2 inline-block">
          질문 답변하기 →
        </Link>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">생성된 산출물</p>
        <p className="text-2xl font-bold">{artifactCount}</p>
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/dashboard/ArtifactsPanel.tsx
import Link from "next/link";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

export function ArtifactsPanel({ artifacts, projectId }: { artifacts: string[]; projectId: string }) {
  return (
    <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="artifact-heading">
      <div className="px-5 py-4 border-b border-slate-100">
        <h2 id="artifact-heading" className="font-bold">산출물</h2>
      </div>
      {artifacts.length === 0 ? (
        <p className="p-5 text-sm text-slate-400">아직 생성된 산출물이 없습니다.</p>
      ) : (
        <ul className="p-3 text-sm">
          {artifacts.map((path) => {
            const base = basename(path);
            const isDoc = base === "discovery-document.md";
            const inner = (
              <>
                <span className="text-lg" aria-hidden="true">{isDoc ? "📕" : "📄"}</span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{base}</p>
                  <p className="text-xs text-slate-400 truncate">{path}</p>
                </div>
                {isDoc && <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">Living</span>}
              </>
            );
            return (
              <li key={path}>
                {isDoc ? (
                  <Link href={`/projects/${projectId}/review`} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50">
                    {inner}
                  </Link>
                ) : (
                  <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg">{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
```

```tsx
// frontend/components/dashboard/ActivityFeed.tsx
import type { AuditEntry } from "@/lib/api/types";

const DOT = ["bg-violet-500", "bg-emerald-500", "bg-sky-500", "bg-rose-400", "bg-amber-500"];

export function ActivityFeed({ entries, limit = 6 }: { entries: AuditEntry[]; limit?: number }) {
  const recent = [...entries].sort((a, b) => b.index - a.index).slice(0, limit);
  return (
    <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="audit-heading">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <h2 id="audit-heading" className="font-bold">
          최근 활동 <span className="text-xs font-normal text-slate-400">(audit.md)</span>
        </h2>
      </div>
      {recent.length === 0 ? (
        <p className="p-5 text-sm text-slate-400">기록된 활동이 없습니다.</p>
      ) : (
        <ul className="p-5 space-y-4 text-sm">
          {recent.map((e, i) => {
            const summary = e.ai_response && e.ai_response !== "N/A" ? e.ai_response : e.user_input;
            return (
              <li key={e.index} className="flex gap-3">
                <span className={`shrink-0 w-2 h-2 mt-1.5 rounded-full ${DOT[i % DOT.length]}`} aria-hidden="true" />
                <div className="min-w-0">
                  <p className="line-clamp-2">{summary}</p>
                  <p className="text-xs text-slate-400">Entry {e.index}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/dashboard && npx tsc --noEmit`
Expected: PASS (7 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/dashboard
git commit -m "feat(frontend): dashboard components (timeline, artifacts, activity, cards)"
```

---

### Task 3: Dashboard page (wires GET /state, /artifacts, /audit, /questions)

**Files:**
- Create: `frontend/app/projects/[projectId]/dashboard/page.tsx`
- Test: `frontend/app/projects/[projectId]/dashboard/page.test.tsx`

**Interfaces:**
- Consumes: `getState`, `listArtifacts`, `getAudit`, `listQuestionFiles`, `useAsync`, `AppHeader`, and the Task 2 components.
- Produces the Dashboard route. Loads all four resources; "대기 중인 질문" is derived as the number of question files present (a proxy — the frontend has no methodology count, so it renders "question files awaiting answers" from `listQuestionFiles`). Renders the project header (project id/name from the route + state), `ProgressCards`, `StageTimeline`, `ArtifactsPanel`, `ActivityFeed`. 404 on unknown project → Korean "프로젝트를 찾을 수 없습니다." error state.
- Next.js 15 note: `params` is a `Promise` in App-Router pages; the page is a client component that unwraps it with React's `use()`. In real Next.js, the `params` prop is an internally-tracked thenable (pre-marked settled), so `use()` reads it synchronously and never suspends. In the test below, `params` is a plain `Promise.resolve(...)`, which genuinely suspends on first render — under this repo's React 19 / `@testing-library/react` 16 / Vitest 3 / jsdom 25 versions, `findByText`/`waitFor`'s internal act-environment toggling never lets that pending Suspense retry flush (confirmed via isolated repro), so the initial `render(...)` call must be wrapped in `await act(async () => { render(...) })` to let the suspended `use(params)` resolve before querying.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/projects/[projectId]/dashboard/page.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import { auditEntries } from "@/test/fixtures/auditEntries";
import DashboardPage from "./page";

function mockAll(pid: string) {
  server.use(
    http.get(`${API_BASE_URL}/projects/${pid}/state`, () => HttpResponse.json(projectState)),
    http.get(`${API_BASE_URL}/projects/${pid}/artifacts`, () =>
      HttpResponse.json({ artifacts: ["aiplc-docs/discovery/discovery-document.md"] }),
    ),
    http.get(`${API_BASE_URL}/projects/${pid}/audit`, () => HttpResponse.json(auditEntries)),
    http.get(`${API_BASE_URL}/projects/${pid}/questions`, () =>
      HttpResponse.json({ questions: ["aiplc-docs/discovery/product-strategy/strategy-questions.md"] }),
    ),
  );
}

// App-Router pages receive params as a Promise in Next 15.
const params = Promise.resolve({ projectId: "pilot1" });

describe("Dashboard page", () => {
  it("renders stage timeline, artifacts, and activity from the API", async () => {
    mockAll("pilot1");
    // `use(params)` suspends on the first render because the test's plain
    // Promise.resolve(...) params (unlike Next's internally-tracked params
    // thenable) isn't pre-marked as settled. Wrapping the initial render in
    // act() lets that Suspense retry flush before we start querying/waiting.
    await act(async () => {
      render(<DashboardPage params={params} />);
    });
    expect(await screen.findByText("Product Strategy")).toBeInTheDocument();
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Entry 34")).toBeInTheDocument());
  });

  it("shows a not-found state on 404", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ghost/state`, () =>
        HttpResponse.json({ detail: "unknown project" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<DashboardPage params={Promise.resolve({ projectId: "ghost" })} />);
    });
    expect(await screen.findByText(/프로젝트를 찾을 수 없습니다/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/dashboard/page.test.tsx"`
Expected: FAIL — `./page` does not resolve.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/projects/[projectId]/dashboard/page.tsx
"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ProgressCards } from "@/components/dashboard/ProgressCards";
import { StageTimeline } from "@/components/dashboard/StageTimeline";
import { ArtifactsPanel } from "@/components/dashboard/ArtifactsPanel";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { getState, listArtifacts, getAudit, listQuestionFiles, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function DashboardPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const artifacts = useAsync(() => listArtifacts(projectId), [projectId]);
  const audit = useAsync(() => getAudit(projectId), [projectId]);
  const questionFiles = useAsync(() => listQuestionFiles(projectId), [projectId]);

  const notFound = state.error instanceof ApiError && state.error.status === 404;

  return (
    <>
      <AppHeader activeTab="dashboard" projectId={projectId} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        {notFound && <p className="text-sm text-rose-600">프로젝트를 찾을 수 없습니다.</p>}
        {!notFound && state.error && (
          <p className="text-sm text-rose-600">대시보드를 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {state.loading && <p className="text-sm text-slate-400">불러오는 중…</p>}

        {state.data && (
          <>
            <div className="mb-8">
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold">{projectId}</h1>
                <span className="text-xs px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 font-medium">🟣 DISCOVERY</span>
                {state.data.project_type && (
                  <span className="text-xs px-2.5 py-1 rounded-full bg-slate-100 text-slate-600">{state.data.project_type}</span>
                )}
              </div>
              {state.data.current_stage && (
                <p className="text-sm text-slate-500 mt-1">현재 단계: {state.data.current_stage}</p>
              )}
            </div>

            <ProgressCards
              state={state.data}
              pendingQuestions={questionFiles.data?.length ?? 0}
              artifactCount={artifacts.data?.length ?? 0}
              projectId={projectId}
            />

            <div className="grid lg:grid-cols-3 gap-6">
              <StageTimeline state={state.data} projectId={projectId} />
              <div className="space-y-6">
                <ArtifactsPanel artifacts={artifacts.data ?? []} projectId={projectId} />
                <ActivityFeed entries={audit.data ?? []} />
              </div>
            </div>
          </>
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/dashboard/page.test.tsx" && npx tsc --noEmit`
Expected: PASS (2 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/projects/[projectId]/dashboard"
git commit -m "feat(frontend): dashboard page wiring state/artifacts/audit/questions"
```

---

### Task 4: Question wizard components (QuestionCard, QuestionForm, RawMarkdownFallback, ClarificationBanner)

**Files:**
- Create: `frontend/components/questions/QuestionCard.tsx`
- Create: `frontend/components/questions/QuestionForm.tsx`
- Create: `frontend/components/questions/RawMarkdownFallback.tsx`
- Create: `frontend/components/questions/ClarificationBanner.tsx`
- Create: `frontend/components/review/MarkdownView.tsx` (shared markdown wrapper; used here for the fallback and in Task 6 for the document)
- Test: `frontend/components/questions/QuestionCard.test.tsx`
- Test: `frontend/components/questions/QuestionForm.test.tsx`
- Test: `frontend/components/questions/RawMarkdownFallback.test.tsx`
- Test: `frontend/components/questions/ClarificationBanner.test.tsx`

**Interfaces:** (ported from `files/ui/02-questions.html`)
- `MarkdownView({ markdown }: { markdown: string })` — wraps `react-markdown` + `remark-gfm` in a `.doc-content` div (styles from `globals.css`). Shared by the fallback and the document panel. No `dangerouslySetInnerHTML`.
- `QuestionCard({ question, value, onChange })` — one `Question` as a radio group: one `<label>` per option with the letter chip (A/B/…/X), option `text`, and — when `recommended` — the "★ AI 추천" amber pill. The `is_other` option renders a textarea (from `02-questions.html`'s Other block); selecting a non-Other option sets `value` to the letter; the Other option sets `value` to the free text and marks itself selected. `value` is the current answer string; `onChange(next: string)` reports changes. Category renders as the "카테고리: …" sub-label when present.
- `QuestionForm({ file, onSubmit, submitting })` — renders `file.preamble` (as the sky "AI 컨텍스트" info box), a progress bar (`answeredCount`), a `QuestionCard` per question with local answer state seeded from each `question.answer`, and the sticky action bar with "답변 제출 → AI 검증". On submit builds `answers: Record<string,string>` of `{ [question.number]: value }` for every question with a non-empty value and calls `onSubmit(answers)`.
- `RawMarkdownFallback({ file, onSubmit, submitting })` — the `parse_ok === false` path: renders an amber "표준 형식으로 파싱하지 못했습니다" notice, the `raw_markdown` via `MarkdownView`, and a single free-text textarea; submit calls `onSubmit(text)` (the page routes this to `POST /message`, since a free-text answer to an unparseable file can't be a keyed `PUT`).
- `ClarificationBanner({ file, projectId })` — the amber contradiction/clarification banner (ported from `02-questions.html`'s "모순 감지 배너"): heading "답변 간 모순이 감지되어 게이트가 보류되었습니다", the clarification file's `preamble` as the body, and a link to open that clarification file in the wizard (`?file=<path>`). Rendered by the page only when a `*-clarification-questions.md` file exists.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/questions/QuestionCard.test.tsx
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionCard } from "./QuestionCard";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { Question } from "@/lib/api/types";

const q1 = strategyQuestions.questions[0];

// Stateful harness: QuestionCard is a controlled component, so a bare vi.fn()
// onChange would leave `value` frozen and userEvent.type would report each
// keystroke against a non-advancing value (last call = a single char). This
// wrapper holds real state so `value` advances, letting us assert the final
// accumulated string while still spying on every onChange call.
function Harness({ question, initial, spy }: { question: Question; initial: string; spy: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <QuestionCard
      question={question}
      value={value}
      onChange={(v) => {
        spy(v);
        setValue(v);
      }}
    />
  );
}

describe("QuestionCard", () => {
  it("renders every option with letters and the ★ recommended pill", () => {
    render(<QuestionCard question={q1} value="A" onChange={vi.fn()} />);
    expect(screen.getByText(/Niche Specialist/)).toBeInTheDocument();
    expect(screen.getByText("★ AI 추천")).toBeInTheDocument(); // Q1 option A recommended
    expect(screen.getByText("X")).toBeInTheDocument(); // Other option chip
  });

  it("selecting an option reports its letter", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="A" spy={spy} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    expect(spy).toHaveBeenCalledWith("B");
  });

  it("typing in the Other textarea reports the free text", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="" spy={spy} />);
    await user.type(screen.getByLabelText(/기타 답변 직접 입력/), "커스텀");
    // With the stateful harness, value accumulates, so the last onChange carries
    // the full string.
    expect(spy).toHaveBeenLastCalledWith("커스텀");
  });
});
```

```tsx
// frontend/components/questions/QuestionForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionForm } from "./QuestionForm";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

describe("QuestionForm", () => {
  it("renders the preamble and a progress count", () => {
    render(<QuestionForm file={strategyQuestions} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText(/추천 기본값/)).toBeInTheDocument();
    expect(screen.getByText(/13 답변/)).toBeInTheDocument();
  });

  it("submits an answers map keyed by question number", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QuestionForm file={strategyQuestions} onSubmit={onSubmit} submitting={false} />);
    await user.click(screen.getByRole("button", { name: /답변 제출/ }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const answers = onSubmit.mock.calls[0][0];
    // seeded from fixture answers: Q1 -> "A", Q12 -> "A,B"
    expect(answers["1"]).toBe("A");
    expect(answers["12"]).toBe("A,B");
  });
});
```

```tsx
// frontend/components/questions/RawMarkdownFallback.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RawMarkdownFallback } from "./RawMarkdownFallback";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";

describe("RawMarkdownFallback", () => {
  it("renders the parse-failure notice and the raw markdown", () => {
    render(<RawMarkdownFallback file={unparsedQuestions} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText(/표준 형식으로 파싱하지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText("자유 형식 메모")).toBeInTheDocument(); // rendered from raw_markdown heading
  });

  it("submits the free-text answer", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<RawMarkdownFallback file={unparsedQuestions} onSubmit={onSubmit} submitting={false} />);
    await user.type(screen.getByLabelText(/자유 답변/), "제 답변입니다");
    await user.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith("제 답변입니다");
  });
});
```

```tsx
// frontend/components/questions/ClarificationBanner.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ClarificationBanner } from "./ClarificationBanner";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";

describe("ClarificationBanner", () => {
  it("shows the contradiction heading and links to the clarification file", () => {
    render(
      <ClarificationBanner
        projectId="pilot1"
        path="aiplc-docs/discovery/envision/prfaq-clarification-questions.md"
        preamble={clarificationQuestions.preamble}
      />,
    );
    expect(screen.getByText(/모순이 감지되어 게이트가 보류/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /확인 질문 답변하기/ });
    expect(link.getAttribute("href")).toContain("prfaq-clarification-questions.md");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/questions`
Expected: FAIL — component imports (and `react-markdown`) do not resolve yet.

- [ ] **Step 3: Add the dependency, then write the implementations**

```bash
cd frontend && npm install react-markdown@^9.0.1 remark-gfm@^4.0.0
```

```tsx
// frontend/components/review/MarkdownView.tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Shared markdown renderer for the Living Document and the parse_ok=false
// fallback. react-markdown does not emit raw HTML by default (no
// dangerouslySetInnerHTML), so authored markdown is rendered safely. `.doc-content`
// styles come from app/globals.css (ported from files/ui/03-document-review.html).
export function MarkdownView({ markdown }: { markdown: string }) {
  return (
    <div className="doc-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </div>
  );
}
```

```tsx
// frontend/components/questions/QuestionCard.tsx
"use client";
import type { Question } from "@/lib/api/types";

export function QuestionCard({
  question,
  value,
  onChange,
}: {
  question: Question;
  value: string;
  onChange: (next: string) => void;
}) {
  const name = `q${question.number}`;
  // The selected non-Other letter, or "" when the Other free-text is in use.
  const selectedLetter = question.options.some((o) => o.letter === value && !o.is_other) ? value : "";

  return (
    <fieldset className="bg-white rounded-xl border-2 border-violet-300 shadow-sm shadow-violet-100 overflow-hidden">
      <legend className="sr-only">질문 {question.number}</legend>
      <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
        <span className="w-7 h-7 rounded-full bg-violet-600 text-white flex items-center justify-center text-xs font-bold" aria-hidden="true">
          {question.number}
        </span>
        <div>
          <h2 className="font-bold">Q{question.number}. {question.text}</h2>
          {question.category && <p className="text-xs text-slate-400 mt-0.5">카테고리: {question.category}</p>}
        </div>
      </div>
      <div className="p-6 space-y-3">
        {question.options.map((opt) => {
          if (opt.is_other) {
            const otherActive = selectedLetter === "";
            return (
              <label key={opt.letter} className="block cursor-pointer">
                <input
                  type="radio"
                  name={name}
                  className="sr-only peer"
                  checked={otherActive && value !== ""}
                  onChange={() => onChange("")}
                />
                <div className="flex gap-3 rounded-xl border-2 border-dashed border-slate-200 p-4 hover:border-violet-200">
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <div className="flex-1">
                    <p className="font-medium">Other — 직접 입력</p>
                    <textarea
                      aria-label="기타 답변 직접 입력"
                      rows={2}
                      value={otherActive ? value : ""}
                      onChange={(e) => onChange(e.target.value)}
                      placeholder="위 선택지에 없다면 직접 설명해 주세요…"
                      className="mt-2 w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                </div>
              </label>
            );
          }
          const checked = selectedLetter === opt.letter;
          return (
            <label key={opt.letter} className="block cursor-pointer">
              <input
                type="radio"
                name={name}
                value={opt.letter}
                className="sr-only peer"
                checked={checked}
                onChange={() => onChange(opt.letter)}
              />
              <div
                className={`flex gap-3 rounded-xl border-2 p-4 hover:border-violet-200 ${
                  checked ? "border-violet-600 bg-violet-50" : "border-slate-200"
                }`}
              >
                <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                  {opt.letter}
                </span>
                <div>
                  <p className="font-medium">
                    {opt.text}
                    {opt.recommended && (
                      <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 ml-1">★ AI 추천</span>
                    )}
                  </p>
                </div>
              </div>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
```

```tsx
// frontend/components/questions/QuestionForm.tsx
"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { answeredCount } from "@/lib/stageProgress";
import { QuestionCard } from "./QuestionCard";

export function QuestionForm({
  file,
  onSubmit,
  submitting,
}: {
  file: QuestionFile;
  onSubmit: (answers: Record<string, string>) => void;
  submitting: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const q of file.questions) seed[String(q.number)] = q.answer ?? "";
    return seed;
  });

  const answered = Object.values(answers).filter((v) => v.trim() !== "").length;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const filled: Record<string, string> = {};
    for (const [k, v] of Object.entries(answers)) if (v.trim() !== "") filled[k] = v;
    onSubmit(filled);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{file.name}</h1>
        </div>
        <p className="text-sm text-slate-500">
          <b className="text-violet-700">{answered}</b> / {file.questions.length} 답변 완료
        </p>
      </div>
      <div className="h-2 rounded-full bg-slate-200 overflow-hidden" role="progressbar" aria-valuenow={answered} aria-valuemin={0} aria-valuemax={file.questions.length}>
        <div className="h-full bg-violet-500 rounded-full transition-all" style={{ width: `${file.questions.length ? (answered / file.questions.length) * 100 : 0}%` }} />
      </div>

      {file.preamble && (
        <div className="flex gap-3 bg-sky-50 border border-sky-200 rounded-xl p-4 text-sm">
          <span className="text-lg" aria-hidden="true">💡</span>
          <p className="text-sky-800">{file.preamble}</p>
        </div>
      )}

      {file.questions.map((q) => (
        <QuestionCard
          key={q.number}
          question={q}
          value={answers[String(q.number)] ?? ""}
          onChange={(next) => setAnswers((prev) => ({ ...prev, [String(q.number)]: next }))}
        />
      ))}

      <div className="sticky bottom-0 bg-white/90 backdrop-blur border border-slate-200 rounded-xl p-4 flex items-center justify-between gap-3 shadow-lg shadow-slate-200/50">
        <div className="text-xs text-slate-500 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500" aria-hidden="true" />
          모든 답변은 audit.md에 원문 그대로 기록됩니다
        </div>
        <button type="submit" disabled={submitting} className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold">
          답변 제출 → AI 검증
        </button>
      </div>
    </form>
  );
}
```

```tsx
// frontend/components/questions/RawMarkdownFallback.tsx
"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { MarkdownView } from "@/components/review/MarkdownView";

export function RawMarkdownFallback({
  file,
  onSubmit,
  submitting,
}: {
  file: QuestionFile;
  onSubmit: (text: string) => void;
  submitting: boolean;
}) {
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(text);
      }}
      className="space-y-4"
    >
      <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-4 text-sm">
        <p className="font-bold text-amber-900">표준 형식으로 파싱하지 못했습니다</p>
        <p className="text-amber-800 mt-1">아래 원본 내용을 확인하고 자유롭게 답변을 작성해 주세요.</p>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <MarkdownView markdown={file.raw_markdown ?? ""} />
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <label htmlFor="freeform" className="block text-sm font-medium mb-2">
          자유 답변
        </label>
        <textarea
          id="freeform"
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
        <div className="mt-3 flex justify-end">
          <button type="submit" disabled={submitting || text.trim() === ""} className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold">
            제출
          </button>
        </div>
      </div>
    </form>
  );
}
```

```tsx
// frontend/components/questions/ClarificationBanner.tsx
import Link from "next/link";

export function ClarificationBanner({
  projectId,
  path,
  preamble,
}: {
  projectId: string;
  path: string;
  preamble: string | null;
}) {
  return (
    <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 overflow-hidden mb-6">
      <div className="px-6 py-4 flex gap-3">
        <span className="text-xl shrink-0" aria-hidden="true">⚠️</span>
        <div className="text-sm">
          <p className="font-bold text-amber-900">답변 간 모순이 감지되어 게이트가 보류되었습니다</p>
          {preamble && <p className="text-amber-800 mt-1">{preamble}</p>}
          <Link
            href={`/projects/${projectId}/questions?file=${encodeURIComponent(path)}`}
            className="mt-3 inline-block px-3 py-1.5 rounded-lg bg-amber-600 text-white text-xs font-medium hover:bg-amber-700"
          >
            확인 질문 답변하기 →
          </Link>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/questions && npx tsc --noEmit`
Expected: PASS (QuestionCard ×3, QuestionForm ×2, RawMarkdownFallback ×2, ClarificationBanner ×1 = 8 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/questions frontend/components/review/MarkdownView.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): question wizard components + react-markdown fallback"
```

---

### Task 5: Question wizard page (picker + PUT submit + clarification detection)

**Files:**
- Create: `frontend/app/projects/[projectId]/questions/page.tsx`
- Test: `frontend/app/projects/[projectId]/questions/page.test.tsx`

**Interfaces:**
- Consumes: `listQuestionFiles`, `getQuestionFile`, `putAnswers`, `postMessage`, `useAsync`, the Task 4 components, `AppHeader`.
- Produces the wizard route. Behavior:
  - Loads `listQuestionFiles(projectId)`. If a `*-clarification-questions.md` path is present, renders `<ClarificationBanner>` above the picker (contradiction detection is the backend's job — the frontend only reacts to the file's presence).
  - Selects the active file from the `?file=` search param (via `useSearchParams`) or defaults to the first non-clarification question file (or the clarification file if that's all there is). A simple picker (list of file basenames) lets the user switch.
  - Loads the active file via `getQuestionFile`. If `parse_ok` → `<QuestionForm>`, else `<RawMarkdownFallback>`.
  - `QuestionForm` submit → `putAnswers(projectId, path, answers)`; on success re-loads the file (reparsed) and re-loads the file list (a submit may cause the agent to create a clarification file — but note: post-submit contradiction generation is the agent's job and may be async; the frontend just re-lists). `RawMarkdownFallback` submit → `postMessage(projectId, text)`.
  - Errors: 404 → "질문 파일을 찾을 수 없습니다."; 400 → "답변 형식이 올바르지 않습니다."
- Test wraps rendering so `useSearchParams` resolves (no `?file=` → defaults to first file).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/projects/[projectId]/questions/page.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";
import QuestionsPage from "./page";

// next/navigation is not wired in the test renderer; stub the hooks the page uses.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";

const params = Promise.resolve({ projectId: "pilot1" });

describe("Questions page", () => {
  it("defaults to the first question file and renders the form", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [STRAT] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    render(<QuestionsPage params={params} />);
    expect(await screen.findByText(/Q1\. 이 제품을 시장/)).toBeInTheDocument();
  });

  it("PUTs answers on submit and re-renders the reparsed file", async () => {
    let putBody: any;
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [STRAT] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
      http.put(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json(strategyQuestions);
      }),
    );
    render(<QuestionsPage params={params} />);
    await screen.findByText(/Q1\. 이 제품을 시장/);
    await userEvent.click(screen.getByRole("button", { name: /답변 제출/ }));
    await waitFor(() => expect(putBody).toBeTruthy());
    expect(putBody.answers["1"]).toBe("A");
  });

  it("renders the clarification banner when a clarification file exists", async () => {
    const CLAR = "aiplc-docs/discovery/envision/prfaq-clarification-questions.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () =>
        HttpResponse.json({ questions: [STRAT, CLAR] }),
      ),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    render(<QuestionsPage params={params} />);
    expect(await screen.findByText(/모순이 감지되어 게이트가 보류/)).toBeInTheDocument();
  });

  it("falls back to raw markdown when parse_ok is false", async () => {
    const FREE = "aiplc-docs/freeform-notes.md";
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions`, () => HttpResponse.json({ questions: [FREE] })),
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${FREE}`, () => HttpResponse.json(unparsedQuestions)),
    );
    render(<QuestionsPage params={params} />);
    expect(await screen.findByText(/표준 형식으로 파싱하지 못했습니다/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/questions/page.test.tsx"`
Expected: FAIL — `./page` does not resolve.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/projects/[projectId]/questions/page.tsx
"use client";
import { use, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { RawMarkdownFallback } from "@/components/questions/RawMarkdownFallback";
import { ClarificationBanner } from "@/components/questions/ClarificationBanner";
import {
  listQuestionFiles,
  getQuestionFile,
  putAnswers,
  postMessage,
  ApiError,
} from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

const isClarification = (p: string) => p.endsWith("-clarification-questions.md");

export default function QuestionsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const search = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const files = useAsync(() => listQuestionFiles(projectId), [projectId]);
  const list = files.data ?? [];
  const clarification = list.find(isClarification);
  const requested = search.get("file");
  const active =
    (requested && list.includes(requested) ? requested : undefined) ??
    list.find((p) => !isClarification(p)) ??
    list[0];

  const file = useAsync(
    () => (active ? getQuestionFile(projectId, active) : Promise.resolve(null)),
    [projectId, active],
  );

  async function submitAnswers(answers: Record<string, string>) {
    if (!active) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await putAnswers(projectId, active, answers);
      file.reload();
      files.reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setSubmitError("답변 형식이 올바르지 않습니다.");
      else if (err instanceof ApiError && err.status === 404) setSubmitError("질문 파일을 찾을 수 없습니다.");
      else setSubmitError("답변 제출에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitFreeText(text: string) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await postMessage(projectId, text);
      file.reload();
      files.reload();
    } catch {
      setSubmitError("답변 제출에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  const notFound = file.error instanceof ApiError && file.error.status === 404;

  return (
    <>
      <AppHeader activeTab="questions" projectId={projectId} />
      <main className="max-w-4xl mx-auto px-6 py-8">
        {clarification && (
          <ClarificationBanner projectId={projectId} path={clarification} preamble={null} />
        )}

        {list.length > 1 && (
          <div className="flex flex-wrap gap-2 mb-6">
            {list.map((p) => {
              const activeBtn = p === active;
              return (
                <a
                  key={p}
                  href={`/projects/${projectId}/questions?file=${encodeURIComponent(p)}`}
                  className={`text-xs px-3 py-1.5 rounded-lg border ${
                    activeBtn ? "bg-violet-600 text-white border-violet-600" : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {p.split("/").pop()}
                </a>
              );
            })}
          </div>
        )}

        {files.loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {notFound && <p className="text-sm text-rose-600">질문 파일을 찾을 수 없습니다.</p>}
        {submitError && <p className="text-sm text-rose-600 mb-4">{submitError}</p>}
        {!files.loading && list.length === 0 && (
          <p className="text-sm text-slate-400">아직 답변할 질문이 없습니다.</p>
        )}

        {file.data && file.data.parse_ok && (
          <QuestionForm file={file.data} onSubmit={submitAnswers} submitting={submitting} />
        )}
        {file.data && !file.data.parse_ok && (
          <RawMarkdownFallback file={file.data} onSubmit={submitFreeText} submitting={submitting} />
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/questions/page.test.tsx" && npx tsc --noEmit`
Expected: PASS (4 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/projects/[projectId]/questions"
git commit -m "feat(frontend): question wizard page (picker, PUT submit, clarification, fallback)"
```

---

### Task 6: Document review components (DocumentPanel, ApprovalGate, VerificationSummary)

**Files:**
- Create: `frontend/components/review/DocumentPanel.tsx`
- Create: `frontend/components/review/ApprovalGate.tsx`
- Create: `frontend/components/review/VerificationSummary.tsx`
- Test: `frontend/components/review/DocumentPanel.test.tsx`
- Test: `frontend/components/review/ApprovalGate.test.tsx`

**Interfaces:** (ported from `files/ui/03-document-review.html`)
- `DocumentPanel({ markdown }: { markdown: string })` — the left "Living Document" panel: header (📕 discovery-document.md + "Living Document" pill + ".md 내보내기" button), and the markdown rendered via `MarkdownView`. Empty state ("아직 작성된 문서가 없습니다.") when `markdown` is empty.
- `ApprovalGate({ onApprove, onRevise, busy }: { onApprove: () => void; onRevise: (text: string) => void; busy: boolean })` — the violet gradient gate banner ("승인 게이트") with two actions: "✓ 승인하고 다음 단계로" (calls `onApprove`) and "✏️ 수정 요청" which toggles a textarea; submitting the textarea calls `onRevise(text)`. Buttons disabled while `busy`.
- `VerificationSummary({ entries }: { entries: AuditEntry[] })` — the right panel(s): "AI 검증 요약" (derived generically from the latest audit entry summaries — since the frontend has no verification model, it renders the recent `ai_response` lines as check items) and "승인 게이트 이력" (audit entries whose `context`/`ai_response` mention gate/approve rendered as the history list). This deliberately renders backend audit data rather than hardcoded mockup copy.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/review/DocumentPanel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocumentPanel } from "./DocumentPanel";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

describe("DocumentPanel", () => {
  it("renders the document markdown (headings + table)", () => {
    render(<DocumentPanel markdown={discoveryDocument} />);
    expect(screen.getByText("Press Release")).toBeInTheDocument();
    expect(screen.getByText("담당자 간 결과 편차")).toBeInTheDocument(); // from the GFM table
  });

  it("shows an empty state when the document is empty", () => {
    render(<DocumentPanel markdown="" />);
    expect(screen.getByText(/아직 작성된 문서가 없습니다/)).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/review/ApprovalGate.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalGate } from "./ApprovalGate";

describe("ApprovalGate", () => {
  it("fires onApprove when the approve button is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<ApprovalGate onApprove={onApprove} onRevise={vi.fn()} busy={false} />);
    await user.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("reveals the revision textarea and submits natural-language text", async () => {
    const user = userEvent.setup();
    const onRevise = vi.fn();
    render(<ApprovalGate onApprove={vi.fn()} onRevise={onRevise} busy={false} />);
    await user.click(screen.getByRole("button", { name: /수정 요청/ }));
    await user.type(screen.getByLabelText(/수정 요청 사항/), "FAQ에 다국어 지원 항목 추가해줘");
    await user.click(screen.getByRole("button", { name: /수정 요청 제출/ }));
    expect(onRevise).toHaveBeenCalledWith("FAQ에 다국어 지원 항목 추가해줘");
  });

  it("disables actions while busy", () => {
    render(<ApprovalGate onApprove={vi.fn()} onRevise={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/review`
Expected: FAIL — `DocumentPanel` / `ApprovalGate` imports do not resolve (MarkdownView already exists from Task 4).

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/components/review/DocumentPanel.tsx
import { MarkdownView } from "./MarkdownView";

export function DocumentPanel({ markdown }: { markdown: string }) {
  return (
    <article className="lg:col-span-2 bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h2 className="font-bold">📕 discovery-document.md</h2>
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">Living Document</span>
        </div>
        <button className="px-2.5 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-slate-600 text-xs">
          .md 내보내기
        </button>
      </div>
      <div className="p-6 text-sm text-slate-700">
        {markdown.trim() === "" ? (
          <p className="text-slate-400">아직 작성된 문서가 없습니다.</p>
        ) : (
          <MarkdownView markdown={markdown} />
        )}
      </div>
    </article>
  );
}
```

```tsx
// frontend/components/review/ApprovalGate.tsx
"use client";
import { useState } from "react";

export function ApprovalGate({
  onApprove,
  onRevise,
  busy,
}: {
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");

  return (
    <>
      <div
        role="alert"
        className="rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 text-white p-6 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4 shadow-lg shadow-violet-200"
      >
        <div className="flex gap-4">
          <span className="text-3xl shrink-0" aria-hidden="true">🚦</span>
          <div>
            <h1 className="text-lg font-bold">승인 게이트</h1>
            <p className="text-violet-100 text-sm mt-1">
              AI가 Discovery Document를 작성했습니다. 검토 후 승인해야 다음 단계로 진행됩니다.
              승인·수정요청은 모두 감사 로그에 기록됩니다.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <button
            className="px-4 py-2.5 rounded-lg bg-white/15 hover:bg-white/25 border border-white/30 text-sm font-medium disabled:opacity-50"
            disabled={busy}
            onClick={() => setOpen((v) => !v)}
          >
            ✏️ 수정 요청
          </button>
          <button
            className="px-6 py-2.5 rounded-lg bg-white text-violet-700 text-sm font-bold hover:bg-violet-50 disabled:opacity-50"
            disabled={busy}
            onClick={onApprove}
          >
            ✓ 승인하고 다음 단계로
          </button>
        </div>
      </div>

      {open && (
        <div className="bg-white border border-violet-200 rounded-xl p-5 mb-6">
          <label htmlFor="revision-input" className="font-medium text-sm">
            수정 요청 사항{" "}
            <span className="text-slate-400 font-normal">— 자연어로 설명하면 AI가 문서를 수정한 뒤 다시 게이트로 돌아옵니다</span>
          </label>
          <textarea
            id="revision-input"
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="예: FAQ에 다국어 지원 계획 항목을 추가해줘."
            className="mt-2 w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
          />
          <div className="mt-3 flex justify-end gap-2">
            <button className="px-4 py-2 text-sm rounded-lg border border-slate-300 hover:bg-slate-50" onClick={() => setOpen(false)}>
              취소
            </button>
            <button
              className="px-4 py-2 text-sm rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50"
              disabled={busy || text.trim() === ""}
              onClick={() => onRevise(text)}
            >
              수정 요청 제출
            </button>
          </div>
        </div>
      )}
    </>
  );
}
```

```tsx
// frontend/components/review/VerificationSummary.tsx
import type { AuditEntry } from "@/lib/api/types";

// Renders backend audit data — NOT hardcoded mockup copy. "AI 검증 요약" shows
// the most recent AI responses as check lines; "승인 게이트 이력" shows entries
// whose context/response reference a gate or approval. The frontend applies no
// methodology judgment; it just surfaces what audit.md recorded.
export function VerificationSummary({ entries }: { entries: AuditEntry[] }) {
  const recent = [...entries].sort((a, b) => b.index - a.index);
  const gateHistory = recent.filter((e) =>
    /gate|approv|승인|게이트/i.test(`${e.context ?? ""} ${e.ai_response}`),
  );

  return (
    <div className="space-y-6">
      <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="check-heading">
        <div className="px-5 py-4 border-b border-slate-100">
          <h2 id="check-heading" className="font-bold">AI 검증 요약</h2>
        </div>
        <ul className="p-5 space-y-3 text-sm">
          {recent.slice(0, 5).map((e) => (
            <li key={e.index} className="flex gap-2.5">
              <span className="text-emerald-500" aria-hidden="true">✓</span>
              <span className="line-clamp-2">{e.ai_response}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="gate-heading">
        <div className="px-5 py-4 border-b border-slate-100">
          <h2 id="gate-heading" className="font-bold">승인 게이트 이력</h2>
        </div>
        <ul className="p-5 space-y-4 text-sm">
          {gateHistory.length === 0 && <li className="text-slate-400">기록된 승인 이력이 없습니다.</li>}
          {gateHistory.map((e) => (
            <li key={e.index} className="flex gap-3">
              <span className="shrink-0 w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-xs" aria-hidden="true">
                ✓
              </span>
              <div>
                <p className="font-medium line-clamp-2">{e.ai_response}</p>
                <p className="text-xs text-slate-400">Entry {e.index}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="bg-slate-100 rounded-xl p-5 text-xs text-slate-500 leading-relaxed">
        <p className="font-medium text-slate-600 mb-1">🔒 감사 추적 (audit.md)</p>
        <p>
          모든 입력은 원문 그대로 타임스탬프와 함께 기록됩니다. API 키·크리덴셜은 절대 기록되지 않습니다.
          이 게이트에서의 승인/수정요청 결정도 즉시 기록됩니다.
        </p>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/review && npx tsc --noEmit`
Expected: PASS (DocumentPanel ×2, ApprovalGate ×3 = 5 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/review/DocumentPanel.tsx frontend/components/review/ApprovalGate.tsx \
  frontend/components/review/VerificationSummary.tsx frontend/components/review/DocumentPanel.test.tsx \
  frontend/components/review/ApprovalGate.test.tsx
git commit -m "feat(frontend): document-review components (panel, approval gate, verification)"
```

---

### Task 7: Document review page (GET /document + approve/revise via POST /message)

**Files:**
- Create: `frontend/app/projects/[projectId]/review/page.tsx`
- Test: `frontend/app/projects/[projectId]/review/page.test.tsx`

**Interfaces:**
- Consumes: `getDocument`, `getAudit`, `postMessage`, `useAsync`, the Task 6 components, `AppHeader`.
- Produces the review route. Loads `getDocument` + `getAudit`. Renders `<ApprovalGate>`, then a 3-col grid: `<DocumentPanel>` (2 cols) + `<VerificationSummary>` (1 col).
  - Approve → `postMessage(projectId, "승인")`, then refetch document + audit (gate state advances). Shows a busy state during the round-trip.
  - Revise → `postMessage(projectId, reviseText)`, then refetch document + audit.
  - Errors surface a Korean message; 404 on document → treated as empty document (DocumentPanel empty state).
- The approve wiring is the single most important behavior test: clicking Approve fires `POST /message` with body `{text: "승인"}`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/projects/[projectId]/review/page.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";
import { auditEntries } from "@/test/fixtures/auditEntries";
import ReviewPage from "./page";

const params = Promise.resolve({ projectId: "pilot1" });

function mockDocAndAudit() {
  server.use(
    http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    http.get(`${API_BASE_URL}/projects/pilot1/audit`, () => HttpResponse.json(auditEntries)),
  );
}

describe("Review page", () => {
  it("renders the document and the approval gate", async () => {
    mockDocAndAudit();
    render(<ReviewPage params={params} />);
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeInTheDocument();
  });

  it("clicking Approve POSTs {text:'승인'} to /message", async () => {
    mockDocAndAudit();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects/pilot1/message`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ events: [{ kind: "done", text: null, path: null }] });
      }),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
    await waitFor(() => expect(body).toEqual({ text: "승인" }));
  });

  it("submitting a revision POSTs the natural-language text to /message", async () => {
    mockDocAndAudit();
    let body: any;
    server.use(
      http.post(`${API_BASE_URL}/projects/pilot1/message`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ events: [{ kind: "done", text: null, path: null }] });
      }),
    );
    render(<ReviewPage params={params} />);
    await screen.findByText("Press Release");
    await userEvent.click(screen.getByRole("button", { name: /수정 요청/ }));
    await userEvent.type(screen.getByLabelText(/수정 요청 사항/), "FAQ에 다국어 지원 추가");
    await userEvent.click(screen.getByRole("button", { name: /수정 요청 제출/ }));
    await waitFor(() => expect(body).toEqual({ text: "FAQ에 다국어 지원 추가" }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/review/page.test.tsx"`
Expected: FAIL — `./page` does not resolve.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/projects/[projectId]/review/page.tsx
"use client";
import { use, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { DocumentPanel } from "@/components/review/DocumentPanel";
import { ApprovalGate } from "@/components/review/ApprovalGate";
import { VerificationSummary } from "@/components/review/VerificationSummary";
import { getDocument, getAudit, postMessage, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function ReviewPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // A 404 document is treated as an empty document, not an error.
  const doc = useAsync(
    () => getDocument(projectId).catch((e) => (e instanceof ApiError && e.status === 404 ? "" : Promise.reject(e))),
    [projectId],
  );
  const audit = useAsync(() => getAudit(projectId), [projectId]);

  async function sendTurn(text: string) {
    setBusy(true);
    setActionError(null);
    try {
      await postMessage(projectId, text);
      doc.reload();
      audit.reload();
    } catch {
      setActionError("요청 처리에 실패했습니다. 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <AppHeader activeTab="review" projectId={projectId} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <ApprovalGate onApprove={() => sendTurn("승인")} onRevise={(t) => sendTurn(t)} busy={busy} />
        {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}
        {busy && <p className="text-sm text-slate-400 mb-4">AI가 요청을 처리하고 있습니다…</p>}

        <div className="grid lg:grid-cols-3 gap-6">
          <DocumentPanel markdown={doc.data ?? ""} />
          <VerificationSummary entries={audit.data ?? []} />
        </div>
      </main>
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run "app/projects/[projectId]/review/page.test.tsx" && npx tsc --noEmit`
Expected: PASS (3 tests); `tsc` clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/projects/[projectId]/review"
git commit -m "feat(frontend): document-review page with approve/revise via POST /message"
```

---

### Task 8: Full suite, build, and INTEGRATION wizard e2e

**Files:**
- Create: `frontend/e2e/wizard.spec.ts`
- Test: full Vitest suite + build

**Interfaces:**
- Produces a second INTEGRATION Playwright spec (needs a live backend + a seeded project with a question file). Kept out of the unit path.

- [ ] **Step 1: Write the e2e spec**

```ts
// frontend/e2e/wizard.spec.ts
import { test, expect } from "@playwright/test";

// INTEGRATION: requires a live backend with a project that has a question file
// (seed via the backend before running). Answers Q1 and submits.
test("answer a question and submit", async ({ page }) => {
  const pid = process.env.E2E_PROJECT_ID ?? "pilot1";
  await page.goto(`/projects/${pid}/questions`);
  await expect(page.getByRole("button", { name: /답변 제출/ })).toBeVisible();
  await page.getByRole("button", { name: /답변 제출/ }).click();
  // After submit the form re-renders (reparsed file) without an error banner.
  await expect(page.getByText(/제출에 실패/)).toHaveCount(0);
});
```

- [ ] **Step 2: Run the full unit suite**

Run: `cd frontend && npm run test`
Expected: PASS — every Vitest test from Plan A (Tasks 1–7) and Plan B (Tasks 1–7). Plan B adds: stageProgress ×4, dashboard ×7, dashboard page ×2, questions components ×8, questions page ×4, review components ×5, review page ×3 = 33 new tests. `e2e/` excluded.

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: `tsc` clean; `next build` succeeds and lists routes `/`, `/projects/[projectId]/dashboard`, `/projects/[projectId]/questions`, `/projects/[projectId]/review`.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/wizard.spec.ts
git commit -m "test(frontend): INTEGRATION wizard e2e; full Discovery slice green"
```

---

## Self-Review

**Scope coverage (Plan B — Discovery screens):**
- Dashboard (port 01: stage timeline from `GET /state`, artifacts from `GET /artifacts`, activity feed from `GET /audit`) → Tasks 2 (components) + 3 (page).
- Question wizard (port 02: A/B/…/X options, ★ recommended default, mandatory Other free-text, `PUT` submit, `parse_ok=false` raw-markdown fallback, clarification/contradiction banner on `*-clarification-questions.md` presence) → Tasks 4 (components) + 5 (page). Post-submit contradiction generation left to the agent; frontend only re-lists files and renders whatever clarification file appears.
- Document review (port 03: `GET /document` markdown in Living-Document panel + approval-gate banner; Approve → `POST /message "승인"`; Revise → `POST /message` NL; AI verification summary + gate history from audit) → Tasks 6 (components) + 7 (page).

**Every in-scope screen mapped to a task:** dashboard → T2/T3; wizard → T4/T5; doc-review → T6/T7; fixtures/helpers → T1; suite/e2e → T8.

**Testing strategy realized:** Component tests use pilot1-derived fixtures (`test/fixtures/*` in T1: parsed `strategy-questions.md`, `aiplc-state.md`, `audit.md`, `prfaq-clarification-questions.md`, a `parse_ok=false` payload, a discovery-document markdown). Explicitly required cases covered: QuestionFile→form rendering incl. recommended/multi-select (T4 QuestionCard/QuestionForm), `parse_ok=false` fallback (T4 RawMarkdownFallback + T5 page), stage-timeline from a ProjectState fixture (T2 StageTimeline), approval-gate interaction firing the right POST (T6 ApprovalGate + T7 page "clicking Approve POSTs {text:'승인'}"). Pages tested against MSW-mocked endpoints (no live backend). Playwright specs labelled INTEGRATION and excluded from vitest.

**Type consistency with backend:** all components import types from `lib/api/types.ts` (Plan A) — `QuestionFile`, `Question`, `QuestionOption` (`is_other`/`recommended`), `ProjectState`/`StageState` (`status` literals), `AuditEntry` (`user_input`/`ai_response`/`context`). No type is redefined here. `tsc --noEmit` run in every task.

**Placeholder scan:** No TBD/TODO. The `strategyQuestions` fixture uses a generated tail for Q3–13 but is explicitly annotated that all 13 must be present with `is_other` on the final option and pilot `[Answer]` values; the assertions only depend on Q1/Q2 + counts, so it is not a hidden placeholder. All component/page code shown in full.

**Constraint checks:** No methodology logic — stage names/notes/questions/categories/contradiction copy all come from backend payloads; `stageProgress.ts` only counts. Korean chrome copy ported verbatim from mockups. `parse_ok=false` → raw-markdown fallback; 404/400/409 → typed `ApiError` + Korean states. Approve/revise use sync `postMessage` (Plan A decision) then refetch. `react-markdown` renders without `dangerouslySetInnerHTML` (XSS-safe).

**Depends on:** Plan A merged (scaffold, client, types, SSE helper, `useAsync`, `AppHeader`, MSW harness) + backend Phase 1 + API Completion merged before running against a real backend / e2e. Unit tests mock the API.

**Out of scope (stated):** Conversational Canvas (04), iframe preview, build-log streaming, handoff/export, facilitator session management, SSO. These are the next frontend plan.
