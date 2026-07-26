import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import { InviteUserModal } from "./InviteUserModal";

describe("InviteUserModal", () => {
  it("invites a user and reveals the temp password once", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, async ({ request }) => {
      const body = (await request.json()) as { email: string; role: string };
      expect(body).toEqual({ email: "new@x.io", role: "pm" });
      return HttpResponse.json({
        username: "new@x.io", email: "new@x.io", role: "pm",
        temp_password: "Tmp!2345678abcd",
      }, { status: 201 });
    }));

    const onInvited = vi.fn();
    render(<InviteUserModal onInvited={onInvited} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@x.io");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));

    expect(await screen.findByText("Tmp!2345678abcd")).toBeInTheDocument();
    // 목록 갱신은 비밀번호를 보여준 뒤에 알린다.
    expect(onInvited).toHaveBeenCalled();
  });

  it("defaults the role to pm", () => {
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    expect(screen.getByLabelText("역할")).toHaveValue("pm");
  });

  it("shows the server message when the email already exists", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({ detail: "이미 등록된 이메일입니다." }, { status: 409 })));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "dup@x.io");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(await screen.findByText("이미 등록된 이메일입니다.")).toBeInTheDocument();
  });

  it("does not submit an empty email", async () => {
    const handler = vi.fn();
    server.use(http.post(`${API_BASE_URL}/admin/users`, () => {
      handler();
      return HttpResponse.json({}, { status: 201 });
    }));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(handler).not.toHaveBeenCalled();
  });

  it("closes without inviting", async () => {
    const onClose = vi.fn();
    render(<InviteUserModal onInvited={() => {}} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
