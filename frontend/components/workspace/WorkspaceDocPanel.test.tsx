import { describe, it, expect, vi } from "vitest";
import { render, screen, act, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { WorkspaceDocPanel } from "./WorkspaceDocPanel";

const DOC = { path: "aiplc-docs/discovery/discovery-document.md", version: "v2" };
const PRFAQ = { path: "aiplc-docs/discovery/envision/prfaq.md", version: null };
// Drawer open — the default for tests that are not about opening and closing.
const OPEN = { open: true, onOpenChange: () => {}, onCurrentDocChange: () => {} };

describe("WorkspaceDocPanel", () => {
  it("shows an empty-state (no fetch) when there is no document yet", async () => {
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} changedPaths={[]} turnSeq={0} />);
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
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
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
    // ui-bug2 regression: prfaq.md is created with file_write alone, without submit_document.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${PRFAQ.path}`, () =>
        HttpResponse.json({ content: "# PR/FAQ\n\n초안" }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={PRFAQ} changedPaths={[]} turnSeq={0} />);
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
    const { rerender } = render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
    expect(await screen.findByText("초안")).toBeInTheDocument();
    expect(hits).toBe(1);

    // When the conversation moves to a different document, read that document.
    await act(async () => {
      rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={PRFAQ} changedPaths={[]} turnSeq={0} />);
    });
    expect(await screen.findByText("PR/FAQ")).toBeInTheDocument();
    await waitFor(() => expect(hits).toBe(2));
  });

  it("re-reads the same file when turnSeq advances (턴 종료 후 동기화 보정)", async () => {
    // ui-bug2 regression: at the time of the document event, the VM→S3 sync has not happened, so the first
    // read may be empty — when the turn ends (turnSeq increments), it must be read again.
    let served = ""; // first read: before sync (empty document)
    let hits = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () => {
        hits++;
        return HttpResponse.json({ content: served });
      }),
    );
    const { rerender } = render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
    expect(await screen.findByText(/문서 내용이 아직 비어 있습니다/)).toBeInTheDocument();
    expect(hits).toBe(1);

    served = "# 동기화 완료\n\n내용 도착";
    await act(async () => {
      rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={1} />);
    });
    expect(await screen.findByText("동기화 완료")).toBeInTheDocument();
    await waitFor(() => expect(hits).toBe(2));
  });

  it("says a 404 document is not saved yet, distinctly from an empty one", async () => {
    // Previously a 404 rendered as "문서 내용이 아직 비어 있습니다", which is what
    // made the sync bug invisible: the chat said the document was created, the
    // panel showed it as merely empty, and only a refresh revealed it had never
    // reached S3. A missing document and an empty document are different states.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
    });
    expect(await screen.findByText(/아직 저장되지 않은 문서입니다/)).toBeInTheDocument();
    expect(screen.queryByText(/문서 내용이 아직 비어 있습니다/)).not.toBeInTheDocument();
    // Still not a hard load error — a 404 mid-turn is expected.
    expect(screen.queryByText(/불러오지 못했습니다/)).not.toBeInTheDocument();
  });

  it("still shows the empty-document note when the file exists but is blank", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ content: "   " }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
    });
    expect(await screen.findByText(/문서 내용이 아직 비어 있습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/아직 저장되지 않은 문서입니다/)).not.toBeInTheDocument();
  });

  it("surfaces a load error on a non-404 failure", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/files/${DOC.path}`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0} />);
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
    render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: "v1" }} changedPaths={[]} turnSeq={0} />);
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
    render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} changedPaths={[]} turnSeq={0} />);
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
      <WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} changedPaths={[]} turnSeq={0} />);
    const select = await screen.findByLabelText("문서 선택");
    // Even if the user has selected one manually…
    await userEvent.setup().selectOptions(select, "aiplc-docs/a.md");
    // …a new document event (activeDoc change) switches to that document
    rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/b.md", version: "v2" }} changedPaths={[]} turnSeq={1} />);
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
    render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/new.md", version: null }} changedPaths={[]} turnSeq={0} />);
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
    render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={{ path: "aiplc-docs/a.md", version: null }} changedPaths={[]} turnSeq={0} />);
    await screen.findByLabelText("문서 선택");
    // It is a real regression guard only if it verifies that the select remains and the current document
    // is kept after the artifacts response ([]) has been applied — asserting before the fetch finishes
    // makes it a fake guard that passes even if the union logic disappears.
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
        <WorkspaceDocPanel {...OPEN}
          projectId="p1"
          activeDoc={{ path: "aiplc-docs/a.md", version: null }}
          changedPaths={[]} turnSeq={0}
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
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} changedPaths={[]} turnSeq={0} />);
    });
    expect(screen.queryByRole("button", { name: "문서 새로고침" })).not.toBeInTheDocument();
  });
});

// ---- A/G/I: syncing the list and the viewer (2026-08-21) ----
//
// **Two measured symptoms.** (1) Recent artifacts showed file names, but the viewer body said "아직
// 생성된 문서가 없습니다". (2) In some cases the dropdown itself did not appear.
//
// There are three causes:
//   A The body path was `manualPath ?? activeDoc?.path ?? null`, with no fallback to the list —
//     the dropdown is filled from `listed`, so "options exist but the body is empty" was
//     structurally possible. After a refresh `activeDoc` is null, so it is always in that state.
//   G The list query key was `[projectId, turnSeq, activeDoc?.path]`. There was no real-time signal
//     that a file changed (`file_changed` → changedPaths), so in a turn that writes only audit, state,
//     and question files, the list stays at its mount-time value (an empty array) until the turn ends.
//   I A failed list query was symptomless — nobody read `artifacts.error`, so the screen looked
//     the same as "there are no documents".

describe("WorkspaceDocPanel — 목록과의 동기화", () => {
  it("activeDoc이 없으면 목록의 첫 항목을 연다 (A)", async () => {
    // The backend returns newest first (Workspace.list_artifacts) — the first item is the newest.
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ artifacts: [PRFAQ.path, "aiplc-docs/audit.md"] }),
      ),
      http.get(`${API_BASE_URL}/projects/p1/files/${PRFAQ.path}`, () =>
        HttpResponse.json({ content: "# PRFAQ 제목" }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                changedPaths={[]} />);
    });
    expect(await screen.findByText("PRFAQ 제목")).toBeInTheDocument();
    expect(screen.queryByText(/아직 생성된 문서가 없습니다/)).not.toBeInTheDocument();
  });

  it("changedPaths가 바뀌면 목록을 다시 조회한다 (G)", async () => {
    let calls = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () => {
        calls += 1;
        return HttpResponse.json({ artifacts: [] });
      }),
    );
    const { rerender } = await act(async () =>
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                changedPaths={[]} />));
    const first = calls;

    // A file was written mid-turn — turnSeq has not gone up yet and there is no activeDoc.
    await act(async () => {
      rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                  changedPaths={["aiplc-docs/audit.md"]} />);
    });

    // It waits briefly before querying, to batch writes that come in quick succession.
    await waitFor(() => expect(calls).toBeGreaterThan(first));
  });

  it("연달아 쓰인 파일은 목록 재조회 한 번으로 묶인다", async () => {
    let calls = 0;
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () => {
        calls += 1;
        return HttpResponse.json({ artifacts: [] });
      }),
    );
    const { rerender } = await act(async () =>
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                changedPaths={[]} />));
    const first = calls;

    const written: string[] = [];
    for (const p of ["aiplc-docs/audit.md", "aiplc-docs/aiplc-state.md",
                     "aiplc-docs/x-questions.md"]) {
      written.push(p);
      await act(async () => {
        rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                    changedPaths={[...written]} />);
      });
    }

    await waitFor(() => expect(calls).toBe(first + 1));
    // It does not grow even after waiting longer.
    await new Promise((r) => setTimeout(r, 600));
    expect(calls).toBe(first + 1);
  });

  it("목록 조회가 실패하면 그렇다고 말한다 (I)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/artifacts`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    await act(async () => {
      render(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={null} turnSeq={0}
                                changedPaths={[]} />);
    });
    // Mashing it into "there are no documents" means never seeing the cause — the same discipline
    // by which this repo split docUnsaved and docEmpty.
    expect(await screen.findByText(/목록을 불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/아직 생성된 문서가 없습니다/)).not.toBeInTheDocument();
  });
});

describe("WorkspaceDocPanel — 드로어", () => {
  const closed = (onOpenChange = () => {}) =>
    ({ open: false, onOpenChange, onCurrentDocChange: () => {} });

  it("닫혀 있으면 본문은 보조 기술에서 숨고 손잡이 탭만 남는다", async () => {
    const onOpenChange = vi.fn();
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                                {...closed(onOpenChange)} />);
    });
    expect(screen.queryByRole("combobox")).toBeNull();
    const tab = screen.getByRole("button", { name: "생성된 문서 열기" });
    expect(tab).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(tab);
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it("열리면 ✕·Esc·바깥 클릭·손잡이 모두 닫는다", async () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                         {...closed(onOpenChange)} />,
    );
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                                  open={true} onOpenChange={onOpenChange} onCurrentDocChange={() => {}} />);
    });
    const [tab, close] = screen.getAllByRole("button", { name: "생성된 문서 닫기" });
    expect(tab).toHaveAttribute("aria-expanded", "true");
    // Closed → open moves focus inside the drawer (✕).
    expect(close).toHaveFocus();
    await userEvent.click(close);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByTestId("doc-drawer-backdrop"));
    await userEvent.click(tab);
    expect(onOpenChange.mock.calls).toEqual([[false], [false], [false], [false]]);
  });

  // At the moment of opening, the ✕ is still sliding in from off screen. A plain focus() made the browser
  // scroll the page root sideways, so the whole screen shifted and bounced back (measured: scrollLeft 648).
  // jsdom has no layout, so we pin the cause side — preventScroll.
  it("포커스를 옮길 때 화면을 스크롤하지 않는다 (열 때·닫을 때 모두)", async () => {
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    try {
      const { rerender } = render(
        <WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                           {...closed()} />,
      );
      await act(async () => {
        rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={DOC} changedPaths={[]}
                                    turnSeq={0} />);
      });
      await act(async () => {
        rerender(<WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                                    {...closed()} />);
      });
      expect(focus).toHaveBeenCalledTimes(2);
      for (const call of focus.mock.calls) expect(call[0]).toEqual({ preventScroll: true });
    } finally {
      focus.mockRestore();
    }
  });

  // A covering drawer opening on its own while answering would hide the question form — only the tab changes.
  it("닫힌 동안 새 문서가 오면 탭이 '새 문서'로 바뀌고 저절로 열지 않는다", async () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <WorkspaceDocPanel projectId="p1" activeDoc={DOC} changedPaths={[]} turnSeq={0}
                         {...closed(onOpenChange)} />,
    );
    const tab = () => screen.getByRole("button", { name: "생성된 문서 열기" });
    expect(tab()).not.toHaveTextContent("새 문서");
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" activeDoc={PRFAQ} changedPaths={[]} turnSeq={0}
                                  {...closed(onOpenChange)} />);
    });
    expect(tab()).toHaveTextContent("새 문서");
    expect(onOpenChange).not.toHaveBeenCalled();

    // After opening and closing it again, it returns to the usual tab.
    await act(async () => {
      rerender(<WorkspaceDocPanel {...OPEN} projectId="p1" activeDoc={PRFAQ} changedPaths={[]}
                                  turnSeq={0} />);
    });
    await act(async () => {
      rerender(<WorkspaceDocPanel projectId="p1" activeDoc={PRFAQ} changedPaths={[]} turnSeq={0}
                                  {...closed(onOpenChange)} />);
    });
    expect(tab()).not.toHaveTextContent("새 문서");
    expect(tab()).toHaveTextContent("생성된 문서");
  });

  it("지금 보여 줄 문서 경로를 부모에게 알린다 (질문 폼의 '{이름} 보기')", async () => {
    const onCurrentDocChange = vi.fn();
    await act(async () => {
      render(<WorkspaceDocPanel projectId="p1" activeDoc={PRFAQ} changedPaths={[]} turnSeq={0}
                                open={false} onOpenChange={() => {}}
                                onCurrentDocChange={onCurrentDocChange} />);
    });
    expect(onCurrentDocChange).toHaveBeenLastCalledWith(PRFAQ.path);
  });
});
