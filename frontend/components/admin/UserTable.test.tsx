import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import type { AdminUser } from "@/lib/api/adminUsers";
import { UserTable } from "./UserTable";

const USERS: AdminUser[] = [
  { username: "admin@pathfinder.local", email: "admin@pathfinder.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
  { username: "pm@pathfinder.local", email: "pm@pathfinder.local",
    role: "pm", status: "FORCE_CHANGE_PASSWORD", enabled: true,
    created_at: "2026-07-25T01:00:00+00:00" },
  { username: "off@x.io", email: "off@x.io", role: "pm",
    status: "CONFIRMED", enabled: false, created_at: "2026-07-25T02:00:00+00:00" },
];

function row(email: string) {
  return screen.getByRole("row", { name: new RegExp(email) });
}

describe("UserTable", () => {
  it("renders email, role and status for each user", () => {
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@pathfinder.local")).getByText("관리자")).toBeInTheDocument();
    expect(within(row("pm@pathfinder.local")).getByText("PM")).toBeInTheDocument();
    // 초대 직후 상태는 "비밀번호 변경 필요"로 읽혀야 한다.
    expect(within(row("pm@pathfinder.local")).getByText(/변경 필요/)).toBeInTheDocument();
    expect(within(row("off@x.io")).getByText("비활성")).toBeInTheDocument();
  });

  it("marks the current user so they know which row is theirs", () => {
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@pathfinder.local")).getByText(/나/)).toBeInTheDocument();
  });

  it("shows a role=null user as 역할 없음", () => {
    // 초대 롤백이 실패해 남은 반쪽 계정을 관리자가 알아볼 수 있어야 한다.
    render(<UserTable users={[{ ...USERS[1], role: null }]} currentEmail={null}
                      onChanged={() => {}} />);
    expect(screen.getByText("역할 없음")).toBeInTheDocument();
  });

  it("changes a role and reloads", async () => {
    const onChanged = vi.fn();
    let received: unknown = null;
    server.use(http.put(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/role`,
      async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({ username: "pm@pathfinder.local", role: "admin" });
      }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.selectOptions(
      within(row("pm@pathfinder.local")).getByLabelText(/역할 변경/), "admin");
    expect(received).toEqual({ role: "admin" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("surfaces the server's refusal to demote the last admin", async () => {
    server.use(http.put(
      `${API_BASE_URL}/admin/users/admin@pathfinder.local/role`, () =>
        HttpResponse.json(
          { detail: "마지막 관리자는 강등할 수 없습니다. 먼저 다른 관리자를 지정하세요." },
          { status: 400 })));
    render(<UserTable users={USERS} currentEmail="other@x.io" onChanged={() => {}} />);
    await userEvent.selectOptions(
      within(row("admin@pathfinder.local")).getByLabelText(/역할 변경/), "pm");
    expect(await screen.findByText(/마지막 관리자는 강등할 수 없습니다/))
      .toBeInTheDocument();
  });

  it("resets a password and shows it once", async () => {
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@pathfinder.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
  });

  it("disables an enabled user", async () => {
    const onChanged = vi.fn();
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@pathfinder.local/disable`, () =>
        new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "비활성화" }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers 활성화 for a disabled user", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users/off@x.io/enable`, () =>
      new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    expect(within(row("off@x.io")).getByRole("button", { name: "활성화" }))
      .toBeInTheDocument();
  });

  it("requires confirmation before deleting", async () => {
    const onChanged = vi.fn();
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@pathfinder.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "삭제" }));
    // 첫 클릭은 확인을 띄우기만 한다 — 되돌릴 수 없는 조작이다.
    expect(handler).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /삭제 확인/ }));
    expect(handler).toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("can back out of the delete confirmation", async () => {
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@pathfinder.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@pathfinder.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@pathfinder.local")).getByRole("button", { name: "삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(handler).not.toHaveBeenCalled();
  });
});
