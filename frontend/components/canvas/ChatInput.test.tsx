// frontend/components/canvas/ChatInput.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInput } from "./ChatInput";

describe("ChatInput", () => {
  it("sends typed text and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByLabelText("채팅 메시지 입력");
    await user.type(box, "승인");
    await user.click(screen.getByRole("button", { name: "전송" }));
    expect(onSend).toHaveBeenCalledWith("승인");
    expect(box).toHaveValue("");
  });

  it("does not send while disabled", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={true} />);
    await user.type(screen.getByLabelText("채팅 메시지 입력"), "안녕");
    await user.click(screen.getByRole("button", { name: "전송" }));
    expect(onSend).not.toHaveBeenCalled();
  });
});
