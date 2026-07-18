// frontend/components/canvas/ChatTimeline.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatTimeline } from "./ChatTimeline";
import type { ChatItem } from "@/lib/useTurnStream";

describe("ChatTimeline", () => {
  it("renders user and AI bubbles in order", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "필터 추가해줘" },
      { id: "a1", role: "ai", text: "추가했습니다.", trace: [], streaming: false, error: null },
    ];
    render(<ChatTimeline items={items} />);
    expect(screen.getByText("필터 추가해줘")).toBeInTheDocument();
    expect(screen.getByText("추가했습니다.")).toBeInTheDocument();
  });

  it("renders an empty state with no items", () => {
    render(<ChatTimeline items={[]} />);
    expect(screen.getByText(/대화를 시작해 보세요/)).toBeInTheDocument();
  });
});
