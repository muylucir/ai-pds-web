import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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

  // TempPasswordPanel 자체는 보이는 동안 항상 비밀번호를 렌더링한다 — "다시 볼 수
  // 없다"는 계약은 부모가 onClose에서 패널을 걷어내는 것으로 지켜진다. 그 계약을
  // 실제로 검증하려면 onClose에서 언마운트하는 최소한의 호스트가 필요하다.
  function ClosableHost({ password }: { password: string }) {
    const [open, setOpen] = useState(true);
    if (!open) return null;
    return <TempPasswordPanel email="new@x.io" password={password}
                              onClose={() => setOpen(false)} />;
  }

  it("removes the password from the document once the host closes on onClose", async () => {
    render(<ClosableHost password="Ab3!xyz789QWERty" />);
    expect(screen.getByText("Ab3!xyz789QWERty")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(screen.queryByText("Ab3!xyz789QWERty")).not.toBeInTheDocument();
  });
});
