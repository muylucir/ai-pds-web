import { describe, it, expect, vi } from "vitest";
import { render, screen, act, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import * as workspaceStream from "@/lib/useWorkspaceStream";
import * as client from "@/lib/api/client";

// The LanguageSwitcher that AppHeader draws calls useRouter() — in a unit test with no app router
// mounted, that hook throws. The switcher's behavior is verified by
// components/LanguageSwitcher.test.tsx, so here it only needs to mount.
// That this page reads window.location directly instead of using useSearchParams() is an
// intentional design (the draft comment in page.tsx) — so filling in useRouter alone is enough.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

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
    prototypeReady: null,
    stages: [],
    lastDocument: null,
    changedPaths: [],
    historyLoading: false,
    context: null,
    activeDoc: null,
    turnSeq: 0,
    interrupt: vi.fn(),
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
    expect(await screen.findByLabelText("단계 진행 상황")).toBeInTheDocument();
    expect(screen.getByLabelText("대화 타임라인")).toBeInTheDocument();
    expect(screen.getByLabelText("컨텍스트 패널")).toBeInTheDocument();
  });

  // AI-PDS team feedback (2026-10): while documents were the 4th column, the question column was
  // a narrow 3.5/12. Now chat and questions are 5:5, and documents open in an overlay drawer.
  it("splits chat and questions 5:5 and keeps the document in a closed drawer", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ historyLoading: true });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("단계 진행 상황");
    const grid = screen.getByLabelText("컨텍스트 패널").parentElement!;
    expect(grid.className).toContain("lg:grid-cols-[1fr_5fr_5fr]");
    const tab = screen.getByRole("button", { name: "생성된 문서 열기" });
    expect(tab).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(tab);
    expect(screen.getByRole("button", { name: "생성된 문서 닫기", expanded: true })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "생성된 문서 열기" })).toHaveAttribute("aria-expanded", "false");
  });

  // A round where a document must be read before answering, like an approval gate — open it
  // right above the question form.
  it("offers a 'View {document}' button above the question form that opens the drawer", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)),
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/discovery/envision/prfaq.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/discovery/envision/prfaq.md`, () =>
        HttpResponse.json({ content: "# PR/FAQ" })),
    );
    mockWorkspaceStream({ pendingQuestions: QP });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    const panel = screen.getByLabelText("컨텍스트 패널");
    const view = await within(panel).findByRole("button", { name: /prfaq\.md 보기/ });
    await userEvent.click(view);
    expect(screen.getByRole("button", { name: "생성된 문서 닫기", expanded: true })).toBeInTheDocument();
  });

  it("shows a pending-questions badge over the chat that opens a bottom-sheet QuestionForm (mobile fallback for the hidden right panel)", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    const submitAnswers = vi.fn();
    mockWorkspaceStream({ pendingQuestions: QP, submitAnswers });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("단계 진행 상황");

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
    await screen.findByLabelText("단계 진행 상황");

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
    await screen.findByLabelText("단계 진행 상황");

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
    await screen.findByLabelText("단계 진행 상황");
    expect(screen.queryByRole("button", { name: /답변 대기 중인 질문/ })).not.toBeInTheDocument();
  });

  it("shows a document-update banner that opens the document drawer in place", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)),
      // The document drawer reads lastDocument.path on mount (even closed).
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
    await screen.findByLabelText("단계 진행 상황");

    const banner = screen.getByRole("status");
    expect(within(banner).getByText(/v2/)).toBeInTheDocument();
    // It does not leave for the review screen — the drawer opens this document (activeDoc).
    await userEvent.click(within(banner).getByRole("button", { name: "문서 열기" }));
    expect(screen.getByRole("button", { name: "생성된 문서 닫기", expanded: true })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();   // the banner that opened it closes
  });

  it("does not show the document-update banner when lastDocument is null", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream();
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("단계 진행 상황");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("진행 중이면 중단 버튼이 뜨고 누르면 훅의 interrupt를 부른다", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    const user = userEvent.setup();
    const interrupt = vi.fn();
    mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: true, interrupt });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    await screen.findByLabelText("단계 진행 상황");
    await user.click(screen.getByRole("button", { name: "중단" }));
    expect(interrupt).toHaveBeenCalledTimes(1);
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
    await screen.findByLabelText("단계 진행 상황");
    expect(screen.queryByText(/어떻게 시작할까요/)).toBeNull();
  });

  it("히스토리를 불러오는 동안 스켈레톤을 보여준다", async () => {
    // Previously this interval was a blank screen. historyLoading already existed but was only
    // used to hide the welcome card (showWelcome in page.tsx).
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ items: [], historyLoading: true, pendingQuestions: null, streaming: false });
    // WorkspacePage suspends on the first render with use(params) — as with the other tests
    // in this file, it must be wrapped in act and wait until microtasks flush before the
    // render result can be queried synchronously.
    await act(async () => {
      render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
    });
    expect(screen.getByRole("status", { name: "이전 대화를 불러오는 중" })).toBeInTheDocument();
    // Regression guard: when items is [], ChatTimeline's own empty-state text ("대화를 시작해 보세요"
    // — start a conversation) nearly appeared together with the skeleton — a scenario where
    // text announcing a restore in progress and "nothing yet" text clash head-on.
    expect(screen.queryByText(/대화를 시작해 보세요/)).not.toBeInTheDocument();
  });

  it("로딩이 끝나면 스켈레톤이 사라진다", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false });
    await act(async () => {
      render(<WorkspacePage params={Promise.resolve({ projectId: "p1" })} />);
    });
    expect(screen.queryByRole("status", { name: "이전 대화를 불러오는 중" })).not.toBeInTheDocument();
  });

  describe("file attachments", () => {
    function setup(send = vi.fn()) {
      vi.mocked(client.uploadFile).mockReset();
      server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
      mockWorkspaceStream({ items: [], historyLoading: false, pendingQuestions: null, streaming: false, send });
      return send;
    }

    // 📎 → modal → pick a file → until the result comes out.
    async function attachViaModal(...files: File[]) {
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "파일 첨부" }));
      const dialog = screen.getByRole("dialog", { name: "파일 첨부" });
      const input = dialog.querySelector('input[type="file"]') as HTMLInputElement;
      await act(async () => {
        fireEvent.change(input, { target: { files } });
      });
      return dialog;
    }

    async function closeModal() {
      await userEvent.setup().click(screen.getByRole("button", { name: "닫기" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    }

    it("opens a modal that explains what can be attached before any file is picked", async () => {
      setup();
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      await userEvent.setup().click(screen.getByRole("button", { name: "파일 첨부" }));
      const dialog = screen.getByRole("dialog", { name: "파일 첨부" });
      expect(dialog).toHaveFocus();
      for (const text of ["문서·표·발표자료", "웹 페이지", "회의 녹취록", "이미지"]) {
        expect(within(dialog).getByText(text)).toBeInTheDocument();
      }
      expect(within(dialog).getByText(/\.docx/)).toBeInTheDocument();
      expect(within(dialog).getByText(/파일당 10MB까지/)).toBeInTheDocument();
      expect(within(dialog).getByText(/PDF는 텍스트와 원본이 함께/)).toBeInTheDocument();
      const input = dialog.querySelector('input[type="file"]') as HTMLInputElement;
      expect(input.multiple).toBe(true);
      expect(input.accept).toContain(".hwpx");
      expect(client.uploadFile).not.toHaveBeenCalled();

      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("uploads from the modal, shows a chip, and prepends the attachment mention to the next message", async () => {
      const send = setup();
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", original: null, chars: 10, truncated: false });
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const file = new File(["내용"], "의견.md", { type: "text/markdown" });
      const dialog = await attachViaModal(file);
      expect(client.uploadFile).toHaveBeenCalledWith("p1", file);
      expect(await within(dialog).findByText(/첨부됨/)).toBeInTheDocument();
      await closeModal();
      expect(screen.getByText("의견.md")).toBeInTheDocument();   // chip

      const user = userEvent.setup();
      await user.type(screen.getByLabelText("채팅 메시지 입력"), "이 파일 기반으로 진행해줘");
      await user.click(screen.getByRole("button", { name: "전송" }));

      expect(send).toHaveBeenCalledWith(
        "[첨부 파일: uploads/의견.md — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 이 파일을 읽어보세요.]\n\n이 파일 기반으로 진행해줘",
      );
      expect(screen.queryByText("의견.md")).not.toBeInTheDocument();
    });

    it("uploads several files in the order they were picked", async () => {
      setup();
      vi.mocked(client.uploadFile).mockImplementation(async (_pid, f) =>
        ({ path: `uploads/${f.name}.md`, original: null, chars: 1, truncated: false }));
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const dialog = await attachViaModal(new File(["a"], "a.md"), new File(["b"], "b.txt"));
      expect(await within(dialog).findAllByText(/첨부됨/)).toHaveLength(2);
      await closeModal();
      const chips = screen.getAllByText(/^[ab]\.(md|txt)\.md$/).map((el) => el.textContent);
      expect(chips).toEqual(["a.md.md", "b.txt.md"]);
    });

    it("points the agent at a PDF's original next to its converted text", async () => {
      const send = setup();
      vi.mocked(client.uploadFile).mockResolvedValue({
        path: "uploads/ab12cd34/요구사항.pdf.md", original: "uploads/ab12cd34/요구사항.pdf",
        chars: 10, truncated: false,
      });
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const dialog = await attachViaModal(new File(["%PDF"], "요구사항.pdf"));
      await within(dialog).findByText(/첨부됨/);
      await closeModal();

      const user = userEvent.setup();
      await user.type(screen.getByLabelText("채팅 메시지 입력"), "정리해줘");
      await user.click(screen.getByRole("button", { name: "전송" }));

      expect(send).toHaveBeenCalledWith(
        "[첨부 파일: uploads/ab12cd34/요구사항.pdf.md — 사용자가 컨텍스트로 제공한 파일을 텍스트로 변환한 것입니다. 필요 시 이 파일을 읽어보세요. 도표·그림·스캔 페이지는 원본 uploads/ab12cd34/요구사항.pdf에 있습니다.]\n\n정리해줘",
      );
    });

    it("refuses legacy formats and oversized files before uploading, and says why", async () => {
      setup();
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const big = new File(["x"], "big.pdf");
      Object.defineProperty(big, "size", { value: 10 * 1024 * 1024 + 1 });
      const dialog = await attachViaModal(new File(["x"], "기획서.hwp"), big, new File(["x"], "a.exe"));

      expect(await within(dialog).findByText(/예전 형식이라 받을 수 없습니다/)).toBeInTheDocument();
      expect(within(dialog).getByText("10MB를 넘습니다.")).toBeInTheDocument();
      expect(within(dialog).getByText(/지원하지 않는 형식입니다/)).toBeInTheDocument();
      expect(client.uploadFile).not.toHaveBeenCalled();
    });

    it("shows the server's reason when an upload fails, and adds no chip", async () => {
      setup();
      vi.mocked(client.uploadFile).mockRejectedValue(
        new client.ApiError(415, '{"detail":"cannot parse .pdf file: corrupted or invalid"}'));
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const dialog = await attachViaModal(new File(["x"], "broken.pdf"));
      expect(await within(dialog).findByText(/파일을 읽을 수 없습니다/)).toBeInTheDocument();
      await closeModal();
      expect(screen.queryByText("broken.pdf")).not.toBeInTheDocument();
    });

    it("removes a chip when its remove button is clicked", async () => {
      const send = setup();
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", original: null, chars: 10, truncated: false });
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const dialog = await attachViaModal(new File(["내용"], "의견.md"));
      await within(dialog).findByText(/첨부됨/);
      await closeModal();

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /제거/ }));
      expect(screen.queryByText("의견.md")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("채팅 메시지 입력"), "안녕");
      await user.click(screen.getByRole("button", { name: "전송" }));
      expect(send).toHaveBeenCalledWith("안녕");
    });

    it("attaches files dropped on the chat area without opening the modal", async () => {
      setup();
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", original: null, chars: 10, truncated: false });
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const main = screen.getByRole("main");
      const good = new File(["내용"], "의견.md");
      const dataTransfer = { types: ["Files"], files: [good, new File(["x"], "old.doc")] };
      fireEvent.dragEnter(main, { dataTransfer });
      expect(screen.getByText("여기에 놓으면 첨부됩니다")).toBeInTheDocument();
      await act(async () => {
        fireEvent.drop(main, { dataTransfer });
      });

      expect(screen.queryByText("여기에 놓으면 첨부됩니다")).not.toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(client.uploadFile).toHaveBeenCalledWith("p1", good);
      expect(await screen.findByText("의견.md")).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent(/old\.doc: 예전 형식이라 받을 수 없습니다/);
    });

    it("does not prepend an attachment mention to the WelcomeCard starter message", async () => {
      const send = setup();
      vi.mocked(client.uploadFile).mockResolvedValue({ path: "uploads/의견.md", original: null, chars: 10, truncated: false });
      render(<WorkspacePage params={params} />);
      await screen.findByLabelText("단계 진행 상황");

      const dialog = await attachViaModal(new File(["내용"], "의견.md"));
      await within(dialog).findByText(/첨부됨/);
      await closeModal();

      await userEvent.setup().click(screen.getByRole("button", { name: /Path A/ }));
      expect(send).toHaveBeenCalledWith(
        "AI-PLC를 시작해줘. Path A(고객 페인 포인트에서 시작)로 진행하고 싶어.",
      );
    });
  });
});

// 2026-08-17: even when Discovery handed the prototype over to build, there was no door to it on
// screen. We expected the agent to say "Prototypes 탭에서 빌드하세요" (build in the Prototypes tab),
// but measured in ws-a-v5 that guidance appeared 0 times and the user got stuck. So there is an
// event-based affordance — it does not depend on the agent's sentences.
describe("프로토타입 인수인계 카드", () => {
  it("prototype_ready가 오면 Prototypes 탭으로 가는 링크가 뜬다", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({
      historyLoading: true,
      prototypeReady: { slug: "prototype", spec_path: "aiplc-docs/x.md" },
    });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    const link = screen.getByRole("link", { name: /Prototypes/i });
    expect(link).toHaveAttribute("href", "/projects/p1/prototypes");
  });

  it("없으면 카드가 뜨지 않는다", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ historyLoading: true, prototypeReady: null });
    await act(async () => {
      render(<WorkspacePage params={params} />);
    });
    expect(screen.queryByRole("link", { name: /Prototypes/i })).toBeNull();
  });
});

describe("Workspace page — 입력창 위 고정 진행 줄", () => {
  // Progress must sit outside the conversation flow. The former approach of updating it
  // alongside the bubble overlapped with token streaming and was distracting ("정신이 없다" —
  // "too hectic"), which is why this line came about. It shows **only the last one**, not a list.
  const liveItems = [{
    id: "a1", role: "ai" as const, text: "정리 중", trace: [], streaming: true,
    error: null, activity: { kind: "tool" as const, tool: "Read",
                             detail: "aiplc-docs/x.md" },
  }];

  it("턴이 도는 동안 마지막 활동을 보여준다 — 무슨 일과 그 대상을 두 줄로", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ streaming: true, items: liveItems });
    await act(async () => { render(<WorkspacePage params={params} />); });
    expect(screen.getByTestId("live-what")).toHaveTextContent("자료를 확인하고 있어요");
    expect(screen.getByTestId("live-what-target")).toHaveTextContent("aiplc-docs/x.md");
  });

  it("도는 턴이 없으면 그리지 않는다 — 입력창 위가 상시 점유되면 안 된다", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/p1/state`, () => HttpResponse.json(projectState)));
    mockWorkspaceStream({ streaming: false, items: [] });
    await act(async () => { render(<WorkspacePage params={params} />); });
    expect(screen.queryByTestId("live-what")).not.toBeInTheDocument();
  });
});
