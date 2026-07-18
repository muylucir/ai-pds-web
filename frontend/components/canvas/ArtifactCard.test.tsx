import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactCard } from "./ArtifactCard";

describe("ArtifactCard", () => {
  it("renders the mockup's verbatim title and opens the panel on click", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ArtifactCard path="aiplc-docs/discovery/discovery-document.md" onOpen={onOpen} />);
    expect(screen.getByText("discovery-document.md — Part 1: Envision")).toBeInTheDocument();
    expect(screen.getByText("패널에서 열기 →")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /우측 패널에서 열기/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
