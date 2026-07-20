import { describe, it, expect } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { WorkspaceDocPanel } from "./WorkspaceDocPanel";

const DOC = { path: "aiplc-docs/discovery/discovery-document.md", version: "v2", summary: "" };

describe("WorkspaceDocPanel", () => {
  it("shows an empty-state (no fetch) when there is no document yet", async () => {
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" lastDocument={null} />);
    });
    expect(screen.getByText(/아직 생성된 문서가 없습니다/)).toBeInTheDocument();
    // No file name / version chip / review link when there's nothing to show.
    expect(screen.queryByRole("link", { name: /전체 문서 리뷰/ })).not.toBeInTheDocument();
  });

  it("fetches and renders the document, its name, and a version chip", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ content: "# 제목\n\n본문 텍스트" }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" lastDocument={DOC} />);
    });
    expect(await screen.findByText("제목")).toBeInTheDocument();
    expect(screen.getByText(/discovery-document\.md/)).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /전체 문서 리뷰/ })).toHaveAttribute(
      "href",
      "/projects/p1/review",
    );
  });

  it("re-reads the file when the version changes (auto-refresh on a new document)", async () => {
    let served = "# 초안\n\n첫 버전";
    let hits = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () => {
        hits++;
        return HttpResponse.json({ content: served });
      }),
    );
    const { rerender } = render(<WorkspaceDocPanel projectId="p1" lastDocument={DOC} />);
    expect(await screen.findByText("초안")).toBeInTheDocument();
    expect(hits).toBe(1);

    // A new version of the SAME path must trigger a re-read.
    served = "# 개정\n\n둘째 버전";
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" lastDocument={{ ...DOC, version: "v3" }} />);
    });
    expect(await screen.findByText("개정")).toBeInTheDocument();
    await waitFor(() => expect(hits).toBe(2));
  });

  it("treats a 404 as an empty document rather than a load error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" lastDocument={DOC} />);
    });
    expect(await screen.findByText(/문서 내용이 아직 비어 있습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/불러오지 못했습니다/)).not.toBeInTheDocument();
  });

  it("surfaces a load error on a non-404 failure", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" lastDocument={DOC} />);
    });
    expect(await screen.findByText(/문서를 불러오지 못했습니다/)).toBeInTheDocument();
  });
});
