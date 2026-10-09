import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { getPackage, putSupplement, startPackage } from "@/lib/api/handoff";
import type { PackageView, Readiness, SupplementView } from "@/lib/api/handoff";
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
    { key: `${STRATEGY}#1`, file: STRATEGY, number: 1, ask: "수익 모델은?", answer: "A",
      confirmed_at: null },
  ],
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
      confirmed: [`${STRATEGY}#1`],
    });
  });

  it("sends the workspace as the only way to change content", () => {
    render(<SupplementForm view={VIEW} workspaceHref="/projects/p/workspace" busy={false} message={null}
                           onSave={() => {}} onSaveAndGenerate={() => {}} onBack={() => {}} />);
    expect(screen.getByRole("link", { name: "워크스페이스에서 고치기 →" }))
      .toHaveAttribute("href", "/projects/p/workspace");
  });

  it("drops blank answers from the update", () => {
    const u = toUpdate({ ...VIEW, superseded: [] }, { "problem.why_now": { text: "  ", unknown: false } },
                       new Set());
    expect(u.answers).toEqual({});
  });
});

const READY: PackageView = {
  manifest: {
    status: "ready", started_at: "2026-10-09T10:38:00Z", finished_at: "2026-10-09T10:40:12Z", error: null, origin: "B",
    files: ["PRD.md", "validation-report.md", "build-scope.md", "README.md"],
    findings: [{ file: "PRD.md", line: 3, term: "PostgreSQL", kind: "tech" },
               { file: "PRD.md", line: 5, term: "빠르게", kind: "vague" }],
    truncated: [],
  },
  files: { "PRD.md": "# PRD 제목\n", "README.md": "# 개발 인계 패키지\n",
           "validation-report.md": "# 검증\n", "build-scope.md": "# 남은 작업\n" },
  stale: [`${D}use-case-intake/use-cases.md`],
  supplement_changed: true,
};

describe("PackagePanel", () => {
  const handlers = () => ({ onRegenerate: vi.fn(), onDownload: vi.fn(), onSupplement: vi.fn(),
                             onStatus: vi.fn() });

  it("shows the PRD first, findings, and what changed since it was built", async () => {
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    expect(screen.getByRole("heading", { name: "PRD 제목" })).toBeInTheDocument();
    expect(screen.getByText("기술어 1건 · 모호어 1건이 섞였습니다")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 원본 1건이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("패키지를 만든 뒤 보완 답이 바뀌었습니다")).toBeInTheDocument();
    expect(screen.getByText("원본이 바뀜")).toBeInTheDocument();
    expect(screen.getByText(/생성 2026-10-09 10:40 UTC/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "build-scope.md" }));
    expect(screen.getByRole("heading", { name: "남은 작업" })).toBeInTheDocument();
  });

  it("has no edit control — only regenerate and download", () => {
    render(<PackagePanel pkg={READY} generating={false} {...handlers()} />);
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).not.toContain("편집");
    expect(names).toContain("다시 생성");
  });

  it("explains an interrupted generation and offers to run it again", async () => {
    const h = handlers();
    render(<PackagePanel pkg={{ ...READY, manifest: { ...READY.manifest!, status: "interrupted" } }}
                         generating={false} {...h} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText(/생성이 중단됐습니다/)).toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "다시 생성" }));
    expect(h.onRegenerate).toHaveBeenCalled();
  });
});

describe("handoff api", () => {
  const base = `${API_BASE_URL}/projects/p1/handoff`;

  it("starts generation with a POST and reads the manifest back", async () => {
    server.use(http.post(`${base}/package`, () => HttpResponse.json(
      { status: "generating", started_at: "t0", finished_at: null, error: null, origin: "B",
        files: [], findings: [], truncated: [] }, { status: 202 })));
    expect((await startPackage("p1")).status).toBe("generating");
  });

  it("puts the whole supplement form", async () => {
    let body: unknown = null;
    server.use(http.put(`${base}/supplement`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ questions: [], superseded: [], confirmations: [] });
    }));
    await putSupplement("p1", { answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [] });
    expect(body).toEqual({ answers: { "problem.evidence": { text: "x", unknown: false } }, confirmed: [] });
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
