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

  it("does not show the pending-questions badge when there is nothing pending", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream();
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");
    expect(screen.queryByRole("button", { name: /답변 대기 중인 질문/ })).not.toBeInTheDocument();
  });
});
