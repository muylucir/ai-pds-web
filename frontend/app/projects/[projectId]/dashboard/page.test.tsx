import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { projectState } from "@/test/fixtures/projectState";
import { auditEntries } from "@/test/fixtures/auditEntries";

// AppHeader가 그리는 LanguageSwitcher가 useRouter()를 부른다 — 앱 라우터가
// 마운트되지 않은 단위 테스트에서 그 훅은 던진다. 스위치의 동작은
// components/LanguageSwitcher.test.tsx가 검증하므로 여기서는 마운트만 되게 한다.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

import DashboardPage from "./page";

function mockAll(pid: string) {
  server.use(
    http.get(`${API_BASE_URL}/projects/${pid}/state`, () => HttpResponse.json(projectState)),
    http.get(`${API_BASE_URL}/projects/${pid}/artifacts`, () =>
      HttpResponse.json({ artifacts: ["aiplc-docs/discovery/discovery-document.md"] }),
    ),
    http.get(`${API_BASE_URL}/projects/${pid}/audit`, () => HttpResponse.json(auditEntries)),
    http.get(`${API_BASE_URL}/projects/${pid}/questions`, () =>
      HttpResponse.json({ questions: ["aiplc-docs/discovery/product-strategy/strategy-questions.md"] }),
    ),
  );
}

const EMPTY_STATE = { project_type: null, current_stage: null, stages: [] };

/** 아무 워크플로우도 돌지 않은 갓 만든 프로젝트. */
function mockFresh(pid: string) {
  server.use(
    http.get(`${API_BASE_URL}/projects/${pid}/state`, () => HttpResponse.json(EMPTY_STATE)),
    http.get(`${API_BASE_URL}/projects/${pid}/artifacts`, () => HttpResponse.json({ artifacts: [] })),
    http.get(`${API_BASE_URL}/projects/${pid}/audit`, () => HttpResponse.json([])),
    http.get(`${API_BASE_URL}/projects/${pid}/questions`, () => HttpResponse.json({ questions: [] })),
  );
}

// App-Router pages receive params as a Promise in Next 15.
const params = Promise.resolve({ projectId: "pilot1" });

describe("Dashboard page", () => {
  it("renders stage timeline, artifacts, and activity from the API", async () => {
    mockAll("pilot1");
    // `use(params)` suspends on the first render because the test's plain
    // Promise.resolve(...) params (unlike Next's internally-tracked params
    // thenable) isn't pre-marked as settled. Wrapping the initial render in
    // act() lets that Suspense retry flush before we start querying/waiting.
    await act(async () => {
      render(<DashboardPage params={params} />);
    });
    expect(await screen.findByText("Product Strategy")).toBeInTheDocument();
    expect(screen.getByText("discovery-document.md")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Entry 34")).toBeInTheDocument());
  });

  it("shows a not-found state on 404", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/ghost/state`, () =>
        HttpResponse.json({ detail: "unknown project" }, { status: 404 }),
      ),
    );
    await act(async () => {
      render(<DashboardPage params={Promise.resolve({ projectId: "ghost" })} />);
    });
    expect(await screen.findByText(/프로젝트를 찾을 수 없습니다/)).toBeInTheDocument();
  });

  it("갓 만든 프로젝트에는 Workspace로 보내는 시작 배너가 뜬다", async () => {
    mockFresh("test333");
    await act(async () => {
      render(<DashboardPage params={Promise.resolve({ projectId: "test333" })} />);
    });
    expect(await screen.findByText(/아직 시작하지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Workspace로 이동/ })).toHaveAttribute(
      "href",
      "/projects/test333/workspace",
    );
  });

  it("진행 중인 프로젝트에는 시작 배너가 뜨지 않는다", async () => {
    mockAll("pilot1");
    await act(async () => {
      render(<DashboardPage params={params} />);
    });
    expect(await screen.findByText("Product Strategy")).toBeInTheDocument();
    expect(screen.queryByText(/아직 시작하지 않았습니다/)).not.toBeInTheDocument();
  });

  it("stages가 비어도 audit이 있으면 배너를 띄우지 않는다", async () => {
    // 상태 파일의 `## Stage Progress`가 어긋나면 진행 중인 프로젝트도 stages가
    // 빈 배열로 온다(backend parsers/state.py의 실측 기록). 그것만 보고 판단하면
    // 이미 일하고 있는 사용자에게 "시작하세요"라고 말한다 — audit이 그 오판을 막는다.
    server.use(
      http.get(`${API_BASE_URL}/projects/drift/state`, () => HttpResponse.json(EMPTY_STATE)),
      http.get(`${API_BASE_URL}/projects/drift/artifacts`, () => HttpResponse.json({ artifacts: [] })),
      http.get(`${API_BASE_URL}/projects/drift/audit`, () => HttpResponse.json(auditEntries)),
      http.get(`${API_BASE_URL}/projects/drift/questions`, () => HttpResponse.json({ questions: [] })),
    );
    await act(async () => {
      render(<DashboardPage params={Promise.resolve({ projectId: "drift" })} />);
    });
    await waitFor(() => expect(screen.getByText("Entry 34")).toBeInTheDocument());
    expect(screen.queryByText(/아직 시작하지 않았습니다/)).not.toBeInTheDocument();
  });
});
