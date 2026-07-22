import { describe, it, expect } from "vitest";
import { render, screen, act, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("WorkspaceDocPanel — 문서 드롭다운", () => {
  it("산출물 목록이 드롭다운 옵션으로 뜬다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/discovery/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: "v1" }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["a.md", "b.md"]);
    expect((select as HTMLSelectElement).value).toBe("aiplc-docs/a.md");
  });

  it("드롭다운으로 다른 문서를 고르면 그 문서를 로드한다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/b.md`, () =>
        HttpResponse.json({ content: "# B-내용" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    await userEvent.setup().selectOptions(select, "aiplc-docs/b.md");
    expect(await screen.findByText("B-내용")).toBeInTheDocument();
  });

  it("새 activeDoc 이벤트가 오면 자동으로 그 문서로 전환한다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/a.md", "aiplc-docs/b.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/b.md`, () =>
        HttpResponse.json({ content: "# B-내용" })),
    );
    const { rerender } = render(
      <WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    // 사용자가 수동 선택해 두어도…
    await userEvent.setup().selectOptions(select, "aiplc-docs/a.md");
    // …새 문서 이벤트(activeDoc 변경)는 그 문서로 전환한다
    rerender(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/b.md", version: "v2" }} turnSeq={1} />);
    expect(await screen.findByText("B-내용")).toBeInTheDocument();
    expect((screen.getByLabelText("문서 선택") as HTMLSelectElement).value).toBe("aiplc-docs/b.md");
  });

  it("artifacts 목록에 없는 activeDoc(턴 중 새 문서)도 선택지에 추가되어 선택 상태가 맞는다", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: ["aiplc-docs/old.md"] })),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/new.md`, () =>
        HttpResponse.json({ content: "# NEW" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/new.md", version: null }} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe("aiplc-docs/new.md"));
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toContain("new.md");
  });

  it("artifacts가 빈 배열로 응답해도 열려 있는 문서는 드롭다운에 남는다", async () => {
    let artifactsFetched = false;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () => {
        artifactsFetched = true;
        return HttpResponse.json({ artifacts: [] });
      }),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () =>
        HttpResponse.json({ content: "# A" })),
    );
    render(<WorkspaceDocPanel projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} turnSeq={0} />);
    await screen.findByLabelText("문서 선택");
    // artifacts 응답([])이 반영된 뒤에도 select가 남아 있고 현재 문서가 유지되는지
    // 를 검증해야 진짜 회귀 가드가 된다 — fetch가 끝나기 전 단언하면 union 로직이
    // 사라져도 통과하는 가짜 가드가 된다.
    await waitFor(() => expect(artifactsFetched).toBe(true));
    const select = screen.getByLabelText("문서 선택") as HTMLSelectElement;
    expect(select.value).toBe("aiplc-docs/a.md");
    expect(await screen.findByText("A")).toBeInTheDocument();
  });
});

describe("WorkspaceDocPanel — 문서 새로고침 버튼", () => {
  it("클릭하면 현재 문서와 산출물 목록을 다시 가져온다", async () => {
    let docFetches = 0;
    let listFetches = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () => {
        listFetches++;
        return HttpResponse.json({ artifacts: ["aiplc-docs/a.md"] });
      }),
      http.get(`${API_BASE_URL}/projects/p1/files/aiplc-docs/a.md`, () => {
        docFetches++;
        return HttpResponse.json({ content: `# 갱신${docFetches}` });
      }),
    );
    await act(async () => {
      render(
        <WorkspaceDocPanel
          projectId="p1"
          activeDoc={{ path: "aiplc-docs/a.md", version: null }}
          turnSeq={0}
        />,
      );
    });
    expect(await screen.findByText("갱신1")).toBeInTheDocument();
    const before = { doc: docFetches, list: listFetches };

    await userEvent.setup().click(screen.getByRole("button", { name: "문서 새로고침" }));

    await waitFor(() => {
      expect(docFetches).toBe(before.doc + 1);
      expect(listFetches).toBe(before.list + 1);
    });
    expect(await screen.findByText(`갱신${before.doc + 1}`)).toBeInTheDocument();
  });

  it("문서가 없는 빈 상태에서는 새로고침 버튼이 없다", async () => {
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={null} turnSeq={0} />);
    });
    expect(screen.queryByRole("button", { name: "문서 새로고침" })).not.toBeInTheDocument();
  });
});
