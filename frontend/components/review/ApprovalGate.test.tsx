import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalGate } from "./ApprovalGate";

describe("ApprovalGate", () => {
  it("수정 요청은 워크스페이스 채팅으로 가는 링크다", () => {
    render(
      <ApprovalGate
        onApprove={vi.fn()}
        busy={false}
        stageStatus={null}
        reviseHref="/projects/p1/workspace?draft=discovery-document.md%20%EC%88%98%EC%A0%95%20%EC%9A%94%EC%B2%AD%3A%20"
      />,
    );
    const link = screen.getByRole("link", { name: /수정 요청/ });
    expect(link).toHaveAttribute(
      "href",
      "/projects/p1/workspace?draft=discovery-document.md%20%EC%88%98%EC%A0%95%20%EC%9A%94%EC%B2%AD%3A%20",
    );
  });

  it("승인 버튼은 그대로 onApprove를 호출한다", async () => {
    const onApprove = vi.fn();
    render(<ApprovalGate onApprove={onApprove} busy={false} stageStatus={null} reviseHref="/x" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /승인하고 다음 단계로/ }));
    expect(onApprove).toHaveBeenCalled();
  });

  it("disables the approve button while busy", () => {
    render(<ApprovalGate onApprove={vi.fn()} busy={true} stageStatus={null} reviseHref="/x" />);
    expect(screen.getByRole("button", { name: /승인하고 다음 단계로/ })).toBeDisabled();
  });
});
