import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalGate } from "./ApprovalGate";

describe("ApprovalGate", () => {
  it("fires onApprove when the approve button is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<ApprovalGate onApprove={onApprove} onRevise={vi.fn()} busy={false} />);
    await user.click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("reveals the revision textarea and submits natural-language text", async () => {
    const user = userEvent.setup();
    const onRevise = vi.fn();
    render(<ApprovalGate onApprove={vi.fn()} onRevise={onRevise} busy={false} />);
    await user.click(screen.getByRole("button", { name: /수정 요청/ }));
    await user.type(screen.getByLabelText(/수정 요청 사항/), "FAQ에 다국어 지원 항목 추가해줘");
    await user.click(screen.getByRole("button", { name: /수정 요청 제출/ }));
    expect(onRevise).toHaveBeenCalledWith("FAQ에 다국어 지원 항목 추가해줘");
  });

  it("disables actions while busy", () => {
    render(<ApprovalGate onApprove={vi.fn()} onRevise={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeDisabled();
  });
});
