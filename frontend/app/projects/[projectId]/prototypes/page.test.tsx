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

describe("survey panel reachability", () => {
  it("stays hidden until the card's 설문 button is clicked", async () => {
    mockStream();
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/survey`,
        () => new HttpResponse(null, { status: 404 })),
    );
    render(<PrototypesPage params={params} />);

    // Before any click there is no survey panel at all.
    expect(await screen.findByText("todo-app")).toBeInTheDocument();
    expect(screen.queryByText("검증 설문")).not.toBeInTheDocument();

    const surveyButtons = screen.getAllByRole("button", { name: "설문" });
    await userEvent.click(surveyButtons[0]);

    // The panel is now rendered in the page body — NOT behind the build
    // drawer, which is a full-screen fixed overlay. Regression guard: the
    // panel used to share the drawer's openSlug condition, so it only ever
    // rendered underneath the drawer and was unreachable.
    expect(await screen.findByText("검증 설문")).toBeInTheDocument();
  });

  it("does not open the build drawer when 설문 is clicked", async () => {
    const startBuild = vi.fn();
    mockStream({ startBuild });
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/survey`,
        () => new HttpResponse(null, { status: 404 })),
    );
    render(<PrototypesPage params={params} />);

    await userEvent.click((await screen.findAllByRole("button", { name: "설문" }))[0]);
    await screen.findByText("검증 설문");
    // No build session was started, and the drawer's own controls are absent.
    expect(startBuild).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /완료/ })).not.toBeInTheDocument();
  });

  it("toggles the panel closed on a second click", async () => {
    mockStream();
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(PROTOTYPES)),
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/survey`,
        () => new HttpResponse(null, { status: 404 })),
    );
    render(<PrototypesPage params={params} />);

    const btn = (await screen.findAllByRole("button", { name: "설문" }))[0];
    await userEvent.click(btn);
    expect(await screen.findByText("검증 설문")).toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.queryByText("검증 설문")).not.toBeInTheDocument();
  });
});
