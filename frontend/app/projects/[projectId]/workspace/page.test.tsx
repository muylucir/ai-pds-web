import { describe, it, expect, vi } from "vitest";
import { render, screen, act, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import * as workspaceStream from "@/lib/useWorkspaceStream";
import * as client from "@/lib/api/client";
import WorkspacePage from "./page";

vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/client")>()),
  uploadFile: vi.fn(),
}));

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
    historyLoading: false,
    activeDoc: null,
    turnSeq: 0,
    ...overrides,
  });
}

describe("Workspace page", () => {
  it("루트 컨테이너가 relative — absolute 후손의 유령 오버플로를 페이지 안에 가둔다 (헤더 고정 회귀)", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ historyLoading: true });
    let container: HTMLElement;
    await act(async () => {
      ({ container } = render(<WorkspacePage params={params} />));
    });
    const root = container!.querySelector("div.h-screen");
    expect(root).not.toBeNull();
    expect(root!.className).toContain("relative");
    expect(root!.className).toContain("overflow-hidden");
  });

  it("renders the three-pane grid: stage sidebar, chat, context panel", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    // historyLoading: true keeps the welcome card from covering the
    // ChatTimeline empty state — this test is about the grid layout, not the
    // welcome card (which has its own dedicated tests below).
    mockWorkspaceStream({ historyLoading: true });
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
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)),
      // The inline doc panel (4th column) reads lastDocument.path on mount.
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/discovery/discovery-document.md`, () =>
        HttpResponse.json({ content: "# 문서\n\n본문" }),
      ),
    );
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

  it("shows the welcome card only when history is empty and loaded", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    expect(await screen.findByText(/어떻게 시작할까요/)).toBeInTheDocument();
  });

  it("hides the welcome card while history is loading or items exist", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ items: [], historyLoading: true, pendingQuestions: null, streaming: false });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("스테이지 진행 상황");
    expect(screen.queryByText(/어떻게 시작할까요/)).toBeNull();
  });

  describe("file attachments", () => {
    it("uploads a file via the clip button, shows a chip, and prepends the attachment mention to the next message", async () => {
      server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
      const send = vi.fn();
      mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false, send });
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", chars: 10, truncated: false });

      const { container } = render(<WorkspacePage params={params} />);
      await screen.findByLabelText("스테이지 진행 상황");

      const file = new File(["내용"], "의견.md", { type: "text/markdown" });
      const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
      const user = userEvent.setup();
      await act(async () => {
        fireEvent.change(fileInput, { target: { files: [file] } });
      });

      expect(client.uploadFile).toHaveBeenCalledWith("p1", file);
      expect(await screen.findByText("의견.md")).toBeInTheDocument();

      const box = screen.getByLabelText("채팅 메시지 입력");
      await user.type(box, "이 파일 기반으로 진행해줘");
      await user.click(screen.getByRole("button", { name: "전송" }));

      expect(send).toHaveBeenCalledWith(
        "[첨부 파일: uploads/의견.md — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 이 파일을 읽어보세요.]\n\n이 파일 기반으로 진행해줘",
      );
      expect(screen.queryByText("의견.md")).not.toBeInTheDocument();
    });

    it("removes a chip when its remove button is clicked, without sending an upload", async () => {
      server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
      const send = vi.fn();
      mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false, send });
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", chars: 10, truncated: false });

      const { container } = render(<WorkspacePage params={params} />);
      await screen.findByLabelText("스테이지 진행 상황");

      const file = new File(["내용"], "의견.md", { type: "text/markdown" });
      const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(fileInput, { target: { files: [file] } });
      });
      await screen.findByText("의견.md");

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /제거/ }));
      expect(screen.queryByText("의견.md")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("채팅 메시지 입력"), "안녕");
      await user.click(screen.getByRole("button", { name: "전송" }));
      expect(send).toHaveBeenCalledWith("안녕");
    });

    it("shows an error message when upload fails and does not add a chip", async () => {
      server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
      mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false });
      vi.mocked(client.uploadFile).mockRejectedValue(new Error("nope"));

      const { container } = render(<WorkspacePage params={params} />);
      await screen.findByLabelText("스테이지 진행 상황");

      const file = new File(["x".repeat(6_000_000)], "big.md");
      const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(fileInput, { target: { files: [file] } });
      });

      expect(await screen.findByText(/업로드에 실패했습니다/)).toBeInTheDocument();
      expect(screen.queryByText("big.md")).not.toBeInTheDocument();
    });

    it("does not prepend an attachment mention to the WelcomeCard starter message", async () => {
      server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
      const send = vi.fn();
      mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false, send });
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", chars: 10, truncated: false });

      const { container } = render(<WorkspacePage params={params} />);
      await screen.findByLabelText("스테이지 진행 상황");

      const file = new File(["내용"], "의견.md", { type: "text/markdown" });
      const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(fileInput, { target: { files: [file] } });
      });
      await screen.findByText("의견.md");

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /Path A/ }));

      expect(send).toHaveBeenCalledWith(
        "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.",
      );
    });
  });
});
