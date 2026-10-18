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
      expect(body).toEqual({ email: "new@example.com", role: "pm" });
      return HttpResponse.json({
        username: "new@example.com", email: "new@example.com", role: "pm",
        temp_password: "Tmp!2345678abcd",
      }, { status: 201 });
    }));

    const onInvited = vi.fn();
    render(<InviteUserModal onInvited={onInvited} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));

    expect(await screen.findByText("Tmp!2345678abcd")).toBeInTheDocument();
    // The list refresh is signaled after the password is shown.
    expect(onInvited).toHaveBeenCalled();
  });

  // InviteUserModal passes onClose straight to TempPasswordPanel — removing the password from the
  // screen is the responsibility of the parent, which receives onClose and unmounts the modal itself.
  // To verify that actual contract, we need a host that mimics the parent the way page.tsx does.
  function ModalHost() {
    const [open, setOpen] = useState(true);
    if (!open) return null;
    return <InviteUserModal onInvited={() => {}} onClose={() => setOpen(false)} />;
  }

  it("removes the password from the document once the host closes on 확인", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({
        username: "new@example.com", email: "new@example.com", role: "pm",
        temp_password: "Tmp!2345678abcd",
      }, { status: 201 })));

    render(<ModalHost />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@example.com");
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
      // The mock must mimic what the backend actually sends — a code, not a message.
      HttpResponse.json({ detail: "email_exists" }, { status: 409 })));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "dup@example.com");
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

  it("says the email is malformed when the server refuses it", async () => {
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({ detail: "email_invalid" }, { status: 400 })));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    // An address the browser's type=email accepts and the server rejects.
    await userEvent.type(screen.getByLabelText("이메일"), "kim@corp");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(await screen.findByText(/이메일 주소 형식이 올바르지 않습니다/)).toBeInTheDocument();
  });

  it("never shows [object Object] for a validation error body", async () => {
    // The detail of FastAPI's default 422 is an array of error objects.
    server.use(http.post(`${API_BASE_URL}/admin/users`, () =>
      HttpResponse.json({ detail: [{ loc: ["body", "role"], msg: "bad", type: "x" }] },
                        { status: 422 })));
    render(<InviteUserModal onInvited={() => {}} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("이메일"), "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "초대" }));
    expect(await screen.findByText("요청이 올바르지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/object Object/)).toBeNull();
  });
});
