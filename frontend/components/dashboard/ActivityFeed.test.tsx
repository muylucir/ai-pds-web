import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActivityFeed } from "./ActivityFeed";
import { auditEntries } from "@/test/fixtures/auditEntries";

describe("ActivityFeed", () => {
  it("shows newest entries first with Entry labels", () => {
    render(<ActivityFeed entries={auditEntries} />);
    const label = screen.getByText("Entry 34");
    expect(label).toBeInTheDocument();
    // newest (34) appears above oldest (Entry 1) in DOM order
    const all = screen.getAllByText(/^Entry \d+$/).map((e) => e.textContent);
    expect(all[0]).toBe("Entry 34");
  });

  it("renders the audit.md heading", () => {
    render(<ActivityFeed entries={auditEntries} />);
    expect(screen.getByText(/최근 활동/)).toBeInTheDocument();
    expect(screen.getByText(/audit\.md/)).toBeInTheDocument();
  });
});
