import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { API_BASE_URL } from "@/lib/api/client";
import { server } from "@/test/msw/server";

// The LanguageSwitcher that AppHeader draws calls useRouter() — in unit tests where the app router
// is not mounted, that hook throws. The switcher's behavior is verified by
// components/LanguageSwitcher.test.tsx, so here it only needs to mount.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

import AdminUsersPage from "./page";

const USERS = [
  { username: "admin@aipds.local", email: "admin@aipds.local",
    role: "admin", status: "CONFIRMED", enabled: true,
    created_at: "2026-07-25T00:00:00+00:00" },
];

function mockList() {
  server.use(
    http.get(`${API_BASE_URL}/admin/users`, () => HttpResponse.json({ users: USERS })),
    http.get("*/api/auth/me", () => HttpResponse.json({
      authenticated: true, email: "admin@aipds.local", role: "admin",
    })),
  );
}

describe("/admin/users", () => {
  it("lists users", async () => {
    mockList();
    render(<AdminUsersPage />);
    expect(await screen.findByText("admin@aipds.local")).toBeInTheDocument();
  });

  it("opens the invite modal", async () => {
    mockList();
    render(<AdminUsersPage />);
    await screen.findByText("admin@aipds.local");
    await userEvent.click(screen.getByRole("button", { name: "사용자 초대" }));
    expect(screen.getByLabelText("이메일")).toBeInTheDocument();
  });

  it("explains a 403 instead of showing an empty table", async () => {
    // A pm who typed the URL directly — even if the middleware was bypassed, the API blocks it.
    server.use(
      http.get(`${API_BASE_URL}/admin/users`, () =>
        HttpResponse.json({ detail: "admin role required" }, { status: 403 })),
      http.get("*/api/auth/me", () => HttpResponse.json({
        authenticated: true, email: "pm@aipds.local", role: "pm",
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
        authenticated: true, email: "admin@aipds.local", role: "admin",
      })),
    );
    render(<AdminUsersPage />);
    expect(await screen.findByText(/사용자 목록을 불러오지 못했습니다/))
      .toBeInTheDocument();
  });
});
