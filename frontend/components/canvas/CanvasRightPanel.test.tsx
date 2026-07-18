import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { CanvasRightPanel } from "./CanvasRightPanel";
import { discoveryDocument } from "@/test/fixtures/discoveryDocument";

describe("CanvasRightPanel", () => {
  it("renders the Document tab's content and marks it selected when tab='document'", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: discoveryDocument })),
    );
    await act(async () => {
      render(
        <CanvasRightPanel
          projectId="pilot1"
          tab="document"
          onTabChange={vi.fn()}
          onApprove={vi.fn()}
          onRevise={vi.fn()}
          busy={false}
        />,
      );
    });
    expect(await screen.findByText("Press Release")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "문서" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "프리뷰" })).toHaveAttribute("aria-selected", "false");
  });

  it("renders the Preview tab's deferred placeholder when tab='preview' (no document fetch)", () => {
    render(
      <CanvasRightPanel
        projectId="pilot1"
        tab="preview"
        onTabChange={vi.fn()}
        onApprove={vi.fn()}
        onRevise={vi.fn()}
        busy={false}
      />,
    );
    expect(screen.getByText("프로토타입 빌드 대기 중")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "프리뷰" })).toHaveAttribute("aria-selected", "true");
  });

  it("clicking a tab calls onTabChange with the clicked tab", async () => {
    const onTabChange = vi.fn();
    render(
      <CanvasRightPanel
        projectId="pilot1"
        tab="document"
        onTabChange={onTabChange}
        onApprove={vi.fn()}
        onRevise={vi.fn()}
        busy={false}
      />,
    );
    // getDocument fires but this test doesn't await it — only the click matters.
    server.use(http.get(`${API_BASE_URL}/projects/pilot1/document`, () => HttpResponse.json({ markdown: "" })));
    await userEvent.click(screen.getByRole("tab", { name: "프리뷰" }));
    expect(onTabChange).toHaveBeenCalledWith("preview");
  });
});
