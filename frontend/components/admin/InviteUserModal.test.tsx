import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
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

  // InviteUserModal은 onClose를 그대로 TempPasswordPanel에 넘긴다 — 비밀번호를
  // 화면에서 걷어내는 것은 onClose를 받아 모달 자체를 언마운트하는 부모의 책임이다.
  // 그 실제 계약을 검증하려면 page.tsx가 하는 대로 부모를 흉내내는 호스트가 필요하다.
  function ModalHost() {
    const [open, setOpen] = useState(true);
    if (!open) return null;
    return <InviteUserModal onInvited={() => {}} onClose={() => setOpen(false)} />;
  }

  it("removes the password from the document once the host closes on 확인", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({
        username: "new@x.io", email: "new@x.io", role: "pm",
        temp_password: "Tmp!2345678abcd",
      }, { status: 201 })));

    render(<ModalHost />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@x.io");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(await screen.findByText("Tmp!2345678abcd")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(screen.queryByText("Tmp!2345678abcd")).not.toBeInTheDocument();
  });

  it("defaults the role to pm", () => {
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    expect(screen.getByLabelText("역할")).toHaveValue("pm");
  });

  it("shows the server message when the email already exists", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      // 백엔드가 실제로 보내는 것을 목이 흉내내야 한다 — 문구가 아니라 코드다.
      HttpResponse.json({ detail: "email_exists" }, { status: 409 })));
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
