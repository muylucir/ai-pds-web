import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import type { AdminUser } from "@/lib/api/adminUsers";
import { UserTable } from "./UserTable";

const USERS: AdminUser[] = [
  { username: "admin@aipds.local", email: "admin@aipds.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
  { username: "pm@aipds.local", email: "pm@aipds.local",
    role: "pm", status: "FORCE_CHANGE_PASSWORD", enabled: true,
    created_at: "2026-07-25T01:00:00+00:00" },
  { username: "off@example.com", email: "off@example.com", role: "pm",
    status: "CONFIRMED", enabled: false, created_at: "2026-07-25T02:00:00+00:00" },
];

function row(email: string) {
  return screen.getByRole("row", { name: new RegExp(email) });
}

describe("UserTable", () => {
  it("renders email, role and status for each user", () => {
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@aipds.local")).getByText("관리자")).toBeInTheDocument();
    expect(within(row("pm@aipds.local")).getByText("PM")).toBeInTheDocument();
    // The state right after an invite must read as "비밀번호 변경 필요" (password change required).
    expect(within(row("pm@aipds.local")).getByText(/변경 필요/)).toBeInTheDocument();
    expect(within(row("off@example.com")).getByText("비활성")).toBeInTheDocument();
  });

  it("marks the current user so they know which row is theirs", () => {
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@aipds.local")).getByText(/나/)).toBeInTheDocument();
  });

  it("shows a role=null user as 역할 없음", () => {
    // The admin must be able to recognize a half-created account left behind when an invite rollback failed.
    render(<UserTable users={[{ ...USERS[1], role: null }]} currentEmail={null}
                      onChanged={() => {}} />);
    expect(screen.getByText("역할 없음")).toBeInTheDocument();
  });

  it("changes a role and reloads", async () => {
    const onChanged = vi.fn();
    let received: unknown = null;
    server.use(http.put(
      `${API_BASE_URL}/admin/users/pm@aipds.local/role`,
      async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({ username: "pm@aipds.local", role: "admin" });
      }));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={onChanged} />);
    await userEvent.selectOptions(
      within(row("pm@aipds.local")).getByLabelText(/역할 변경/), "admin");
    expect(received).toEqual({ role: "admin" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("surfaces the server's refusal to demote the last admin", async () => {
    server.use(http.put(
      `${API_BASE_URL}/admin/users/admin@aipds.local/role`, () =>
        // The mock must imitate what the backend actually sends — a code, not wording.
        HttpResponse.json({ detail: "last_admin" }, { status: 400 })));
    render(<UserTable users={USERS} currentEmail="other@example.com" onChanged={() => {}} />);
    await userEvent.selectOptions(
      within(row("admin@aipds.local")).getByLabelText(/역할 변경/), "pm");
    // The assertion is on Korean wording — it renders without a Provider, so the default locale (ko) applies.
    expect(await screen.findByText(/마지막 관리자에게는 이 작업을 할 수 없습니다/))
      .toBeInTheDocument();
  });

  it("resets a password and shows it once", async () => {
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@aipds.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@aipds.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
  });

  it("removes the reset password from the document once the panel is closed", async () => {
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@aipds.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@aipds.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(screen.queryByText("New!23456789abc")).not.toBeInTheDocument();
  });

  it("reloads the table after a reset, without dropping the revealed password", async () => {
    // A reset changes server-side state (FORCE_CHANGE_PASSWORD) — without refreshing the list,
    // the status column would lie. But the refresh must not take the password panel away
    // first — if it vanished before the admin read it, the only chance to view it would be lost.
    const onChanged = vi.fn();
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@aipds.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@aipds.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("reveals the password before calling onChanged, so a reload cannot outrun the reveal", async () => {
    // The two statements (setRevealed, onChanged) are in the same synchronous block, so their
    // order cannot be told apart through the DOM or a real async refetch — React batches
    // state updates in the same tick, and a real reload always arrives only after both
    // statements finish (after the await). Instead, make onChanged itself throw to expose the
    // order: if setRevealed ran first, an exception thrown afterward cannot erase the password
    // already applied. If the order were reversed, onChanged would throw first, the
    // setRevealed line would never be reached, and the password would never appear on screen.
    const onChanged = vi.fn(() => { throw new Error("boom"); });
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@aipds.local/reset-password`, () =>
        HttpResponse.json({ username: "pm@aipds.local",
                            temp_password: "New!23456789abc" })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: /비밀번호 재설정/ }));
    expect(onChanged).toHaveBeenCalled();
    expect(await screen.findByText("New!23456789abc")).toBeInTheDocument();
  });

  it("disables an enabled user", async () => {
    const onChanged = vi.fn();
    server.use(http.post(
      `${API_BASE_URL}/admin/users/pm@aipds.local/disable`, () =>
        new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: "비활성화" }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers 활성화 for a disabled user", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users/off@example.com/enable`, () =>
      new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    expect(within(row("off@example.com")).getByRole("button", { name: "활성화" }))
      .toBeInTheDocument();
  });

  it("requires confirmation before deleting", async () => {
    const onChanged = vi.fn();
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@aipds.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={onChanged} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: "삭제" }));
    // The first click only shows a confirmation — this is an irreversible operation.
    expect(handler).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /삭제 확인/ }));
    expect(handler).toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("can back out of the delete confirmation", async () => {
    const handler = vi.fn();
    server.use(http.delete(`${API_BASE_URL}/admin/users/pm@aipds.local`, () => {
      handler();
      return new HttpResponse(null, { status: 204 });
    }));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    await userEvent.click(
      within(row("pm@aipds.local")).getByRole("button", { name: "삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(handler).not.toHaveBeenCalled();
  });
});
