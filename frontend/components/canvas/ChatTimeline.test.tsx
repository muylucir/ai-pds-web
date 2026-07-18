// frontend/components/canvas/ChatTimeline.test.tsx  (full replacement)
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

const STRAT = "aiplc-docs/discovery/product-strategy/strategy-questions.md";
const DOC = "aiplc-docs/discovery/discovery-document.md";

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
});
