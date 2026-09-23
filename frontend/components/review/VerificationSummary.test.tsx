import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VerificationSummary } from "./VerificationSummary";

describe("VerificationSummary", () => {
  it("offers the original audit.md when no entry could be read", async () => {
    // 빈 패널은 "기록이 없다"와 구별되지 않는다 — 원문으로 가는 길이 있어야 한다.
    const onViewRaw = vi.fn();
    render(<VerificationSummary entries={[]} onViewRaw={onViewRaw} />);
    await userEvent.click(screen.getByRole("button", { name: "원문 보기" }));
    expect(onViewRaw).toHaveBeenCalledTimes(1);
  });

  it("does not offer it when there is no audit.md to open", () => {
    render(<VerificationSummary entries={[]} />);
    expect(screen.queryByRole("button", { name: "원문 보기" })).not.toBeInTheDocument();
  });

  it("does not offer it once entries are read", () => {
    render(<VerificationSummary
      entries={[{ index: 1, timestamp: "t", user_input: "승인",
                  ai_response: "진행", context: null }]}
      onViewRaw={() => {}} />);
    expect(screen.queryByRole("button", { name: "원문 보기" })).not.toBeInTheDocument();
  });
});
