import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { getPackage, putSupplement, startPackage } from "@/lib/api/handoff";
import type { PackageView, Readiness, SupplementView } from "@/lib/api/handoff";
import { GenerationProgress } from "./GenerationProgress";
import { PackagePanel } from "./PackagePanel";
import { ReadinessPanel } from "./ReadinessPanel";
import { SupplementForm, toUpdate } from "./SupplementForm";
import { HandoffReadyBanner, discoveryFinished } from "./HandoffReadyBanner";

const D = "aiplc-docs/discovery/";

// 실측 test2222(Path B)의 판정 모양.
const PATH_B: Readiness = {
  origin: "B",
  prototypes: [{ id: "triage", spec: `${D}prototypes/triage/PROTOTYPE-triage.md`,
                 validation: "planned", evidence: [] }],
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
  const handlers = { onSupplement: vi.fn(), onGenerate: vi.fn(), onViewPackage: vi.fn() };
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
      note: "Discovery 결과 기반 제안", remark: "첫 해는 할인", confirmed_at: null, in_prd: [],
      in_goals: false },
    { key: `${D}envision/prfaq-clarifying-questions.md#1`, file: `${D}envision/prfaq-clarifying-questions.md`,
      number: 1, ask: "제품명은?", answer: "A", stage: "envision",
      choices: ["메가마트 안전ON"], note: "페인 포인트 분석 기반 제안", remark: "", confirmed_at: "t",
      in_prd: [], in_goals: false },
  ],
  open_questions: [],
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
    // 실측(industry-safe-law): 확인 항목 40개가 "고른 답: A · strategy-questions.md"로만 보였다.
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

  it("puts the decisions the PRD actually used first, with the PRD line they became", () => {
    const view: SupplementView = {
      ...VIEW, has_package: true,
      confirmations: VIEW.confirmations.map((c) => c.number === 1 && c.stage === "product_strategy"
        ? { ...c, in_prd: ["G-01 담당자 투입 시간 절감: 파일럿 90일 30% 이상"] } : c),
    };
    render(<SupplementForm view={view} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("PRD에 들어간 AI 제안 1건")).toBeInTheDocument();
    expect(screen.getByText("G-01 담당자 투입 시간 절감: 파일럿 90일 30% 이상")).toBeInTheDocument();
    expect(screen.getByText("PRD에 직접 쓰이지 않은 결정 1건")).toBeInTheDocument();
    // PRD에 들어간 것은 접히지 않고, 나머지만 단계별로 접힌다.
    const summaries = [...document.querySelectorAll("summary")].map((el) => el.textContent);
    expect(summaries).toEqual(["PR/FAQ · 문제 정의 1건 · 확인 1"]);
  });

  it("explains that a package moves the used decisions up when there is none yet", () => {
    render(<SupplementForm view={VIEW} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText(/패키지를 한 번 만들면/)).toBeInTheDocument();
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

describe("SupplementForm — what the last PRD left open", () => {
  // 실측(industry-safe-law): 섹션이 모두 sourced라 보완 질문이 0개였는데, PRD 9번에는 보존 기간·
  // 권한·재알림 규칙처럼 구현을 막는 PM 결정이 열려 있었다.
  const OPEN: SupplementView = {
    ...VIEW, has_package: true, questions: [], superseded: [],
    open_questions: [
      { key: "open:aaaaaaaaaaaa", id: "1", question: "법무 결론: 보존 기간(R-08)", answer: null },
      { key: "open:bbbbbbbbbbbb", id: "6", question: "자동 재알림 규칙(R-05)",
        answer: { question: "자동 재알림 규칙(R-05)", text: "기한 2일 전 1회", unknown: false, updated_at: "t" } },
    ],
    open_answered: [
      { key: "open:cccccccccccc", id: "", question: "개인 링크 유효기간",
        answer: { question: "개인 링크 유효기간", text: "7일", unknown: false, updated_at: "t" } },
    ],
  };

  it("asks the PRD's open PM decisions with no prefilled answer and counts them as progress", () => {
    render(<SupplementForm view={OPEN} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("PRD에 남은 PM 결정 2개")).toBeInTheDocument();
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
      "open:aaaaaaaaaaaa": { question: "법무 결론: 보존 기간(R-08)", text: "", unknown: true },
      "open:bbbbbbbbbbbb": { question: "자동 재알림 규칙(R-05)", text: "기한 2일 전 1회", unknown: false },
      "open:cccccccccccc": { question: "개인 링크 유효기간", text: "7일", unknown: false },
    });
  });

  it("puts the AI suggestions that became success metrics first", () => {
    // 실측: 성공 지표 G-01~05가 전부 확인되지 않은 AI 제안이었다.
    const view: SupplementView = {
      ...OPEN,
      confirmations: VIEW.confirmations.map((c) => c.stage === "product_strategy"
        ? { ...c, in_prd: ["G-01 투입 시간 60% 절감"], in_goals: true } : c),
    };
    render(<SupplementForm view={view} workspaceHref="/w" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByText("성공 지표가 된 AI 제안 1건")).toBeInTheDocument();
    expect(screen.getByText("G-01 투입 시간 60% 절감")).toBeInTheDocument();
    expect(screen.getByText("PRD에 들어간 AI 제안 0건")).toBeInTheDocument();
  });
});

describe("ReadinessPanel — open PM decisions", () => {
  it("shows PM decisions the last PRD left open even when no section is empty", async () => {
    const full: Readiness = { ...PATH_B, sections: PATH_B.sections.map((s) =>
      s.key === "open_questions" ? s : { ...s, status: "sourced" }) };
    const h = readiness({ readiness: full, questionCount: 17, openDecisions: 17, hasPackage: true });
    expect(screen.getByText("9개 섹션의 재료가 모두 있습니다")).toBeInTheDocument();
    expect(screen.getByText(/^17개가 아직 열려 있습니다/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "보완 질문 답하기 →" })[0]);
    expect(h.onSupplement).toHaveBeenCalled();
  });

  it("has no open-decision row before a package exists", () => {
    readiness();
    expect(screen.queryByText("PRD에 남은 PM 결정")).not.toBeInTheDocument();
  });
});


const READY: PackageView = {
  manifest: {
    status: "ready", started_at: "2026-10-09T10:38:00Z", finished_at: "2026-10-09T10:40:12Z", error: null, origin: "B",
    files: ["PRD.md", "validation-report.md", "build-scope.md", "README.md"],
    steps: [],
    findings: [{ file: "PRD.md", line: 3, term: "PostgreSQL", kind: "tech" },
               { file: "PRD.md", line: 5, term: "빠르게", kind: "vague" }],
    truncated: [],
  },
  files: { "PRD.md": "# PRD 제목\n", "README.md": "# 개발 인계 패키지\n",
           "validation-report.md": "# 검증\n", "build-scope.md": "# 남은 작업\n" },
  stale: [`${D}use-case-intake/use-cases.md`],
  supplement_changed: true,
  resumable: false,
};

describe("PackagePanel", () => {
  const handlers = () => ({ onRegenerate: vi.fn(), onDownload: vi.fn(), onSupplement: vi.fn(),
                             onStatus: vi.fn() });

  it("shows the PRD first, findings, and what changed since it was built", async () => {
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    expect(screen.getByRole("heading", { name: "PRD 제목" })).toBeInTheDocument();
    expect(screen.getByText("검사에 걸린 줄 2건")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 원본 1건이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 보완 답이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("원본이 바뀜")).toBeInTheDocument();
    expect(screen.getByText(/생성 2026-10-09 10:40 UTC/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "build-scope.md" }));
    expect(screen.getByRole("heading", { name: "남은 작업" })).toBeInTheDocument();
  });

  it("says how to fix each kind of finding — success metrics are not fixed by regenerating", () => {
    const pkg: PackageView = { ...READY, manifest: { ...READY.manifest!, findings: [
      { file: "PRD.md", line: 23, term: "G-01", kind: "ai_goal" },
      { file: "PRD.md", line: 54, term: "R-10", kind: "acceptance" },
      { file: "PRD.md", line: 48, term: "confirmed_by_pm=true", kind: "internal" },
    ] } };
    render(<PackagePanel pkg={pkg} generating={false} {...handlers()} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("검사에 걸린 줄 3건")).toBeInTheDocument();
    expect(within(alert).getByText(/다시 생성해도 그대로입니다/)).toBeInTheDocument();
    expect(within(alert).getByText("수용 기준 없음 1")).toBeInTheDocument();
    expect(screen.getByText("L54 · 수용 기준 없음 ·", { exact: false })).toBeInTheDocument();
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
                                 open_questions: [], open_answered: [] });
    }));
    await putSupplement("p1", { answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [],
                                open_answers: {} });
    expect(body).toEqual({ answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [],
                           open_answers: {} });
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

const step = (name: "prd" | "validation" | "scope", patch: Record<string, unknown>) => ({
  name, file: `${name}.md`, status: "pending" as const, started_at: null, finished_at: null,
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
      step("prd", { status: "done", chars: 12000, started_at: "2026-10-09T14:16:39Z",
                    finished_at: "2026-10-09T14:19:00Z" }),
      step("validation", { status: "running", started_at: new Date().toISOString() }),
      step("scope", {}),
    ] });
    expect(screen.getByText(/^완료 · 12,000자 · 2분 21초/)).toBeInTheDocument();
    expect(screen.getByText(/^첫 응답을 기다리는 중 ·/)).toBeInTheDocument();
    expect(screen.getByText("대기")).toBeInTheDocument();
    // 생성 중에는 다시 하기 버튼이 없다.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("tells thinking apart from receiving", () => {
    progress({ status: "generating", steps: [
      step("prd", { status: "running", thinking: true, started_at: new Date().toISOString() }),
      step("validation", {}), step("scope", {}),
    ] });
    expect(screen.getByText(/^생각하는 중 ·/)).toBeInTheDocument();
  });

  it("offers to resume from the failed step when the server says it can", async () => {
    const h = progress({ status: "failed", error: "timeout", steps: [
      step("prd", { status: "done", chars: 9000 }),
      step("validation", { status: "failed", error: "timeout" }),
      step("scope", {}),
    ] }, { resumable: true });
    expect(screen.getByRole("alert")).toHaveTextContent("모델 응답이 너무 오래 걸렸습니다");
    expect(screen.getByText("실패: 모델 응답이 너무 오래 걸렸습니다")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "실패한 단계부터 다시" }));
    expect(h.onResume).toHaveBeenCalled();
  });

  it("only offers to start over when the sources changed", async () => {
    const h = progress({ status: "interrupted", steps: [
      step("prd", { status: "done" }),
      step("validation", { status: "failed", error: "interrupted" }),
      step("scope", {}),
    ] }, { resumable: false });
    expect(screen.getByRole("alert")).toHaveTextContent("생성이 중단됐습니다");
    expect(screen.getByText("실패: 서버가 다시 시작되어 중단됐습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "실패한 단계부터 다시" })).not.toBeInTheDocument();
    expect(screen.getByText(/처음부터 다시 만들어야 합니다/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "처음부터 다시" }));
    expect(h.onRestart).toHaveBeenCalled();
  });
});
