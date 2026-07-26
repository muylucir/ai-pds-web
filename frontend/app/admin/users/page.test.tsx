import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";
import AdminUsersPage from "./page";

const USERS = [
  { username: "admin@pathfinder.local", email: "admin@pathfinder.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
];

function mockList() {
  server.use(
    http.get(`${API_BASE_URL}/admin/users`, () => HttpResponse.json({ users: USERS })),
    http.get("*/api/auth/me", () => HttpResponse.json({
      authenticated: true, email: "admin@pathfinder.local", role: "admin",
    })),
  );
}

describe("/admin/users", () => {
  it("lists users", async () => {
    mockList();
    render(<AdminUsersPage />);
    expect(await screen.findByText("admin@pathfinder.local")).toBeInTheDocument();
  });

  it("opens the invite modal", async () => {
    mockList();
    render(<AdminUsersPage />);
    await screen.findByText("admin@pathfinder.local");
    await userEvent.click(screen.getByRole("button", { name: "사용자 초대" }));
    expect(screen.getByLabelText("이메일")).toBeInTheDocument();
  });

  it("explains a 403 instead of showing an empty table", async () => {
    // pm이 URL을 직접 쳐서 들어온 경우 — 미들웨어를 우회했더라도 API가 막는다.
    server.use(
      http.get(`${API_BASE_URL}/admin/users`, () =>
        HttpResponse.json({ detail: "admin role required" }, { status: 403 })),
      http.get("*/api/auth/me", () => HttpResponse.json({
        authenticated: true, email: "pm@pathfinder.local", role: "pm",
      })),
    );
    render(<AdminUsersPage />);
    expect(await screen.findByText(/관리자 권한이 필요합니다/)).toBeInTheDocument();
  });

  it("shows a generic error when the list fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/admin/users`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 502 })),
      http.get("*/api/auth/me", () => HttpResponse.json({
        authenticated: true, email: "admin@pathfinder.local", role: "admin",
      })),
    );
    render(<AdminUsersPage />);
    expect(await screen.findByText(/사용자 목록을 불러오지 못했습니다/))
      .toBeInTheDocument();
  });
});
