// frontend/app/projects/[projectId]/prototypes/page.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import * as prototypeStream from "@/lib/usePrototypeStream";
import PrototypesPage from "./page";

// BuildPanel's own tests cover its rendering in depth; here it's exercised
// through usePrototypeStream's mock (same hook-mock pattern as the workspace
// page test) so this page test stays about the GRID + panel-open wiring, not
// the chat internals.
function mockStream(overrides: Partial<prototypeStream.PrototypeStream> = {}) {
  vi.spyOn(prototypeStream, "usePrototypeStream").mockReturnValue({
    items: [],
    streaming: false,
    pendingQuestions: null,
    changedPaths: [],
    startBuild: vi.fn(),
    send: vi.fn(),
    submitAnswers: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  });
}

const PROTOTYPES = [
  { slug: "todo-app", spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md", state: "none", port: null },
  { slug: "chat-widget", spec_path: "aiplc-docs/discovery/prototypes/chat-widget/PROTOTYPE-chat-widget.md", state: "running", port: 4021 },
];

const params = Promise.resolve({ projectId: "p1" });

describe("Prototypes page", () => {
  it("renders cards from the prototype list", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    expect(await screen.findByText("todo-app")).toBeInTheDocument();
    expect(screen.getByText("chat-widget")).toBeInTheDocument();
    expect(screen.getByText("실행 중 :4021")).toBeInTheDocument();
  });

  it("shows an empty state when there are no prototype specs", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json([])),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    expect(await screen.findByText(/아직 프로토타입 스펙이 없습니다/)).toBeInTheDocument();
  });

  it("onBuild with a fresh 202 session opens BuildPanel with autoStart (fires the first-build turn)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/session`, () =>
        HttpResponse.json({ status: "starting" }, { status: 202 }),
      ),
    );
    const startBuild = vi.fn();
    mockStream({ startBuild });
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    await screen.findByText("todo-app");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "빌드 시작" }));

    expect(await screen.findByRole("heading", { name: "todo-app" })).toBeInTheDocument();
    expect(startBuild).toHaveBeenCalledTimes(1);
  });

  it("onBuild against an already-live session (409) opens BuildPanel WITHOUT autoStart", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/session`, () =>
        HttpResponse.json({ detail: "build session already active" }, { status: 409 }),
      ),
    );
    const startBuild = vi.fn();
    mockStream({ startBuild });
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    await screen.findByText("todo-app");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "빌드 시작" }));

    expect(await screen.findByRole("heading", { name: "todo-app" })).toBeInTheDocument();
    expect(startBuild).not.toHaveBeenCalled();
  });

  it("onShowLogs fetches host status and renders log_tail in a <pre>", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.get(`${API_BASE_URL}/projects/p1/prototypes/chat-widget/host`, () =>
        HttpResponse.json({ state: "running", port: 4021, log_tail: "listening on 4021" }),
      ),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    await screen.findByText("chat-widget");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "로그" }));

    expect(await screen.findByText("listening on 4021")).toBeInTheDocument();
  });
});
