import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import { normalTurn, questionsTurn, documentTurn } from "@/test/fixtures/agentEventStreams";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";
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

describe("Canvas page — C2 structured cards + switchable panel", () => {
  it("materializes a questions card after a turn touches a *-questions.md file, and it renders via QuestionCardSlot", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(
        `${API_BASE_URL}/projects/pilot1/questions/aiplc-docs/discovery/product-strategy/strategy-questions.md`,
        () => HttpResponse.json(strategyQuestions),
      ),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "질문 만들어줘");
    await user.click(screen.getByRole("button", { name: "전송" }));
    const es = FakeEventSource.last!;
    for (const frame of questionsTurn) await act(async () => es.emit(frame));

    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("clicking an artifact card switches the right panel to the 문서 tab and loads the document", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument(); // default tab is preview

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "문서 갱신해줘");
    await user.click(screen.getByRole("button", { name: "전송" }));
    const es = FakeEventSource.last!;
    for (const frame of documentTurn) await act(async () => es.emit(frame));

    await user.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
  });

  it("approving from the Document tab sends '승인' as the next turn's text", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/state`, () => HttpResponse.json(projectState)),
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(<CanvasPage params={params} />);
    });
    await screen.findByText("Product Strategy");
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "문서" }));
    await screen.findByText("Press Release");
    await user.click(screen.getByRole("button", { name: "✓ 이 문서 승인" }));
    expect(FakeEventSource.last!.url).toContain(`text=${encodeURIComponent("승인")}`);
  });
});
