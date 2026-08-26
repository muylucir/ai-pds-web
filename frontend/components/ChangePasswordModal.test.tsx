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
    // 성공 확인을 받기 전에 닫으면 사용자는 바뀌지 않은 비밀번호를 바뀐 것으로
    // 믿는다. 그래서 닫힘은 204 이후에만 일어나야 한다.
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
    // 오타를 서버까지 보내면 Cognito가 그 값을 실제 비밀번호로 만든다 —
    // 사용자는 자기가 무엇을 입력했는지 모르는 상태로 잠긴다.
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
    // 확인 칸은 클라이언트 전용이다 — 서버로 보내지 않는다.
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
    // 정책을 다시 안내해야 한다 — "거부됨"만 보여주면 사용자가 무엇을 고쳐야
    // 하는지 알 수 없다. 검사는 Cognito가 하므로 문구가 유일한 안내다.
    //
    // 화면에 붙어 있는 정적 안내가 아니라 **오류 영역**을 본다. findByText로
    // 잡으면 그 정적 안내와 매칭되어, 서버가 무엇을 돌려주든 통과하는 테스트가 된다.
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
    // 입력한 값이 남아 있어야 한다 — 세 칸을 다시 채우게 만들면 사용자는
    // 오타를 반복한다.
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
      // type=text면 어깨 너머로 보이고 브라우저 자동완성이 값을 저장한다.
      expect(screen.getByLabelText(label)).toHaveAttribute("type", "password");
    }
  });
});
