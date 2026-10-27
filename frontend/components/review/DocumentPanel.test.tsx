import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocumentPanel } from "./DocumentPanel";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

const PATH = "aiplc-docs/discovery/discovery-document.md";

describe("DocumentPanel", () => {
  it("renders the document markdown (headings + table)", () => {
    render(<DocumentPanel projectId="p1" path={PATH} markdown={discoveryDocument} />);
    expect(screen.getByText("Press Release")).toBeInTheDocument();
    expect(screen.getByText("담당자 간 결과 편차")).toBeInTheDocument(); // from the GFM table
  });

  it("shows an empty state when the document is empty", () => {
    render(<DocumentPanel projectId="p1" path={PATH} markdown="" />);
    expect(screen.getByText(/아직 작성된 문서가 없습니다/)).toBeInTheDocument();
  });
});
