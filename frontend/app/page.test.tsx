// frontend/app/page.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
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
        }),
      ),
    );
    render(<Home />);
    expect(await screen.findByText("기획전 AI 어시스턴트")).toBeInTheDocument();
    // Name-less project falls back to showing its id as the title.
    expect(screen.getByText("bare")).toBeInTheDocument();
  });

  it("renders the empty state when there are no projects", async () => {
    server.use(http.get(`${API_BASE_URL}/projects`, () => HttpResponse.json({ projects: [] })));
    render(<Home />);
    expect(
      await screen.findByText(/아직 생성된 프로젝트가 없습니다/),
    ).toBeInTheDocument();
  });
});
