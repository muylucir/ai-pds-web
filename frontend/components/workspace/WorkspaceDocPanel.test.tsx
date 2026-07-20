import { describe, it, expect } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { WorkspaceDocPanel } from "./WorkspaceDocPanel";

const DOC = { path: "aiplc-docs/discovery/discovery-document.md", version: "v2" };
const PRFAQ = { path: "aiplc-docs/discovery/envision/prfaq.md", version: null };

describe("WorkspaceDocPanel", () => {
  it("shows an empty-state (no fetch) when there is no document yet", async () => {
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={null} turnSeq={0} />);
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
      render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={0} />);
    });
    expect(await screen.findByText("제목")).toBeInTheDocument();
    expect(screen.getByText(/discovery-document\.md/)).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /전체 문서 리뷰/ })).toHaveAttribute(
      "href",
      "/projects/p1/review",
    );
  });

  it("file_changed로만 추적된 문서(version 없음)도 렌더하고 버전 칩은 생략한다", async () => {
    // ui-bug2 회귀: prfaq.md는 submit_document 없이 file_write로만 생성된다.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${PRFAQ.path}`, () =>
        HttpResponse.json({ content: "# PR/FAQ\n\n초안" }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={PRFAQ} turnSeq={0} />);
    });
    expect(await screen.findByText("PR/FAQ")).toBeInTheDocument();
    expect(screen.getByText(/prfaq\.md/)).toBeInTheDocument();
    expect(screen.queryByText(/^v/)).not.toBeInTheDocument();
  });

  it("re-reads the file when the path changes (conversation moves to another doc)", async () => {
    let served = "# 초안\n\n첫 버전";
    let hits = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () => {
        hits++;
        return HttpResponse.json({ content: served });
      }),
      http.get(`${API_BASE_URL}/projects/p1/files/${PRFAQ.path}`, () => {
        hits++;
        return HttpResponse.json({ content: "# PR/FAQ\n\n둘째 문서" });
      }),
    );
    const { rerender } = render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={0} />);
    expect(await screen.findByText("초안")).toBeInTheDocument();
    expect(hits).toBe(1);

    // 대화가 다른 문서로 옮겨가면 그 문서를 읽는다.
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" activeDoc={PRFAQ} turnSeq={0} />);
    });
    expect(await screen.findByText("PR/FAQ")).toBeInTheDocument();
    await waitFor(() => expect(hits).toBe(2));
  });

  it("re-reads the same file when turnSeq advances (턴 종료 후 동기화 보정)", async () => {
    // ui-bug2 회귀: 문서 이벤트 시점에는 VM→S3 동기화 전이라 첫 읽기가 빈
    // 내용일 수 있다 — 턴이 끝나면(turnSeq 증가) 반드시 다시 읽어야 한다.
    let served = ""; // 첫 읽기: 아직 동기화 전 (빈 문서)
    let hits = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () => {
        hits++;
        return HttpResponse.json({ content: served });
      }),
    );
    const { rerender } = render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={0} />);
    expect(await screen.findByText(/문서 내용이 아직 비어 있습니다/)).toBeInTheDocument();
    expect(hits).toBe(1);

    served = "# 동기화 완료\n\n내용 도착";
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={1} />);
    });
    expect(await screen.findByText("동기화 완료")).toBeInTheDocument();
    await waitFor(() => expect(hits).toBe(2));
  });

  it("treats a 404 as an empty document rather than a load error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={0} />);
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
      render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} turnSeq={0} />);
    });
    expect(await screen.findByText(/문서를 불러오지 못했습니다/)).toBeInTheDocument();
  });
});
