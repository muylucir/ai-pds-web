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
  { username: "off@x.io", email: "off@x.io", role: "pm",
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
    // 초대 직후 상태는 "비밀번호 변경 필요"로 읽혀야 한다.
    expect(within(row("pm@aipds.local")).getByText(/변경 필요/)).toBeInTheDocument();
    expect(within(row("off@x.io")).getByText("비활성")).toBeInTheDocument();
  });

  it("marks the current user so they know which row is theirs", () => {
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    expect(within(row("admin@aipds.local")).getByText(/나/)).toBeInTheDocument();
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
        // 백엔드가 실제로 보내는 것을 목이 흉내내야 한다 — 문구가 아니라 코드다.
        HttpResponse.json({ detail: "last_admin" }, { status: 400 })));
    render(<UserTable users={USERS} currentEmail="other@x.io" onChanged={() => {}} />);
    await userEvent.selectOptions(
      within(row("admin@aipds.local")).getByLabelText(/역할 변경/), "pm");
    // 단정은 한국어 문구다 — Provider 없이 렌더하므로 기본 로케일(ko)이 걸린다.
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
    // 재설정은 서버 쪽 상태(FORCE_CHANGE_PASSWORD)를 바꾼다 — 목록을 갱신하지
    // 않으면 상태 컬럼이 거짓말을 하게 된다. 단, 갱신이 비밀번호 패널을 먼저
    // 걷어가면 안 된다 — 관리자가 읽기 전에 사라지면 유일한 열람 기회를 잃는다.
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
    // 두 문장(setRevealed, onChanged)이 같은 동기 블록 안에 있어 DOM이나 실제
    // 비동기 재조회로는 순서를 구분할 수 없다 — React가 같은 틱의 상태 갱신을
    // 배치 처리하고, 실제 reload도 항상 두 문장이 끝난 뒤에야(await 이후) 도착한다.
    // 대신 onChanged 자체가 던지게 만들어 순서를 드러낸다: setRevealed가 먼저
    // 실행됐다면 그 뒤에 던진 예외는 이미 반영된 비밀번호를 지우지 못한다. 순서가
    // 뒤집히면 onChanged가 먼저 던져 setRevealed 줄까지 도달하지 못하고, 비밀번호는
    // 끝내 화면에 오르지 않는다.
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
    server.use(http.post(`${API_BASE_URL}/admin/users/off@x.io/enable`, () =>
      new HttpResponse(null, { status: 204 })));
    render(<UserTable users={USERS} currentEmail="admin@aipds.local"
                      onChanged={() => {}} />);
    expect(within(row("off@x.io")).getByRole("button", { name: "활성화" }))
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
    // 첫 클릭은 확인을 띄우기만 한다 — 되돌릴 수 없는 조작이다.
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
