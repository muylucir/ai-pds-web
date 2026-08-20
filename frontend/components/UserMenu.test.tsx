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
    // pm에게 열리지 않을 화면의 링크를 보여주지 않는다(실제 차단은 백엔드).
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
    // 로그인 화면 등 인증 전 화면에서 빈 아바타가 뜨지 않게 한다.
    mockMe({ authenticated: false }, 401);
    const { container } = render(<UserMenu />);
    await waitFor(() => expect(container.querySelector("button")).toBeNull());
  });

  it("starts the proactive token refresh for as long as it is mounted", async () => {
    // 프로토타입 빌드 중 로그아웃되는 결함의 배선 지점. 갱신 타이머가 실제로
    // 시작되지 않으면 route/keepSessionAlive가 둘 다 통과해도 사용자에게는
    // 아무것도 고쳐지지 않는다.
    //
    // 이 컴포넌트에 붙이는 이유: 인증된 모든 화면의 헤더에 들어가므로 페이지마다
    // 배선을 반복하지 않는다(이미 /api/auth/me를 같은 이유로 여기서 부른다).
    // 프로토타입 화면만 감싸면 워크스페이스의 긴 디스커버리 턴이 빠진다.
    const stop = vi.fn();
    const start = vi.spyOn(keepAlive, "keepSessionAlive").mockReturnValue(stop);
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });

    const { unmount } = render(<UserMenu />);
    await screen.findByRole("button", { name: /사용자 메뉴/ });
    expect(start).toHaveBeenCalled();

    // 언마운트에서 타이머를 반드시 정리한다 — 남으면 화면을 옮길 때마다
    // 타이머가 하나씩 쌓여 Cognito를 여러 배로 때린다.
    unmount();
    expect(stop).toHaveBeenCalled();
  });

  it("logs out via POST so a prefetch cannot end the session", async () => {
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    const form = screen.getByRole("button", { name: "로그아웃" }).closest("form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/auth/logout");
  });

  it("falls back to a placeholder label for an unrecognized role", async () => {
    // ROLE_LABEL에 없는 값이 오면(백엔드 회귀·미래의 세 번째 역할) 빈 텍스트가
    // 아니라 안내 문구를 보여준다. admin 링크도 노출하지 않는다.
    mockMe({ authenticated: true, email: "x@aipds.local", role: "superuser" });
    render(<UserMenu />);
    await userEvent.click(await screen.findByRole("button", { name: /사용자 메뉴/ }));
    expect(screen.getByText("역할 없음")).toBeInTheDocument();
    expect(screen.queryByText("superuser")).toBeNull();
    expect(screen.queryByRole("link", { name: "사용자 관리" })).toBeNull();
  });

  it("closes when Escape is pressed", async () => {
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
    render(<UserMenu />);
    const toggle = await screen.findByRole("button", { name: /사용자 메뉴/ });
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "로그아웃" })).toBeNull();
  });

  it("closes when clicking outside the menu", async () => {
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
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
    mockMe({ authenticated: true, email: "a@b.io", role: "pm" });
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
    // 클릭 자체가 예외 없이 처리되고, 링크가 여전히 올바른 href를 갖는다.
    expect(link).toHaveAttribute("href", "/admin/users");
  });
});
