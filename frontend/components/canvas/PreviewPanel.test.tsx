import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PreviewPanel } from "./PreviewPanel";

afterEach(() => vi.unstubAllEnvs());

describe("PreviewPanel", () => {
  it("renders the deferred-build placeholder when no preview URL is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "");
    render(<PreviewPanel projectId="pilot1" />);
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument();
    expect(screen.queryByTitle("프로토타입 프리뷰")).not.toBeInTheDocument();
  });

  it("renders an iframe pointed at the seam URL when a preview base is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_PREVIEW_BASE_URL", "https://preview.example.com");
    render(<PreviewPanel projectId="pilot1" prototypeId="proto-1" />);
    const frame = screen.getByTitle("프로토타입 프리뷰") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(
      "https://preview.example.com/projects/pilot1/preview/proto-1",
    );
  });
});
