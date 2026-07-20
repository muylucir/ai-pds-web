import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DocTree } from "./DocTree";

const PATHS = [
  "aiplc-docs/aiplc-state.md",
  "aiplc-docs/audit.md",
  "aiplc-docs/discovery/prfaq.md",
  "aiplc-docs/discovery/discovery-document.md",
];

describe("DocTree", () => {
  it("groups files by directory and highlights the selection", () => {
    render(<DocTree paths={PATHS} selected="aiplc-docs/discovery/prfaq.md" onSelect={vi.fn()} />);
    expect(screen.getByText("discovery")).toBeInTheDocument(); // directory group header
    expect(screen.getByRole("button", { name: /prfaq\.md/ })).toHaveAttribute("aria-current", "true");
  });

  it("selects a file on click", () => {
    const onSelect = vi.fn();
    render(<DocTree paths={PATHS} selected={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /audit\.md/ }));
    expect(onSelect).toHaveBeenCalledWith("aiplc-docs/audit.md");
  });
});
