// frontend/app/page.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";

// 생성 → 대시보드 이동 검증용. vitest 환경에는 AppRouter 컨텍스트가 없어
// useRouter를 mock한다 (hoisted — Home import보다 먼저 적용).
const pushMock = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

import Home from "./page";

describe("Project list screen", () => {
  it("lists projects from GET /projects", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({
          projects: [
            { project_id: "pilot1", name: "기획전 AI 어시스턴트" },
            { project_id: "bare", name: null },
          ],
          total: 2,
          page: 1,
          size: 10,
        }),
      ),
    );
    render(<Home />);
    expect(await screen.findByText("기획전 AI 어시스턴트")).toBeInTheDocument();
    // Name-less project falls back to showing its id as the link text.
    expect(screen.getByRole("link", { name: "bare" })).toHaveAttribute(
      "href", "/projects/bare/dashboard");
  });

  it("renders the empty state when there are no projects", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ projects: [], total: 0, page: 1, size: 10 }),
      ),
    );
    render(<Home />);
    expect(
      await screen.findByText(/아직 생성된 프로젝트가 없습니다/),
    ).toBeInTheDocument();
  });
});

describe("프로젝트 생성 → 대시보드 이동", () => {
  it("생성 성공 시 새 프로젝트의 대시보드로 이동한다", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    server.use(
      http.get(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ projects: [], total: 0, page: 1, size: 10 })),
      http.post(`${API_BASE_URL}/projects`, () =>
        HttpResponse.json({ project_id: "new-proj", name: "새 프로젝트" })),
    );
    render(<Home />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("프로젝트 ID"), "new-proj");
    await user.click(screen.getByRole("button", { name: "프로젝트 생성" }));
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/projects/new-proj/dashboard");
    });
  });
});
