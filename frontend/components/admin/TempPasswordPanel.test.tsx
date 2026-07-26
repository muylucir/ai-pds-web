import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TempPasswordPanel } from "./TempPasswordPanel";

describe("TempPasswordPanel", () => {
  it("shows the password and warns that it cannot be seen again", () => {
    render(<TempPasswordPanel email="new@x.io" password="Ab3!xyz789QWERty"
                              onClose={() => {}} />);
    expect(screen.getByText("Ab3!xyz789QWERty")).toBeInTheDocument();
    expect(screen.getByText("new@x.io")).toBeInTheDocument();
    // 서버가 저장하지 않으므로 이 경고가 없으면 관리자가 값을 잃는다.
    expect(screen.getByText(/다시 볼 수 없습니다/)).toBeInTheDocument();
  });

  it("copies the password to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<TempPasswordPanel email="new@x.io" password="pw-1" onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /복사/ }));
    expect(writeText).toHaveBeenCalledWith("pw-1");
    expect(await screen.findByText(/복사했습니다/)).toBeInTheDocument();
  });

  it("calls onClose when the confirm button is pressed", async () => {
    const onClose = vi.fn();
    render(<TempPasswordPanel email="new@x.io" password="pw-1" onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
