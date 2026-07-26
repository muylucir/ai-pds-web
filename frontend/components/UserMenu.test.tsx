import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, type JsonBodyType } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/msw/server";
import { UserMenu } from "./UserMenu";

function mockMe(body: JsonBodyType, status = 200) {
  server.use(http.get("*/api/auth/me", () => HttpResponse.json(body, { status })));
}

describe("UserMenu", () => {
  it("shows the signed-in email's initial", async () => {
    mockMe({ authenticated: true, email: "admin@pathfinder.local", role: "admin" });
    render(<UserMenu />);
    expect(await screen.findByRole("button", { name: /사용자 메뉴/ }))
      .toHaveTextContent("A");
  });

  it("reveals email, role and logout when opened", async () => {
    mockMe({ authenticated: true, email: "pm@pathfinder.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByText("pm@pathfinder.local")).toBeInTheDocument();
    expect(screen.getByText("PM")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
  });

  it("offers 사용자 관리 to an admin", async () => {
    mockMe({ authenticated: true, email: "admin@pathfinder.local", role: "admin" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByRole("link", { name: "사용자 관리" }))
      .toHaveAttribute("href", "/admin/users");
  });

  it("hides 사용자 관리 from a pm", async () => {
    // pm에게 열리지 않을 화면의 링크를 보여주지 않는다(실제 차단은 백엔드).
    mockMe({ authenticated: true, email: "pm@pathfinder.local", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.queryByRole("link", { name: "사용자 관리" })).toBeNull();
  });

  it("renders nothing when not authenticated", async () => {
    // 로그인 화면 등 인증 전 화면에서 빈 아바타가 뜨지 않게 한다.
    mockMe({ authenticated: false }, 401);
    const { container } = render(<UserMenu />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector("button")).toBeNull();
  });

  it("logs out via POST so a prefetch cannot end the session", async () => {
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    const form = screen.getByRole("button", { name: "로그아웃" }).closest("form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/auth/logout");
  });
});
