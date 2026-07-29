// frontend/components/canvas/ChatInput.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

  it("does not send on the IME composition-commit Enter (Korean input)", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByLabelText("채팅 메시지 입력");
    await user.type(box, "안녕");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends on a plain (non-IME) Enter keydown", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByLabelText("채팅 메시지 입력");
    await user.type(box, "안녕");
    fireEvent.keyDown(box, { key: "Enter", isComposing: false });
    expect(onSend).toHaveBeenCalledWith("안녕");
  });

  it("does not render a clip button when onAttach is not given", () => {
    render(<ChatInput onSend={vi.fn()} disabled={false} />);
    expect(screen.queryByRole("button", { name: "파일 첨부" })).not.toBeInTheDocument();
  });

  it("selecting a file via the hidden file input calls onAttach and resets the input", async () => {
    const onAttach = vi.fn();
    const { container } = render(<ChatInput onSend={vi.fn()} disabled={false} onAttach={onAttach} />);
    const file = new File(["hello"], "note.md", { type: "text/markdown" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAttach).toHaveBeenCalledWith(file);
    expect(input.value).toBe("");
  });

  it("initialText가 있으면 프리필 + 포커스된다", () => {
    render(<ChatInput onSend={vi.fn()} disabled={false} initialText="doc.md 수정 요청: " />);
    const input = screen.getByLabelText("채팅 메시지 입력");
    expect(input).toHaveValue("doc.md 수정 요청: ");
    expect(input).toHaveFocus();
  });

  it("initialText가 없으면 기존과 동일 (빈 입력, 포커스 강제 없음)", () => {
    render(<ChatInput onSend={vi.fn()} disabled={false} />);
    expect(screen.getByLabelText("채팅 메시지 입력")).toHaveValue("");
  });

  it("shows three lines of input without scrolling", () => {
    // One row made every multi-line message a peephole: people write
    // 질문·수정요청 several sentences long here and had to scroll their own
    // draft to re-read it. The workspace and the prototype build panel share
    // this component, so this one value covers both screens.
    render(<ChatInput onSend={vi.fn()} disabled={false} />);
    expect(screen.getByLabelText("채팅 메시지 입력")).toHaveAttribute("rows", "3");
  });
});
