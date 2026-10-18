// frontend/components/canvas/HistorySkeleton.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HistorySkeleton } from "./HistorySkeleton";

describe("HistorySkeleton", () => {
  it("스크린리더에 무엇을 기다리는지 알린다", () => {
    // The same thing is conveyed as shape (gray boxes) on screen and as text to screen readers.
    // With only pulsing boxes and no aria-label, screen reader users cannot tell it apart from an empty screen.
    render(<HistorySkeleton />);
    expect(screen.getByRole("status")).toHaveAccessibleName("이전 대화를 불러오는 중");
  });

  it("말풍선이 여러 개인 것처럼 보인다", () => {
    // With just one, it reads as "one message is on its way" — to hint that a restore is in progress
    // it must be several rows.
    render(<HistorySkeleton />);
    expect(screen.getAllByTestId("skeleton-line")).toHaveLength(3);
  });
});
