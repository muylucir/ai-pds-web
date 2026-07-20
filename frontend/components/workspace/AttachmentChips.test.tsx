import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AttachmentChips } from "./AttachmentChips";

describe("AttachmentChips", () => {
  it("renders one chip per path and removes on click", () => {
    const onRemove = vi.fn();
    render(<AttachmentChips paths={["uploads/의견.md", "uploads/설문-2.md"]} onRemove={onRemove} />);
    expect(screen.getByText("의견.md")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /제거/ })[0]);
    expect(onRemove).toHaveBeenCalledWith("uploads/의견.md");
  });

  it("renders nothing when empty", () => {
    const { container } = render(<AttachmentChips paths={[]} onRemove={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
