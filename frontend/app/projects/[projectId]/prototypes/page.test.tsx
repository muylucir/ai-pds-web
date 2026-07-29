// frontend/app/projects/[projectId]/prototypes/page.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
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
  { slug: "todo-app", spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md", state: "none", port: null, response_count: 0 },
  { slug: "chat-widget", spec_path: "aiplc-docs/discovery/prototypes/chat-widget/PROTOTYPE-chat-widget.md", state: "running", port: 4021, response_count: 3 },
];

// GET /prototypes now answers {prototypes, active_builds, max_builds}
// (Task 7) rather than a bare array; this wraps the fixture so each test
// site doesn't have to restate the capacity fields.
function listing(prototypes = PROTOTYPES, activeBuilds = 0, maxBuilds = 2) {
  return { prototypes, active_builds: activeBuilds, max_builds: maxBuilds };
}

const params = Promise.resolve({ projectId: "p1" });

describe("Prototypes page", () => {
  it("renders cards from the prototype list", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing([]))),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    expect(await screen.findByText(/아직 프로토타입 스펙이 없습니다/)).toBeInTheDocument();
  });

  it("onBuild with a fresh 202 session opens BuildPanel with autoStart (fires the first-build turn)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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

  it("warns when the concurrent-build cap is reached", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing(PROTOTYPES, 2, 2))),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    expect(await screen.findByText(/동시 빌드 상한\(2건\)에 도달했습니다/)).toBeInTheDocument();
  });

  it("does not show the cap warning when builds are below the limit", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing(PROTOTYPES, 0, 2))),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    await screen.findByText("todo-app");
    expect(screen.queryByText(/동시 빌드 상한/)).not.toBeInTheDocument();
  });

  it("wires each card's download link to prototypeArchiveUrl", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () =>
        HttpResponse.json(
          listing([
            { slug: "todo-app", spec_path: "s.md", state: "built", port: null, response_count: 0 },
          ]),
        ),
      ),
    );
    mockStream();
    await act(async () => {
      render(<PrototypesPage params={params} />);
    });
    const link = await screen.findByRole("link", { name: "다운로드" });
    expect(link).toHaveAttribute("href", `${API_BASE_URL}/projects/p1/prototypes/todo-app/archive`);
  });
});

describe("survey panel reachability", () => {
  it("stays hidden until the card's 설문 button is clicked", async () => {
    mockStream();
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(listing())),
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

describe("reset confirmation", () => {
  // handleReset re-fetches /prototypes at click time (a workshop's survey
  // answers arrive live while this page sits open, so the list's
  // response_count can be stale by the time the dialog needs to show it) —
  // every test here needs the GET handler live for BOTH the initial render
  // and that click-time refetch, so this counts calls rather than using
  // `.once` like some of the file's other tests do.
  function trackedListingHandler(prototypes = PROTOTYPES) {
    const calls = { get: 0 };
    return {
      calls,
      handler: http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => {
        calls.get += 1;
        return HttpResponse.json(listing(prototypes));
      }),
    };
  }

  it("shows the fresh response count and irreversibility wording in the dialog", async () => {
    const { handler } = trackedListingHandler();
    server.use(handler);
    mockStream();
    render(<PrototypesPage params={params} />);
    await screen.findByText("chat-widget");

    await userEvent.click(await screen.findByRole("button", { name: "chat-widget 초기화" }));

    const dialog = await screen.findByRole("dialog", { name: "프로토타입 초기화 확인" });
    expect(dialog).toHaveTextContent("응답 3건");
    expect(dialog).toHaveTextContent("되돌릴 수 없습니다");
  });

  it("cancel closes the dialog without calling DELETE", async () => {
    const { handler } = trackedListingHandler();
    let deleteCalls = 0;
    server.use(
      handler,
      http.delete(`${API_BASE_URL}/projects/p1/prototypes/chat-widget`, () => {
        deleteCalls++;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    mockStream();
    render(<PrototypesPage params={params} />);
    await screen.findByText("chat-widget");

    await userEvent.click(await screen.findByRole("button", { name: "chat-widget 초기화" }));
    await screen.findByRole("dialog", { name: "프로토타입 초기화 확인" });
    await userEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByRole("dialog", { name: "프로토타입 초기화 확인" })).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("confirm sends DELETE on 204 and the list re-fetches", async () => {
    const { handler, calls } = trackedListingHandler();
    let deleteCalls = 0;
    server.use(
      handler,
      http.delete(`${API_BASE_URL}/projects/p1/prototypes/chat-widget`, () => {
        deleteCalls++;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    mockStream();
    render(<PrototypesPage params={params} />);
    await screen.findByText("chat-widget");
    const getsBeforeConfirm = calls.get;

    await userEvent.click(await screen.findByRole("button", { name: "chat-widget 초기화" }));
    await screen.findByRole("dialog", { name: "프로토타입 초기화 확인" });
    await userEvent.click(screen.getByRole("button", { name: "초기화" }));

    await waitFor(() => expect(deleteCalls).toBe(1));
    // list.reload() ran: another GET landed beyond the initial load + the
    // click-time refetch that already happened before this assertion.
    await waitFor(() => expect(calls.get).toBeGreaterThan(getsBeforeConfirm));
    expect(screen.queryByRole("dialog", { name: "프로토타입 초기화 확인" })).not.toBeInTheDocument();
  });

  it("confirm on a 502 shows the retry error but still re-fetches the list", async () => {
    const { handler, calls } = trackedListingHandler();
    server.use(
      handler,
      http.delete(`${API_BASE_URL}/projects/p1/prototypes/chat-widget`, () =>
        HttpResponse.json({ detail: "reset partial" }, { status: 502 }),
      ),
    );
    mockStream();
    render(<PrototypesPage params={params} />);
    await screen.findByText("chat-widget");
    const getsBeforeConfirm = calls.get;

    await userEvent.click(await screen.findByRole("button", { name: "chat-widget 초기화" }));
    await screen.findByRole("dialog", { name: "프로토타입 초기화 확인" });
    await userEvent.click(screen.getByRole("button", { name: "초기화" }));

    expect(await screen.findByText(/초기화가 완료되지 않았습니다/)).toBeInTheDocument();
    // The dialog stays open on failure so retrying is one click away — but
    // list.reload() must still have run in `finally`, since a 502 means the
    // purge was only partial and the card needs to reflect whatever WAS
    // deleted.
    expect(screen.getByRole("dialog", { name: "프로토타입 초기화 확인" })).toBeInTheDocument();
    await waitFor(() => expect(calls.get).toBeGreaterThan(getsBeforeConfirm));
  });
});
