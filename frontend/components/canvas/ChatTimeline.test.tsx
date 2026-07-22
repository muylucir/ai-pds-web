// frontend/components/canvas/ChatTimeline.test.tsx  (full replacement)
import { describe, it, expect, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatTimelineItem } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const DOC = "aiplc-docs/discovery/discovery-document.md";

// Shared render helper for the stick-to-bottom suite below: a thin wrapper
// over ChatTimeline with sensible defaults so each test only varies `items`
// and `stickSignal`.
function Harness({ items, stickSignal }: { items: ChatTimelineItem[]; stickSignal?: number }) {
  return (
    <ChatTimeline
      items={items}
      projectId="pilot1"
      onChoose={vi.fn()}
      onOpenArtifact={vi.fn()}
      busy={false}
      stickSignal={stickSignal}
    />
  );
}

let msgCounter = 0;
function msg(text: string): ChatItem {
  return { id: `m-${msgCounter++}`, role: "user", text };
}

describe("ChatTimeline", () => {
  it("renders user and AI bubbles in order", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "필터 추가해줘" },
      { id: "a1", role: "ai", text: "추가했습니다.", trace: [], streaming: false, error: null },
    ];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });

  it("renders the verbatim typing-hint chrome once there is at least one item, but not on the empty state", () => {
    const items: ChatItem[] = [{ id: "u1", role: "user", text: "필터 추가해줘" }];
    const { rerender } = render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/버튼 대신 채팅으로 답해도 됩니다/)).toBeInTheDocument();
    rerender(<ChatTimeline items={[]} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />);
    expect(screen.queryByText(/버튼 대신 채팅으로 답해도 됩니다/)).not.toBeInTheDocument();
  });

  it("renders a questions card item via QuestionCardSlot", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/questions/${STRAT}`, () => HttpResponse.json(strategyQuestions)),
    );
    const items: ChatItem[] = [{ id: "c1", role: "card", card: "questions", path: STRAT }];
    await act(async () => {
      render(
        <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
      );
    });
    expect(await screen.findByText(/13개 답변 완료/)).toBeInTheDocument();
  });

  it("renders an artifact card item that calls onOpenArtifact when clicked", async () => {
    const onOpenArtifact = vi.fn();
    const items: ChatItem[] = [{ id: "c2", role: "card", card: "artifact", path: DOC }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={onOpenArtifact} busy={false} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(onOpenArtifact).toHaveBeenCalledTimes(1);
  });

  it("renders a history-card item (from useWorkspaceStream) as a static summary, naming the questions file", () => {
    const items: ChatTimelineItem[] = [{ id: "h1", role: "history-card", name: "mode-selection" }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText(/질문지 제시됨/)).toBeInTheDocument();
    expect(screen.getByText(/mode-selection/)).toBeInTheDocument();
  });

  it("renders a history-card item with no name without the trailing dash", () => {
    const items: ChatTimelineItem[] = [{ id: "h2", role: "history-card", name: null }];
    render(
      <ChatTimeline items={items} projectId="pilot1" onChoose={vi.fn()} onOpenArtifact={vi.fn()} busy={false} />,
    );
    expect(screen.getByText("📋 질문지 제시됨")).toBeInTheDocument();
  });
});

describe("ChatTimeline — stick-to-bottom", () => {
  function scroller(): HTMLElement {
    return screen.getByLabelText("대화 타임라인");
  }
  function fakeScrollGeometry(el: HTMLElement, { height = 400, content = 1000 }) {
    Object.defineProperty(el, "clientHeight", { value: height, configurable: true });
    Object.defineProperty(el, "scrollHeight", { value: content, configurable: true });
  }

  it("items가 추가되면 바닥으로 스크롤한다 (기본 stick)", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });

  it("사용자가 위로 스크롤하면 자동 스크롤이 멈춘다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    // 사용자가 위로: scrollTop을 바닥에서 멀리 두고 scroll 이벤트 발생
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={0} />);
    expect(el.scrollTop).toBe(100); // 위치 보존 — 끌려 내려가지 않음
  });

  it("stickSignal이 증가하면(메시지 전송) 무조건 바닥으로 복귀한다", () => {
    const { rerender } = render(<Harness items={[msg("1")]} stickSignal={0} />);
    const el = scroller();
    fakeScrollGeometry(el, {});
    el.scrollTop = 100;
    fireEvent.scroll(el);
    rerender(<Harness items={[msg("1"), msg("2")]} stickSignal={1} />);
    expect(el.scrollTop).toBe(el.scrollHeight);
  });
});
