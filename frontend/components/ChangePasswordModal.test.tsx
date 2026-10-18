import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw/server";
import { ChangePasswordModal } from "./ChangePasswordModal";

function respond(status: number, detail?: string) {
  server.use(http.post("*/me/password", () =>
    detail === undefined
      ? new HttpResponse(null, { status })
      : HttpResponse.json({ detail }, { status })));
}

async function fill(current: string, next: string, confirm = next) {
  await userEvent.type(screen.getByLabelText("현재 비밀번호"), current);
  await userEvent.type(screen.getByLabelText("새 비밀번호"), next);
  await userEvent.type(screen.getByLabelText("새 비밀번호 확인"), confirm);
}

const OK = { current: "OldPass1!", next: "NewPass2@" };

describe("ChangePasswordModal", () => {
  it("closes only after the server confirms the change", async () => {
    respond(204);
    const onClose = vi.fn();
    render(<ChangePasswordModal onClose={onClose} />);
    await fill(OK.current, OK.next);
    // If it closes before the success confirmation arrives, the user believes an unchanged
    // password was changed. So closing must happen only after the 204.
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("never sends a request when the confirmation does not match", async () => {
    const seen: unknown[] = [];
    server.use(http.post("*/me/password", async ({ request }) => {
      seen.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }));
    const onClose = vi.fn();
    render(<ChangePasswordModal onClose={onClose} />);
    await fill(OK.current, OK.next, "NewPass3#");
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    // Sending a typo to the server makes Cognito turn that value into the real password —
    // the user gets locked out without knowing what they typed.
    expect(seen).toEqual([]);
    expect(await screen.findByRole("alert")).toHaveTextContent(/일치하지 않습니다/);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("sends exactly the two passwords the backend needs", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(http.post("*/me/password", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return new HttpResponse(null, { status: 204 });
    }));
    render(<ChangePasswordModal onClose={vi.fn()} />);
    await fill(OK.current, OK.next);
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    await waitFor(() => expect(body).not.toBeNull());
    // The confirm field is client-only — it is not sent to the server.
    expect(body).toEqual({
      current_password: OK.current, new_password: OK.next,
    });
  });

  it("tells the user the current password was wrong, not that access was denied", async () => {
    respond(400, "wrong_password");
    render(<ChangePasswordModal onClose={vi.fn()} />);
    await fill("Wrong1!x", OK.next);
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    expect(await screen.findByRole("alert"))
      .toHaveTextContent(/현재 비밀번호가 맞지 않습니다/);
  });

  it("restates the policy when the new password is rejected", async () => {
    respond(400, "password_policy");
    render(<ChangePasswordModal onClose={vi.fn()} />);
    await fill(OK.current, "weak");
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    // The policy must be explained again — showing only "rejected" leaves the user unable to
    // tell what to fix. Cognito does the check, so this text is the only guidance.
    //
    // Look at the **error region**, not the static hint attached to the screen. Matching with
    // findByText would hit that static hint, making a test that passes whatever the server returns.
    expect(await screen.findByRole("alert")).toHaveTextContent(/8자 이상/);
  });

  it("asks the user to sign in again when the token lacks the self-service scope", async () => {
    respond(403, "reauth_required");
    render(<ChangePasswordModal onClose={vi.fn()} />);
    await fill(OK.current, OK.next);
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/다시 로그인/);
  });

  it("stays open after a failure so the user can retry", async () => {
    respond(400, "wrong_password");
    const onClose = vi.fn();
    render(<ChangePasswordModal onClose={onClose} />);
    await fill("Wrong1!x", OK.next);
    await userEvent.click(screen.getByRole("button", { name: "변경" }));
    await screen.findByRole("alert");
    expect(onClose).not.toHaveBeenCalled();
    // The entered values must remain — making the user refill all three fields leads them to
    // repeat the typo.
    expect(screen.getByLabelText("새 비밀번호")).toHaveValue(OK.next);
  });

  it("does not submit twice while a request is in flight", async () => {
    let hits = 0;
    server.use(http.post("*/me/password", async () => {
      hits += 1;
      await new Promise((r) => setTimeout(r, 30));
      return new HttpResponse(null, { status: 204 });
    }));
    render(<ChangePasswordModal onClose={vi.fn()} />);
    await fill(OK.current, OK.next);
    const submit = screen.getByRole("button", { name: "변경" });
    await userEvent.click(submit);
    await userEvent.click(submit);
    await waitFor(() => expect(hits).toBe(1));
  });

  it("renders the password fields as password inputs", async () => {
    render(<ChangePasswordModal onClose={vi.fn()} />);
    for (const label of ["현재 비밀번호", "새 비밀번호", "새 비밀번호 확인"]) {
      // With type=text it is visible over the shoulder and browser autofill stores the value.
      expect(screen.getByLabelText(label)).toHaveAttribute("type", "password");
    }
  });
});
