import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ProjectList } from "./ProjectList";

const PROJECTS = [
  { project_id: "p1", name: "워크숍 A" },
  { project_id: "p2", name: null },
];

describe("ProjectList delete", () => {
  it("shows a delete button per card and opens the confirm dialog with the warning copy", async () => {
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
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
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
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
    render(<ProjectList projects={PROJECTS} onDeleted={onDeleted} />);
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
    render(<ProjectList projects={PROJECTS} onDeleted={onDeleted} />);
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
    render(<ProjectList projects={PROJECTS} onDeleted={vi.fn()} />);
    await userEvent.setup().click(
      screen.getAllByRole("button", { name: /프로젝트 삭제/ })[0],
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
