import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

describe("ProjectList delete", () => {
  it("shows a delete button per card and opens the confirm dialog with the warning copy", async () => {
    render(<ProjectList data={PAGE} onDeleted={vi.fn()} onPageChange={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: /프로젝트 삭제/ });
    expect(buttons).toHaveLength(2);

    await userEvent.setup().click(buttons[0]);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("'워크숍 A' 프로젝트 삭제");
    expect(dialog).toHaveTextContent(
      "채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.",
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
    // 버튼은 Link 밖에 있으므로 클릭이 내비게이션(링크 href 이동)을 유발하지
    // 않아야 한다 — jsdom에서는 dialog가 열렸고 링크 클릭 핸들러가 없음을 확인.
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
    expect(screen.getByText("—")).toBeInTheDocument();          // progress null
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
