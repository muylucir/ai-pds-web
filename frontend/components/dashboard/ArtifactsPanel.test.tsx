import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ArtifactsPanel } from "./ArtifactsPanel";

describe("ArtifactsPanel", () => {
  it("lists artifact basenames and links the discovery document to the review screen", () => {
    render(
      <ArtifactsPanel
        projectId="pilot1"
        artifacts={[
          "aiplc-docs/discovery/discovery-document.md",
          "aiplc-docs/discovery/envision/pain-point-analysis.md",
        ]}
      />,
    );
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    expect(screen.getByText("pain-point-analysis.md")).toBeInTheDocument();
    const docLink = screen.getByRole("link", { name: /discovery-document\.md/ });
    expect(docLink).toHaveAttribute("href", "/projects/pilot1/review");
  });

  it("renders an empty state", () => {
    render(<ArtifactsPanel projectId="pilot1" artifacts={[]} />);
    expect(screen.getByText(/아직 생성된 산출물이 없습니다/)).toBeInTheDocument();
  });
});
