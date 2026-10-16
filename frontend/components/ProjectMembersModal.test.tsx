import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { ProjectMembersModal } from "./ProjectMembersModal";

const OWNED = { project_id: "p1", name: "워크숍", owner: "kim", access: "owner" as const,
                members: ["lee"] };

describe("ProjectMembersModal", () => {
  it("adds a viewer by email and shows the username the server stored", async () => {
    let sent: unknown = null;
    server.use(
      http.put(`${API_BASE_URL}/projects/p1/members`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ project_id: "p1", owner: "kim", access: "owner",
                                   members: ["lee", "choi"] });
      }),
    );
    const onChanged = vi.fn();
    render(<ProjectMembersModal project={OWNED} onClose={vi.fn()} onChanged={onChanged} />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "열람 멤버 이메일" }), "Choi@example.com");
    await user.click(screen.getByRole("button", { name: "추가" }));
    await waitFor(() => expect(screen.getByText("choi")).toBeInTheDocument());
    expect(sent).toEqual({ members: ["lee", "Choi@example.com"] });
    expect(onChanged).toHaveBeenCalled();
  });

  it("removes a viewer", async () => {
    server.use(
      http.put(`${API_BASE_URL}/projects/p1/members`, () =>
        HttpResponse.json({ project_id: "p1", owner: "kim", access: "owner", members: [] })),
    );
    render(<ProjectMembersModal project={OWNED} onClose={vi.fn()} onChanged={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /lee 열람 멤버에서 제거/ }));
    await waitFor(() => expect(screen.getByText("열람 멤버가 없습니다.")).toBeInTheDocument());
  });

  it("lets an admin hand the project to another user", async () => {
    server.use(
      http.put(`${API_BASE_URL}/admin/projects/p1/owner`, () =>
        HttpResponse.json({ project_id: "p1", owner: "park", members: ["lee", "kim"] })),
    );
    render(<ProjectMembersModal project={{ ...OWNED, access: "admin" }} onClose={vi.fn()}
                                onChanged={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "새 소유자" }), "park@example.com");
    await user.click(screen.getByRole("button", { name: "소유자 변경" }));
    await waitFor(() => expect(screen.getByTestId("members-owner")).toHaveTextContent("park"));
    // 이전 소유자는 열람 멤버로 남는다.
    expect(within(screen.getByRole("list")).getByText("kim")).toBeInTheDocument();
  });

  it("shows the server's reason when a save is refused", async () => {
    server.use(
      http.put(`${API_BASE_URL}/projects/p1/members`, () =>
        HttpResponse.json({ detail: "member_invalid" }, { status: 400 })),
    );
    render(<ProjectMembersModal project={OWNED} onClose={vi.fn()} onChanged={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "열람 멤버 이메일" }), "bad value");
    await user.click(screen.getByRole("button", { name: "추가" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("쓸 수 없는 값");
  });
});
