import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CanvasSidebar } from "./CanvasSidebar";
import { projectState } from "@/test/fixtures/projectState";

describe("CanvasSidebar", () => {
  it("renders every stage name from the backend state (nothing hardcoded)", () => {
    render(<CanvasSidebar state={projectState} />);
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("shows the completed/total count from the fixture", () => {
    render(<CanvasSidebar state={projectState} />);
    // projectState fixture: 5 completed of 8 (see Plan B Task 1)
    expect(screen.getByText(/5 \/ 8 스테이지/)).toBeInTheDocument();
  });
});
