import { describe, it, expect, vi } from "vitest";
import { render, screen, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import * as workspaceStream from "@/lib/useWorkspaceStream";
import WorkspacePage from "./page";

const QP = {
  interrupt_id: "i-1",
  questions: {
    name: "q",
    preamble: null,
    parse_ok: true,
    raw_markdown: null,
    questions: [
      {
        number: 1,
        category: null,
        text: "누구?",
        answer: null,
        options: [{ letter: "A", text: "PM", is_other: false, recommended: true }],
      },
    ],
  },
};

const params = Promise.resolve({ projectId: "p1" });

function mockWorkspaceStream(overrides: Partial<workspaceStream.WorkspaceStream> = {}) {
  vi.spyOn(workspaceStream, "useWorkspaceStream").mockReturnValue({
    items: [],
    streaming: false,
    send: vi.fn(),
    submitAnswers: vi.fn(),
    pendingQuestions: null,
    stages: [],
    lastDocument: null,
    changedPaths: [],
    ...overrides,
  });
}

describe("Workspace page", () => {
  it("renders the three-pane grid: stage sidebar, chat, context panel", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream();
    // use(params) suspends on first render (plain Promise.resolve params); the
    // act-wrap lets that Suspense retry flush before we query (existing
    // canvas/dashboard page test pattern).
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    expect(await screen.findByLabelText("스테이지 진행 상황")).toBeInTheDocument();
    expect(screen.getByLabelText("대화 타임라인")).toBeInTheDocument();
    expect(screen.getByLabelText("컨텍스트 패널")).toBeInTheDocument();
  });

  it("shows a pending-questions badge over the chat that opens a bottom-sheet QuestionForm (mobile fallback for the hidden right panel)", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    const submitAnswers = vi.fn();
    mockWorkspaceStream({ pendingQuestions: QP, submitAnswers });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");

    // No pending questions → no badge (covered implicitly by the first test's
    // default mock); with pending questions, the badge appears and opens the
    // sheet with the SAME QuestionForm content the right panel would show.
    const badge = screen.getByRole("button", { name: /답변 대기 중인 질문/ });
    const user = userEvent.setup();
    await user.click(badge);

    // The right panel (hidden below `lg` via CSS, but still present in the
    // jsdom tree) renders the SAME QuestionForm content for this mode, so
    // scope these assertions to the sheet's own dialog to avoid ambiguous
    // duplicate matches.
    const dialog = screen.getByRole("dialog", { name: "질문 답변 시트" });
    expect(within(dialog).getByText(/누구\?/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /답변 제출/ }));
    expect(submitAnswers).toHaveBeenCalledTimes(1);
  });

  it("marks the bottom-sheet dialog as aria-modal and moves focus into it on open", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ pendingQuestions: QP });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /답변 대기 중인 질문/ }));

    const dialog = screen.getByRole("dialog", { name: "질문 답변 시트" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
  });

  it("closes the bottom-sheet on Escape", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ pendingQuestions: QP });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /답변 대기 중인 질문/ }));
    expect(screen.getByRole("dialog", { name: "질문 답변 시트" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "질문 답변 시트" })).not.toBeInTheDocument();
  });

  it("does not show the pending-questions badge when there is nothing pending", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream();
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");
    expect(screen.queryByRole("button", { name: /답변 대기 중인 질문/ })).not.toBeInTheDocument();
  });

  it("shows a document-update banner linking to the review route when lastDocument is set", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({
      lastDocument: { path: "aiplc-docs/discovery/discovery-document.md", version: "v2", summary: "" },
    });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");

    const banner = screen.getByRole("status");
    expect(within(banner).getByText(/v2/)).toBeInTheDocument();
    const link = within(banner).getByRole("link", { name: /문서 리뷰/ });
    expect(link).toHaveAttribute("href", "/projects/p1/review");
  });

  it("does not show the document-update banner when lastDocument is null", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream();
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
