import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ProjectList } from "./ProjectList";

const PAGE = {
  projects: [
    { project_id: "p1", name: "워크숍 A", progress: { current_stage: "Envision", completed: 2, total: 8 } },
    { project_id: "p2", name: null, progress: null },
  ],
  total: 2, page: 1, size: 10,
};

describe("ProjectList 메타데이터 열", () => {
  const META = {
    projects: [
      {
        project_id: "p1", name: "워크숍 A", created_at: "2026-08-01T09:30:00+00:00",
        model_id: "global.anthropic.claude-opus-5", language: "ko" as const,
        progress: null,
      },
      {
        project_id: "p2", name: "Workshop B", created_at: "2026-07-15T00:00:00+00:00",
        model_id: "global.anthropic.claude-sonnet-5", language: "en" as const,
        progress: null,
      },
    ],
    total: 2, page: 1, size: 10,
  };

  function models() {
    server.use(
      http.get(`${API_BASE_URL}/models`, () =>
        HttpResponse.json({
          models: [{ name: "Opus 5", model_id: "global.anthropic.claude-opus-5" }],
        })),
    );
  }

  it("shows the catalog display name for a known model", async () => {
    models();
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(await screen.findByText("Opus 5")).toBeInTheDocument();
  });

  it("falls back to the raw model id when the catalog has no entry", async () => {
    // A project running on a model the admin deleted from the catalog is a normal path —
    // show the raw id, under the same discipline as the header badge.
    models();
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(await screen.findByText("global.anthropic.claude-sonnet-5")).toBeInTheDocument();
  });

  it("still renders rows when the model catalog fails", async () => {
    server.use(http.get(`${API_BASE_URL}/models`, () => HttpResponse.error()));
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    // Without the catalog we just cannot attach names; the row and id must still show.
    expect(await screen.findByText("global.anthropic.claude-opus-5")).toBeInTheDocument();
  });

  it("labels each language in that language, not the UI language", () => {
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText("한국어")).toBeInTheDocument();
    expect(screen.getByText("English")).toBeInTheDocument();
  });

  it("shows the creation date as a plain day", () => {
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText("2026-08-01")).toBeInTheDocument();
    expect(screen.getByText("2026-07-15")).toBeInTheDocument();
  });

  it("renders a dash for projects missing this metadata", () => {
    // A project restored from an old manifest — show absence with —, not a blank cell.
    const bare = {
      projects: [{ project_id: "old", name: "구 프로젝트", progress: null }],
      total: 1, page: 1, size: 10,
    };
    render(<ProjectList data={bare} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const row = screen.getByText("구 프로젝트").closest("tr")!;
    // All 5 cells (progress, model, language, owner, created date) are —
    expect(row.textContent?.match(/—/g)?.length).toBe(5);
  });

  it("has a header for each new column", () => {
    render(<ProjectList data={META} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("columnheader", { name: "모델" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "언어" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "생성일" })).toBeInTheDocument();
  });
});

describe("ProjectList 접근", () => {
  const SHARED = {
    projects: [
      { project_id: "mine", name: "내 것", progress: null, owner: "kim", access: "owner" as const,
        members: ["lee"] },
      { project_id: "seen", name: "열람", progress: null, owner: "park", access: "member" as const },
    ],
    total: 2, page: 1, size: 10,
  };

  it("shows the owner and marks projects the user only views", () => {
    render(<ProjectList data={SHARED} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const seen = screen.getByText("열람", { selector: "a" }).closest("tr")!;
    expect(seen).toHaveTextContent("park");
    expect(within(seen).getByText("열람", { selector: "span" })).toBeInTheDocument();
  });

  it("gives viewers no delete, export, or access buttons", () => {
    render(<ProjectList data={SHARED} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const seen = screen.getByText("열람", { selector: "a" }).closest("tr")!;
    expect(within(seen).queryAllByRole("button")).toHaveLength(0);
    const mine = screen.getByText("내 것").closest("tr")!;
    expect(within(mine).getAllByRole("button")).toHaveLength(3);
  });

  it("opens access management with the current viewers", async () => {
    render(<ProjectList data={SHARED} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /내 것 접근 관리/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("lee");
    // Only admins change the owner.
    expect(within(dialog).queryByRole("button", { name: "소유자 변경" })).toBeNull();
  });
});

describe("ProjectList delete", () => {
  it("shows a delete button per card and opens the confirm dialog with the warning copy", async () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: /프로젝트 삭제/ });
    expect(buttons).toHaveLength(2);

    await userEvent.setup().click(buttons[0]);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("'워크숍 A' 프로젝트 삭제");
    // The wording must state the actual deletion scope — prototype builds and survey responses,
    // and also that already-shared links break with them (the backend now runs the reset path
    // per slug, deleting the artifacts and the tokens).
    expect(dialog).toHaveTextContent(
      "채팅 기록·문서·프로토타입 빌드·설문 응답이 영구 삭제되고, " +
      "공유한 프리뷰·설문 링크가 즉시 끊깁니다. 되돌릴 수 없습니다.",
    );
  });

  it("cancel closes the dialog without calling DELETE", async () => {
    let called = 0;
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () => {
        called++;
        return HttpResponse.json({ deleted: true });
      }),
    );
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(called).toBe(0);
  });

  it("confirm calls DELETE and then onDeleted", async () => {
    let called = 0;
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () => {
        called++;
        return HttpResponse.json({ deleted: true });
      }),
    );
    const onDeleted = vi.fn();
    render(<ProjectList data={PAGE} onDeleted={onDeleted} onPageChange={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "삭제" }));
    expect(called).toBe(1);
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows an error and keeps the dialog when DELETE fails", async () => {
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1`, () =>
        HttpResponse.json({ detail: "project delete failed" }, { status: 500 }),
      ),
    );
    const onDeleted = vi.fn();
    render(<ProjectList data={PAGE} onDeleted={onDeleted} onPageChange={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0]);
    await user.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByText(/삭제에 실패했습니다/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("delete button does not navigate the card link", async () => {
    // The button is outside the Link, so a click must not trigger navigation (following the
    // link's href) — in jsdom, confirm the dialog opened and there is no link click handler.
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    await userEvent.setup().click(
      screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0],
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("ProjectList table + pagination", () => {
  it("테이블에 ID·이름·진행상황 컬럼과 행 데이터를 렌더한다", () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "프로젝트 ID" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "프로젝트명" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "진행상황" })).toBeInTheDocument();
    expect(screen.getByText("p1")).toBeInTheDocument();
    expect(screen.getByText("워크숍 A")).toBeInTheDocument();
    expect(screen.getByText("Envision (2/8)")).toBeInTheDocument();
    // progress null → —. The model, language, and created-date columns also use — when empty,
    // so narrow to the row and look only at the progress cell (3rd td). p2 has a null name, so
    // it appears in both the id cell and the name link; take the row as the table's 2nd tr.
    const rows = screen.getAllByRole("row");
    expect(rows[2].querySelectorAll("td")[2]).toHaveTextContent("—");
  });

  it("이름 링크는 대시보드로 가고, 이름 없으면 ID를 링크 텍스트로 쓴다", () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("link", { name: "워크숍 A" })).toHaveAttribute(
      "href", "/projects/p1/dashboard");
    expect(screen.getByRole("link", { name: "p2" })).toHaveAttribute(
      "href", "/projects/p2/dashboard");
  });

  it("current_stage가 null이면 카운트만 보여준다", () => {
    const page = { ...PAGE, projects: [
      { project_id: "p3", name: null, progress: { current_stage: null, completed: 1, total: 4 } }] };
    render(<ProjectList data={page} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText("(1/4)")).toBeInTheDocument();
  });

  it("페이지네이션: 총 건수·현재/전체 페이지·이전/다음 버튼", async () => {
    const onPageChange = vi.fn();
    const page = { ...PAGE, total: 23, page: 2, size: 10 };
    render(<ProjectList data={page} onDeleted={vi.fn()} onPageChange={onPageChange} />);
    expect(screen.getByText("총 23건")).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "다음 페이지" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
    await userEvent.setup().click(screen.getByRole("button", { name: "이전 페이지" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("첫/마지막 페이지에서 이전/다음이 비활성화된다", () => {
    const first = { ...PAGE, total: 11, page: 1, size: 10 };
    const { rerender } = render(
      <ProjectList data={first} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "이전 페이지" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다음 페이지" })).toBeEnabled();
    rerender(<ProjectList data={{ ...first, page: 2 }} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "다음 페이지" })).toBeDisabled();
  });

  it("total이 0이면 빈 목록 문구를 보여준다", () => {
    render(<ProjectList data={{ projects: [], total: 0, page: 1, size: 10 }}
                        onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByText(/아직 생성된 프로젝트가 없습니다/)).toBeInTheDocument();
  });
});

describe("ProjectList export", () => {
  /** jsdom does not implement object URLs or anchor clicks. */
  function stubDownload() {
    const clicked: HTMLAnchorElement[] = [];
    (URL as unknown as { createObjectURL: unknown }).createObjectURL =
      vi.fn(() => "blob:fake");
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });
    return clicked;
  }

  afterEach(() => vi.restoreAllMocks());

  it("행마다 내보내기 버튼이 있고 그 프로젝트의 번들을 받는다", async () => {
    const clicked = stubDownload();
    let requested = "";
    server.use(http.get(`${API_BASE_URL}/projects/:pid/export`, ({ params }) => {
      requested = params.pid as string;
      return new HttpResponse(new Blob(["PK"]), {
        headers: { "Content-Disposition": 'attachment; filename="p2.zip"' },
      });
    }));
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: /프로젝트 내보내기/ });
    expect(buttons).toHaveLength(2);

    await userEvent.setup().click(buttons[1]);

    await waitFor(() => expect(clicked).toHaveLength(1));
    // A row without a name builds its label from the id — the second row is p2.
    expect(requested).toBe("p2");
    expect(clicked[0].download).toBe("p2.zip");
  });

  it("빌드 세션 중이면 그 이유를 문구로 보여준다", async () => {
    stubDownload();
    server.use(http.get(`${API_BASE_URL}/projects/p1/export`, () =>
      HttpResponse.json({ detail: "build_session_active" }, { status: 409 })));
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);

    await userEvent.setup().click(
      screen.getAllByRole("button", { name: /프로젝트 내보내기/ })[0]);

    // The project list does not have that card — so first say which tab to go to.
    await screen.findByText(/“프로토타입” 탭에서 해당 카드의 “세션 열기”/);
  });

  it("내보내기 버튼은 카드 링크를 타지 않는다", async () => {
    stubDownload();
    server.use(http.get(`${API_BASE_URL}/projects/:pid/export`, () =>
      new HttpResponse(new Blob(["PK"]))));
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);

    await userEvent.setup().click(
      screen.getAllByRole("button", { name: /프로젝트 내보내기/ })[0]);

    // A stretched link lies on top, so check whether the button was actually pressed rather
    // than z-10 and stopPropagation — the same discipline as the delete button.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
