import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, type JsonBodyType } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw/server";
import { UserMenu } from "./UserMenu";
import * as keepAlive from "@/lib/auth/keepSessionAlive";

function mockMe(body: JsonBodyType, status = 200) {
  server.use(http.get("*/api/auth/me", () => HttpResponse.json(body, { status })));
}

describe("UserMenu", () => {
  it("shows the signed-in email's initial", async () => {
    mockMe({ authenticated: true, email: "admin@aipds.local", role: "admin" });
    render(<UserMenu />);
    expect(await screen.findByRole("button", { name: /사용자 메뉴/ }))
      .toHaveTextContent("A");
  });

  it("reveals email, role and logout when opened", async () => {
    mockMe({ authenticated: true, email: "pm@aipds.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByText("pm@aipds.local")).toBeInTheDocument();
    expect(screen.getByText("PM")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
  });

  it("offers 사용자 관리 to an admin", async () => {
    mockMe({ authenticated: true, email: "admin@aipds.local", role: "admin" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByRole("link", { name: "사용자 관리" }))
      .toHaveAttribute("href", "/admin/users");
  });

  it("hides 사용자 관리 from a pm", async () => {
    // Links to screens that will not open for a pm are not shown (the actual blocking is in
    // the backend).
    mockMe({ authenticated: true, email: "pm@aipds.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.queryByRole("link", { name: "사용자 관리" })).toBeNull();
  });

  it("shows the design profile link for admins", async () => {
    mockMe({ authenticated: true, email: "admin@aipds.local", role: "admin" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByRole("link", { name: "브랜드 디자인" }))
      .toHaveAttribute("href", "/admin/design");
  });

  it("hides the design profile link from a pm", async () => {
    mockMe({ authenticated: true, email: "pm@aipds.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.queryByRole("link", { name: "브랜드 디자인" })).toBeNull();
  });

  it("renders nothing when not authenticated", async () => {
    // Keeps an empty avatar from appearing on pre-authentication screens such as the login
    // screen.
    mockMe({ authenticated: false }, 401);
    const { container } = render(<UserMenu />);
    await waitFor(() => expect(container.querySelector("button")).toBeNull());
  });

  it("starts the proactive token refresh for as long as it is mounted", async () => {
    // The wiring point for the defect of being logged out during a prototype build. If the
    // refresh timer does not actually start, nothing is fixed for the user even if route and
    // keepSessionAlive both pass.
    //
    // Why it is attached to this component: it goes into the header of every authenticated
    // screen, so the wiring is not repeated per page (/api/auth/me is already called here for
    // the same reason). Wrapping only the prototype screen would miss the workspace's long
    // Discovery turns.
    const stop = vi.fn();
    const start = vi.spyOn(keepAlive, "keepSessionAlive").mockReturnValue(stop);
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" });

    const { unmount } = render(<UserMenu />);
    await screen.findByRole("button", { name: /사용자 메뉴/ });
    expect(start).toHaveBeenCalled();

    // The timer must be cleaned up on unmount — if left, a timer piles up every time the screen
    // changes, hitting Cognito several times over.
    unmount();
    expect(stop).toHaveBeenCalled();
  });

  it("logs out via POST so a prefetch cannot end the session", async () => {
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    const form = screen.getByRole("button", { name: "로그아웃" }).closest("form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/auth/logout");
  });

  it("falls back to a placeholder label for an unrecognized role", async () => {
    // If a value not in ROLE_LABEL arrives (a backend regression, a future third role), a
    // notice is shown instead of empty text. The admin links are not exposed either.
    mockMe({ authenticated: true, email: "x@aipds.local", role: "superuser" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByText("역할 없음")).toBeInTheDocument();
    expect(screen.queryByText("superuser")).toBeNull();
    expect(screen.queryByRole("link", { name: "사용자 관리" })).toBeNull();
  });

  it("closes when Escape is pressed", async () => {
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" });
    render(<UserMenu />);
    const toggle = await screen.findByRole("button", { name: /사용자 메뉴/ });
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "로그아웃" })).toBeNull();
  });

  it("closes when clicking outside the menu", async () => {
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" });
    render(
      <div>
        <UserMenu />
        <button type="button">밖</button>
      </div>,
    );
    const toggle = await screen.findByRole("button", { name: /사용자 메뉴/ });
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "밖" }));
    expect(screen.queryByRole("button", { name: "로그아웃" })).toBeNull();
  });

  it("still toggles open and closed by clicking the avatar button", async () => {
    mockMe({ authenticated: true, email: "a@example.com", role: "pm" });
    render(<UserMenu />);
    const toggle = await screen.findByRole("button", { name: /사용자 메뉴/ });

    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();

    await userEvent.click(toggle);
    expect(screen.queryByRole("button", { name: "로그아웃" })).toBeNull();
  });

  it("keeps the admin link clickable — the outside-click handler doesn't swallow it", async () => {
    mockMe({ authenticated: true, email: "admin@aipds.local", role: "admin" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    const link = screen.getByRole("link", { name: "사용자 관리" });
    await userEvent.click(link);
    // The click itself is handled without an exception, and the link still has the correct href.
    expect(link).toHaveAttribute("href", "/admin/users");
  });

  it("offers 비밀번호 변경 to a pm, not just to an admin", async () => {
    // It is an operation on one's own account, so it is independent of role. If it went under the
    // same condition as the admin links, a pm would have no way to change their own password, and
    // asking an admin for a reset would remain the only path.
    mockMe({ authenticated: true, email: "pm@aipds.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByRole("button", { name: "비밀번호 변경" })).toBeInTheDocument();
  });

  it("opens the change-password dialog from the menu", async () => {
    mockMe({ authenticated: true, email: "pm@aipds.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    await userEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));
    expect(await screen.findByLabelText("현재 비밀번호")).toBeInTheDocument();
    // The menu must close — if an open menu remained behind the modal, outside-click handling
    // would overlap.
    expect(screen.queryByRole("button", { name: "로그아웃" })).toBeNull();
  });
});
