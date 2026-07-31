// frontend/components/canvas/HistorySkeleton.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HistorySkeleton } from "./HistorySkeleton";

describe("HistorySkeleton", () => {
  it("스크린리더에 무엇을 기다리는지 알린다", () => {
    // 화면에는 형태(회색 박스)로, 스크린리더에는 문구로 같은 것을 전달한다.
    // aria-label 없이 펄스 박스만 두면 스크린리더 사용자에게는 빈 화면과 구별되지 않는다.
    render(<HistorySkeleton />);
    expect(screen.getByRole("status")).toHaveAccessibleName("이전 대화를 불러오는 중");
  });

  it("말풍선이 여러 개인 것처럼 보인다", () => {
    // 하나만 두면 "메시지 하나가 오는 중"으로 읽힌다 — 복원 중임을 예감하게
    // 하려면 여러 줄이어야 한다.
    render(<HistorySkeleton />);
    expect(screen.getAllByTestId("skeleton-line")).toHaveLength(3);
  });
});
