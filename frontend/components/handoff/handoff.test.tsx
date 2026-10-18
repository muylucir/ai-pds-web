import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { FINDING_KINDS, archiveFolder, getPackage, putSupplement, resetHandoff, startPackage } from "@/lib/api/handoff";
import { en } from "@/lib/i18n/en";
import { ko } from "@/lib/i18n/ko";
import type { PackageView, Readiness, SupplementView } from "@/lib/api/handoff";
import { GenerationProgress } from "./GenerationProgress";
import { PackagePanel } from "./PackagePanel";
import { ReadinessPanel } from "./ReadinessPanel";
import { ResetDialog, savedAnswers } from "./ResetDialog";
import { SupplementForm, toUpdate } from "./SupplementForm";
import { HandoffReadyBanner, discoveryFinished } from "./HandoffReadyBanner";

const D = "aiplc-docs/discovery/";

// 실측 test2222(Path B)의 판정 모양.
const PATH_B: Readiness = {
  origin: "B",
  prototypes: [{ id: "triage", spec: `${D}prototypes/triage/PROTOTYPE-triage.md`,
                 validation: "planned", evidence: [], reconcile: "not_needed", history: null }],
  sections: [
    { key: "problem", number: 1, status: "partial", sources: [] },
    { key: "audience", number: 2, status: "partial", sources: [] },
    { key: "goals", number: 3, status: "partial", sources: [] },
    { key: "scenarios", number: 4, status: "sourced", sources: [] },
    { key: "requirements", number: 5, status: "partial", sources: [] },
    { key: "non_goals", number: 6, status: "partial", sources: [] },
    { key: "constraints", number: 7, status: "partial", sources: [] },
    { key: "assumptions", number: 8, status: "partial", sources: [] },
    { key: "open_questions", number: 9, status: "derived", sources: [] },
  ],
  ai_defaults: { answered: 18, suggested: 4, accepted: 3, items: [] },
  discovery_document: `${D}discovery-document.md`,
  broken_references: [],
  blockers: [],
};

function readiness(props: Partial<Parameters<typeof ReadinessPanel>[0]> = {}) {
  const handlers = { onSupplement: vi.fn(), onGenerate: vi.fn(), onViewPackage: vi.fn(),
                     onReconcile: vi.fn() };
  render(<ReadinessPanel readiness={PATH_B} questionCount={7} generating={false}
                         hasPackage={false} {...handlers} {...props} />);
  return handlers;
}

describe("ReadinessPanel", () => {
  it("says the project skipped Envision and lists the empty sections", async () => {
    const h = readiness();
    expect(screen.getByText(/use case에서 출발\(Path B\)/)).toBeInTheDocument();
    expect(screen.getByText(/Envision을 거치지 않아/)).toBeInTheDocument();
    expect(screen.getByText(/^7곳: 1 문제와 근거/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "보완 질문 답하기 →" }));
    expect(h.onSupplement).toHaveBeenCalled();
  });

  it("cannot generate without a specification", () => {
    readiness({ readiness: { ...PATH_B, prototypes: [], blockers: ["no_spec"] } });
    expect(screen.getByRole("button", { name: "패키지 생성" })).toBeDisabled();
    expect(screen.getByText(/명세가 없습니다/)).toBeInTheDocument();
  });

  it("does not offer gap questions when there are none", () => {
    readiness({ questionCount: 0 });
    expect(screen.queryByRole("button", { name: "보완 질문 답하기 →" })).not.toBeInTheDocument();
  });

  it("has no reconciliation row when no build changed the spec", () => {
    readiness();
    expect(screen.queryByText("명세 정합성")).not.toBeInTheDocument();
  });

  it("sends an unreconciled spec back to Discovery with a draft naming the history", async () => {
    const history = `${D}prototypes/triage/change-history.md`;
    const h = readiness({ readiness: { ...PATH_B, prototypes: [
      { ...PATH_B.prototypes[0], reconcile: "stale", history }] } });
    expect(screen.getByText(/^triage: 최근 수정 미반영 · 패키지가 받는 명세가/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discovery에서 반영하기 →" }));
    expect(h.onReconcile).toHaveBeenCalledWith(expect.stringContaining(history));
    expect(h.onReconcile).toHaveBeenCalledWith(expect.stringContaining("spec-as-built.md"));
  });

  it("reports a reconciled spec and offers no action", () => {
    readiness({ readiness: { ...PATH_B, prototypes: [
      { ...PATH_B.prototypes[0], reconcile: "current", history: `${D}x/change-history.md` }] } });
    expect(screen.getByText("명세가 수정 이력을 모두 반영했습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discovery에서 반영하기 →" })).not.toBeInTheDocument();
  });

  it("a read-only member sees the warning without the action", () => {
    readiness({ readOnly: true, readiness: { ...PATH_B, prototypes: [
      { ...PATH_B.prototypes[0], reconcile: "missing", history: `${D}x/change-history.md` }] } });
    expect(screen.getByText(/^triage: 반영 안 됨/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discovery에서 반영하기 →" })).not.toBeInTheDocument();
  });

  it("shows the accepted-suggestion count without hiding it", () => {
    readiness();
    expect(screen.getByText(/^3 \/ 4 · 개발자가 다시 물을 수 있는/)).toBeInTheDocument();
  });
});

const STRATEGY = `${D}product-strategy/strategy-questions.md`;
const VIEW: SupplementView = {
  has_package: false,
  questions: [
    { id: "problem.evidence", section: "problem",
      answer: { text: "인터뷰 5명", unknown: false, updated_at: "t" } },
    { id: "problem.why_now", section: "problem", answer: null },
    { id: "assumptions.failure_reasons", section: "assumptions", answer: null },
  ],
  superseded: [
    { id: "goals.success", section: "goals",
      answer: { text: "처리 시간 절반", unknown: false, updated_at: "t" } },
  ],
  confirmations: [
    { key: `${STRATEGY}#1`, file: STRATEGY, number: 1, ask: "수익 모델은?", answer: "A: 첫 해는 할인",
      stage: "product_strategy", choices: ["구독형 — 매장 수 기준 월 과금"],
      note: "Discovery 결과 기반 제안", remark: "첫 해는 할인", confirmed_at: null },
    { key: `${D}envision/prfaq-clarifying-questions.md#1`, file: `${D}envision/prfaq-clarifying-questions.md`,
      number: 1, ask: "제품명은?", answer: "A", stage: "envision",
      choices: ["샘플몰 안전ON"], note: "페인 포인트 분석 기반 제안", remark: "", confirmed_at: "t",
    },
  ],
  open_questions: [],
  open_deferred: [],
  open_answered: [],
};

describe("SupplementForm", () => {
  it("has no prefilled AI answers — only what the PM wrote", () => {
    render(<SupplementForm view={VIEW} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    const why = screen.getByLabelText("이 문제를 왜 지금 풀어야 하나요?");
    expect(why).toHaveValue("");
    expect(screen.getByLabelText("이 문제가 실제로 있다는 걸 무엇으로 알았나요?")).toHaveValue("인터뷰 5명");
  });

  it("saves unknown, confirmations and keeps superseded answers on record", async () => {
    const onSave = vi.fn();
    render(<SupplementForm view={VIEW} workspaceHref="/w" busy={false} message={null}
                           onSave={onSave} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    const cards = screen.getAllByRole("checkbox");
    await userEvent.click(cards[2]); // 실패 요인: 모름
    await userEvent.click(screen.getByRole("button", { name: "맞다" }));
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onSave).toHaveBeenCalledWith({
      answers: {
        "problem.evidence": { text: "인터뷰 5명", unknown: false },
        "assumptions.failure_reasons": { text: "", unknown: true },
        "goals.success": { text: "처리 시간 절반", unknown: false },
      },
      confirmed: [`${D}envision/prfaq-clarifying-questions.md#1`, `${STRATEGY}#1`],
      open_answers: {},
    });
  });

  it("shows the decision itself, grouped by Discovery stage", () => {
    // 실측(ws-g): 확인 항목 40개가 "고른 답: A · strategy-questions.md"로만 보였다.
    render(<SupplementForm view={VIEW} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("구독형 — 매장 수 기준 월 과금")).toBeInTheDocument();
    expect(screen.getByText("첫 해는 할인")).toBeInTheDocument();
    expect(screen.getByText("AI 제안 근거: Discovery 결과 기반 제안")).toBeInTheDocument();
    const summaries = [...document.querySelectorAll("summary")].map((el) => el.textContent);
    // 단계 순서(PR/FAQ가 제품 전략보다 앞)와 단계별 확인 수.
    expect(summaries).toEqual(["PR/FAQ · 문제 정의 1건 · 확인 1", "제품 전략 1건 · 확인 0"]);
    expect(screen.queryByText(/strategy-questions\.md/)).not.toBeInTheDocument();
  });

  it("counts only the gap questions as progress — confirming is optional", () => {
    render(<SupplementForm view={VIEW} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText(/^1 \/ 3 답함/)).toBeInTheDocument();
    expect(screen.getByText("선택")).toBeInTheDocument();
  });

  it("sends the workspace as the only way to change content", () => {
    render(<SupplementForm view={VIEW} workspaceHref="/projects/p/workspace" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    for (const link of screen.getAllByRole("link", { name: "워크스페이스에서 고치기 →" })) {
      expect(link).toHaveAttribute("href", "/projects/p/workspace");
    }
  });

  it("drops blank answers from the update", () => {
    const u = toUpdate({ ...VIEW, superseded: [] }, { "problem.why_now": { text: "  ", unknown: false } },
                       new Set());
    expect(u.answers).toEqual({});
  });
});

describe("SupplementForm — what the last open decisions left to the PM", () => {
  // 실측(ws-g): 섹션이 모두 sourced라 보완 질문이 0개였는데, PRD 9번에는 보존 기간·
  // 권한·재알림 규칙처럼 구현을 막는 PM 결정이 열려 있었다.
  const OPEN: SupplementView = {
    ...VIEW, has_package: true, questions: [], superseded: [],
    open_questions: [
      { key: "open:aaaaaaaaaaaa", id: "1", question: "법무 결론: 보존 기간(R-08)", answer: null,
        until: "", external: false, options: [] },
      { key: "open:bbbbbbbbbbbb", id: "6", question: "자동 재알림 규칙(R-05)",
        answer: { question: "자동 재알림 규칙(R-05)", text: "기한 2일 전 1회", unknown: false, updated_at: "t", pending_with: "" },
        until: "", external: false, options: [] },
    ],
    open_answered: [
      { key: "open:cccccccccccc", id: "", question: "개인 링크 유효기간",
        answer: { question: "개인 링크 유효기간", text: "7일", unknown: false, updated_at: "t", pending_with: "" },
        until: "", external: false, options: [] },
    ],
  };

  it("asks the open PM decisions with no prefilled answer and counts them as progress", () => {
    render(<SupplementForm view={OPEN} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("빌드 전에 정할 PM 결정 2개")).toBeInTheDocument();
    expect(screen.getByLabelText(/법무 결론: 보존 기간/)).toHaveValue("");
    expect(screen.getByLabelText(/자동 재알림 규칙/)).toHaveValue("기한 2일 전 1회");
    expect(screen.getByText(/^1 \/ 2 답함/)).toBeInTheDocument();
    expect(screen.getByText("앞서 답한 질문 1개")).toBeInTheDocument();
  });

  it("saves open answers with their question, and keeps the earlier ones on record", async () => {
    const onSave = vi.fn();
    render(<SupplementForm view={OPEN} workspaceHref="/w" busy={false} message={null}
                           onSave={onSave} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    await userEvent.click(within(screen.getByLabelText(/법무 결론/).closest("div")!).getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onSave.mock.calls[0][0].open_answers).toEqual({
      "open:aaaaaaaaaaaa": { question: "법무 결론: 보존 기간(R-08)", text: "", unknown: true, pending_with: "" },
      "open:bbbbbbbbbbbb": { question: "자동 재알림 규칙(R-05)", text: "기한 2일 전 1회", unknown: false, pending_with: "" },
      "open:cccccccccccc": { question: "개인 링크 유효기간", text: "7일", unknown: false, pending_with: "" },
    });
  });

  // 실측(ws-g): PM 몫 18개가 전부 서술형이었고, 구현을 막는 것은 7개였다.
  const TRIAGED: SupplementView = {
    ...OPEN,
    open_questions: [
      { key: "open:dddddddddddd", id: "O-01", question: "보존 기간은? (R-08)", answer: null, until: "", external: false,
        options: [{ text: "3년", source: "go-to-market/gtm-questions.md Q4" },
                  { text: "5년", source: "envision/business-context.md" }] },
      { key: "open:eeeeeeeeeeee", id: "O-02", question: "권한 범위는? (R-16)", answer: null, until: "", external: false,
        options: [{ text: "지점은 자기 지점만", source: "envision/business-context.md" },
                  { text: "본사는 전체", source: "envision/business-context.md" }] },
    ],
    open_deferred: [
      { key: "open:ffffffffffff", id: "O-03", question: "지원 외국어 목록은?", answer: null,
        until: "목록을 바꿀 수 있게 두고 영어·베트남어로 시작", external: false, options: [] },
    ],
    open_answered: [],
  };

  it("offers grounded options with nothing preselected, and folds decisions that can wait", async () => {
    const onSave = vi.fn();
    render(<SupplementForm view={TRIAGED} workspaceHref="/w" busy={false} message={null}
                           onSave={onSave} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    const retention = screen.getByRole("radiogroup", { name: /보존 기간은/ });
    const radios = within(retention).getAllByRole("radio");
    expect(radios).toHaveLength(3);                       // 보기 둘 + 직접 입력
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(within(retention).getByText(/go-to-market\/gtm-questions.md Q4/)).toBeInTheDocument();
    // 진행률은 빌드 전 결정만 센다.
    expect(screen.getByText(/^0 \/ 2 답함/)).toBeInTheDocument();
    expect(screen.getByText("빌드 중에 정해도 되는 PM 결정 1개")).toBeInTheDocument();
    expect(screen.getByText(/답이 나올 때까지: 목록을 바꿀 수 있게/)).toBeInTheDocument();

    await userEvent.click(within(retention).getByRole("radio", { name: /5년/ }));
    const scope = screen.getByRole("radiogroup", { name: /권한 범위는/ });
    await userEvent.click(within(scope).getByRole("radio", { name: "직접 입력" }));
    await userEvent.type(screen.getByRole("textbox", { name: "직접 입력" }), "본사 안전팀만 전체");
    expect(screen.getByText(/^2 \/ 2 답함/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onSave.mock.calls[0][0].open_answers).toEqual({
      "open:dddddddddddd": { question: "보존 기간은? (R-08)", text: "5년", unknown: false, pending_with: "" },
      "open:eeeeeeeeeeee": { question: "권한 범위는? (R-16)", text: "본사 안전팀만 전체", unknown: false, pending_with: "" },
    });
  });

  it("reopens a typed answer as its own entry and an answer that matches an option as that option", () => {
    const view: SupplementView = { ...TRIAGED, open_questions: [
      { ...TRIAGED.open_questions[0],
        answer: { question: "보존 기간은? (R-08)", text: "5년", unknown: false, updated_at: "t", pending_with: "" } },
      { ...TRIAGED.open_questions[1],
        answer: { question: "권한 범위는? (R-16)", text: "본사 안전팀만", unknown: false, updated_at: "t", pending_with: "" } },
    ] };
    render(<SupplementForm view={view} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(within(screen.getByRole("radiogroup", { name: /보존 기간은/ }))
      .getByRole("radio", { name: /5년/ })).toBeChecked();
    expect(within(screen.getByRole("radiogroup", { name: /권한 범위는/ }))
      .getByRole("radio", { name: "직접 입력" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "직접 입력" })).toHaveValue("본사 안전팀만");
  });

  it("asks an external check for the answer or who is checking, with no options", async () => {
    // 실측(ws-g): "증빙 효력을 갖는가?"에 "인정한다"가 보기로 나왔다 — 법무가 답할 사실이다.
    const onSave = vi.fn();
    const view: SupplementView = { ...TRIAGED, open_deferred: [], open_questions: [
      { key: "open:gggggggggggg", id: "O-01", question: "전자 기록의 증빙 효력은? (C-01)", answer: null,
        until: "", external: true, options: [] },
    ] };
    render(<SupplementForm view={view} workspaceHref="/w" busy={false} message={null}
                           onSave={onSave} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("외부 확인")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: /확인 중인 곳/ }), "법무팀");
    expect(screen.getByText(/^1 \/ 1 답함/)).toBeInTheDocument();   // 확인 중인 곳만으로도 답한 것이다
    await userEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onSave.mock.calls[0][0].open_answers).toEqual({
      "open:gggggggggggg": { question: "전자 기록의 증빙 효력은? (C-01)", text: "", unknown: false,
                             pending_with: "법무팀" },
    });
  });

});

describe("ReadinessPanel — open PM decisions", () => {
  it("shows open PM decisions even when no topic lacks material", async () => {
    const full: Readiness = { ...PATH_B, sections: PATH_B.sections.map((s) =>
      s.key === "open_questions" ? s : { ...s, status: "sourced" }) };
    const h = readiness({ readiness: full, questionCount: 17, openDecisions: 17, hasPackage: true });
    expect(screen.getByText("9개 주제의 재료가 모두 있습니다")).toBeInTheDocument();
    expect(screen.getByText(/^17개가 아직 열려 있습니다/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "보완 질문 답하기 →" })[0]);
    expect(h.onSupplement).toHaveBeenCalled();
  });

  it("has no open-decision row before a package exists", () => {
    readiness();
    expect(screen.queryByText("빌드 전에 정할 PM 결정")).not.toBeInTheDocument();
  });
});


const READY: PackageView = {
  manifest: {
    status: "ready", started_at: "2026-10-09T10:38:00Z", finished_at: "2026-10-09T10:40:12Z", error: null, origin: "B",
    files: ["README.md", "start-prompt.md", "vision.md", "decisions.md"],
    steps: [],
    findings: [{ file: "vision.md", line: 3, term: "PostgreSQL", kind: "tech" },
               { file: "vision.md", line: 5, term: "빠르게", kind: "vague" }],
    truncated: [],
  },
  files: { "vision.md": "# 샘플몰 — Vision\n", "README.md": "# 개발 인계 패키지\n",
           "decisions.md": "# 결정 장부\n",
           "start-prompt.md": "# AI-DLC 시작 프롬프트\n\n```text\n/aidlc ./vision.md를 읽고 만들어 주세요.\n```\n\n```text\n/aidlc 다른 변형\n```\n" },
  stale: [`${D}use-case-intake/use-cases.md`],
  supplement_changed: true,
  resumable: false,
};

describe("PackagePanel", () => {
  const handlers = () => ({ onRegenerate: vi.fn(), onDownload: vi.fn(), onSupplement: vi.fn(),
                             onStatus: vi.fn() });

  it("opens on the vision document, with findings and what changed since it was built", async () => {
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    expect(screen.getByRole("heading", { name: "샘플몰 — Vision" })).toBeInTheDocument();
    expect(screen.getByText("검사에 걸린 줄 2건")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 원본 1건이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 보완 답이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("원본이 바뀜")).toBeInTheDocument();
    expect(screen.getByText(/생성 2026-10-09 10:40 UTC/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "decisions.md" }));
    expect(screen.getByRole("heading", { name: "결정 장부" })).toBeInTheDocument();
  });

  it("says how to fix each kind of finding and ignores kinds an older package had", () => {
    const pkg: PackageView = { ...READY, manifest: { ...READY.manifest!, findings: [
      { file: "vision.md", line: 4, term: "개인 링크의 유효 기간은?", kind: "dropped" },
      { file: "vision.md", line: 9, term: "O-05", kind: "triage" },
      { file: "vision.md", line: 12, term: "confirmed_by_pm=true", kind: "internal" },
      { file: "PRD.md", line: 54, term: "R-10", kind: "acceptance" },
    ] } };
    render(<PackagePanel pkg={pkg} generating={false} {...handlers()} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("검사에 걸린 줄 3건")).toBeInTheDocument();
    expect(within(alert).getByText(/원본에 이제 답이 있는지 확인하고/)).toBeInTheDocument();
    expect(within(alert).getByText("빠진 PM 결정 1")).toBeInTheDocument();
    expect(screen.getByText("L9 · 열린 질문 분류 ·", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(/acceptance/)).not.toBeInTheDocument();
  });

  it("shows the AI-DLC start request on top and copies exactly its first code block", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    expect(screen.getByText("AI-DLC 시작 프롬프트")).toBeInTheDocument();
    expect(screen.getByText("/aidlc ./vision.md를 읽고 만들어 주세요.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "복사" }));
    expect(writeText).toHaveBeenCalledWith("/aidlc ./vision.md를 읽고 만들어 주세요.");
    expect(screen.getByRole("button", { name: "복사됨" })).toBeInTheDocument();
  });

  it("has no edit control — only regenerate and download", () => {
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).not.toContain("편집");
    expect(names).toContain("다시 생성");
  });
});

describe("handoff api", () => {
  const base = `${API_BASE_URL}/projects/p1/handoff`;

  it("starts generation with a POST and reads the manifest back", async () => {
    let body: unknown = null;
    server.use(http.post(`${base}/package`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(
        { status: "generating", started_at: "t0", finished_at: null, error: null, origin: "B",
          files: [], steps: [], findings: [], truncated: [] }, { status: 202 });
    }));
    expect((await startPackage("p1")).status).toBe("generating");
    expect(body).toEqual({ resume: false });
    await startPackage("p1", true);
    expect(body).toEqual({ resume: true });
  });

  it("puts the whole supplement form", async () => {
    let body: unknown = null;
    server.use(http.put(`${base}/supplement`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ questions: [], superseded: [], confirmations: [],
                                 open_questions: [], open_deferred: [], open_answered: [] });
    }));
    await putSupplement("p1", { answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [],
                                open_answers: {} });
    expect(body).toEqual({ answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [],
                           open_answers: {} });
  });

  it("names the zip after the folder inside it, with the same rule as the server", () => {
    expect(archiveFolder("sample-mall")).toBe("sample-mall-handoff");
    expect(archiveFolder("안전 프로젝트")).toBe("project-handoff");
    expect(archiveFolder("ws_a.v2 (copy)")).toBe("ws_a.v2-copy-handoff");
  });

  it("resets the whole handoff with one DELETE", async () => {
    let method = "";
    server.use(http.delete(base, ({ request }) => {
      method = request.method;
      return HttpResponse.json({ deleted: 7 });
    }));
    expect(await resetHandoff("p1")).toEqual({ deleted: 7 });
    expect(method).toBe("DELETE");
  });

  it("reads an absent package as an empty view", async () => {
    server.use(http.get(`${base}/package`, () => HttpResponse.json(
      { manifest: null, files: {}, stale: [], supplement_changed: false })));
    expect((await getPackage("p1")).manifest).toBeNull();
  });
});

describe("HandoffReadyBanner", () => {
  const state = (gtm: "completed" | "in_progress" | "pending") => ({
    project_type: "Greenfield", current_stage: null, stages: [],
    capabilities: [
      { key: "prototype", name: "Prototype", status: "completed" as const, note: null },
      { key: "go_to_market", name: "Go-to-Market", status: gtm, note: null },
    ],
  });

  it("opens the door to the handoff tab once Go-to-Market is completed", () => {
    render(<HandoffReadyBanner projectId="p1" state={state("completed")} />);
    expect(screen.getByRole("link", { name: "인계 탭으로 가기" })).toHaveAttribute("href", "/projects/p1/handoff");
    expect(screen.getByRole("status")).toHaveTextContent("Go-to-Market까지 끝났습니다");
  });

  it("stays hidden while Go-to-Market is unfinished — e.g. only an interim Discovery Document", () => {
    // test2222: "Discovery Document 중간 통합"은 끝났지만 GTM은 남아 capability가 in_progress다.
    render(<HandoffReadyBanner projectId="p1" state={state("in_progress")} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("tolerates a state without capabilities", () => {
    expect(discoveryFinished({ project_type: null, current_stage: null, stages: [] } as never)).toBe(false);
    expect(discoveryFinished(null)).toBe(false);
  });
});

const step = (name: "vision" | "options", patch: Record<string, unknown>) => ({
  name, file: name === "vision" ? "vision.md" : "options.json", status: "pending" as const, started_at: null, finished_at: null,
  chars: 0, thinking: false, error: null, ...patch,
});

function progress(manifest: Record<string, unknown>, extra: Partial<PackageView> = {}) {
  const handlers = { onResume: vi.fn(), onRestart: vi.fn() };
  const pkg = {
    manifest: { started_at: "2026-10-09T14:16:39Z", finished_at: null, error: null, origin: "A.1",
                files: [], findings: [], truncated: [], ...manifest },
    files: {}, stale: [], supplement_changed: false, resumable: false, ...extra,
  } as PackageView;
  render(<GenerationProgress pkg={pkg} busy={false} {...handlers} />);
  return handlers;
}

describe("GenerationProgress", () => {
  it("shows each step — waiting for the first response, thinking, receiving", () => {
    progress({ status: "generating", steps: [
      step("vision", { status: "done", chars: 9000, started_at: "2026-10-09T14:16:39Z",
                     finished_at: "2026-10-09T14:19:00Z" }),
      step("options", { status: "running", started_at: new Date().toISOString() }),
    ] });
    expect(screen.getByText("vision 문서 작성")).toBeInTheDocument();
    expect(screen.getByText(/^완료 · 9,000자 · 2분 21초/)).toBeInTheDocument();
    expect(screen.getByText(/^첫 응답을 기다리는 중 ·/)).toBeInTheDocument();
    // 생성 중에는 다시 하기 버튼이 없다.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("tells thinking apart from receiving", () => {
    progress({ status: "generating", steps: [
      step("vision", { status: "running", thinking: true, started_at: new Date().toISOString() }),
      step("options", {}),
    ] });
    expect(screen.getByText(/^생각하는 중 ·/)).toBeInTheDocument();
    expect(screen.getByText("대기")).toBeInTheDocument();
  });

  it("offers to resume from the failed step when the server says it can", async () => {
    const h = progress({ status: "failed", error: "timeout", steps: [
      step("vision", { status: "failed", error: "timeout" }),
      step("options", {}),
    ] }, { resumable: true });
    expect(screen.getByRole("alert")).toHaveTextContent("모델 응답이 너무 오래 걸렸습니다");
    expect(screen.getByText("실패: 모델 응답이 너무 오래 걸렸습니다")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "실패한 단계부터 다시" }));
    expect(h.onResume).toHaveBeenCalled();
  });

  it("only offers to start over when the sources changed", async () => {
    const h = progress({ status: "interrupted", steps: [
      step("vision", { status: "done" }),
      step("options", { status: "failed", error: "interrupted" }),
    ] }, { resumable: false });
    expect(screen.getByRole("alert")).toHaveTextContent("생성이 중단됐습니다");
    expect(screen.getByText("실패: 서버가 다시 시작되어 중단됐습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "실패한 단계부터 다시" })).not.toBeInTheDocument();
    expect(screen.getByText(/처음부터 다시 만들어야 합니다/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "처음부터 다시" }));
    expect(h.onRestart).toHaveBeenCalled();
  });
});

describe("finding kinds", () => {
  // triage를 더할 때 사이드바 문구(lint.*)를 빠뜨려 키가 그대로 보였다.
  it("has every label and fix for every kind in both languages", () => {
    for (const dict of [ko, en] as Record<string, string>[]) {
      for (const k of FINDING_KINDS) {
        for (const prefix of ["handoff.pkg.lint.", "handoff.pkg.findingKind.", "handoff.pkg.findingFix."]) {
          expect(dict[prefix + k], prefix + k).toBeTruthy();
        }
      }
    }
  });
});

describe("ResetDialog", () => {
  it("says what stays and what goes, and warns only when answers would be lost", async () => {
    const onConfirm = vi.fn();
    const { rerender } = render(<ResetDialog answers={3} busy={false} error={null}
                                             onCancel={() => {}} onConfirm={onConfirm} />);
    const dialog = screen.getByRole("dialog", { name: "인계 초기화 확인" });
    expect(within(dialog).getByText(/Discovery 산출물은 그대로 남습니다/)).toBeInTheDocument();
    expect(within(dialog).getByText(/이어받던 열린 결정과 이전 PRD/)).toBeInTheDocument();
    expect(within(dialog).getByText("보완 답과 확인 3건은 되돌릴 수 없습니다.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "초기화" }));
    expect(onConfirm).toHaveBeenCalled();

    rerender(<ResetDialog answers={0} busy={false} error={null} onCancel={() => {}} onConfirm={() => {}} />);
    expect(screen.queryByText(/되돌릴 수 없습니다/)).not.toBeInTheDocument();
  });

  it("closes on Escape unless it is busy", async () => {
    const onCancel = vi.fn();
    const { rerender } = render(<ResetDialog answers={0} busy={true} error={null}
                                             onCancel={onCancel} onConfirm={() => {}} />);
    await userEvent.keyboard("{Escape}");
    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "초기화" })).toBeDisabled();
    rerender(<ResetDialog answers={0} busy={false} error={null} onCancel={onCancel} onConfirm={() => {}} />);
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
  });

  it("counts every answer and confirmation the PM left in the tab", () => {
    // VIEW: 고정 문항 답 1 + 대체된 답 1 + 확인 1.
    expect(savedAnswers(VIEW)).toBe(3);
    expect(savedAnswers(null)).toBe(0);
  });
});
