// frontend/lib/api/prototypes.test.ts
// @vitest-environment node
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import {
  listPrototypes,
  prototypeArchiveUrl,
  startSession,
  closeSession,
  interruptSession,
  submitPrototypeAnswers,
  startHost,
  stopHost,
  getHost,
  prototypePreviewUrl,
  streamPrototypeEvents,
  resetPrototype,
} from "./prototypes";

describe("listPrototypes", () => {
  it("GETs /prototypes and unwraps {prototypes, active_builds, max_builds}", async () => {
    const prototypes = [
      { slug: "todo-app", spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md", state: "built", port: null, response_count: 0 },
      { slug: "chat-widget", spec_path: "aiplc-docs/discovery/prototypes/chat-widget/PROTOTYPE-chat-widget.md", state: "running", port: 4021, response_count: 3 },
    ];
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () =>
        HttpResponse.json({ prototypes, active_builds: 0, max_builds: 2 }),
      ),
    );
    expect(await listPrototypes("p1")).toEqual({ prototypes, active_builds: 0, max_builds: 2 });
  });

  it("unwraps the prototypes array and reports build capacity", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () =>
        HttpResponse.json({
          prototypes: [{ slug: "demo", spec_path: "s.md", state: "built", port: null, response_count: 0 }],
          active_builds: 1,
          max_builds: 2,
        }),
      ),
    );
    const result = await listPrototypes("p1");
    expect(result.prototypes.map((p) => p.slug)).toEqual(["demo"]);
    expect(result.active_builds).toBe(1);
    expect(result.max_builds).toBe(2);
  });
});

describe("prototypeArchiveUrl", () => {
  it("builds an absolute archive URL with encoded segments", () => {
    const url = prototypeArchiveUrl("proj 1", "한글-앱");
    expect(url).toContain("/projects/proj%201/prototypes/");
    expect(url).toContain(encodeURIComponent("한글-앱"));
    expect(url).toMatch(/\/archive$/);
  });
});

describe("startSession / closeSession / interruptSession", () => {
  it("POSTs /session and returns {status}", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/session`, () =>
        HttpResponse.json({ status: "starting" }, { status: 202 }),
      ),
    );
    expect(await startSession("p1", "todo-app")).toEqual({ status: "starting" });
  });

  it("startSession maps 409 to ApiError(409)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/session`, () =>
        HttpResponse.json({ detail: "build session already active" }, { status: 409 }),
      ),
    );
    await expect(startSession("p1", "todo-app")).rejects.toMatchObject({ status: 409 });
  });

  it("closeSession DELETEs /session and resolves on 204", async () => {
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1/prototypes/todo-app/session`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(closeSession("p1", "todo-app")).resolves.toBeUndefined();
  });

  it("interruptSession POSTs /interrupt and returns {status}", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/interrupt`, () =>
        HttpResponse.json({ status: "interrupting" }, { status: 202 }),
      ),
    );
    expect(await interruptSession("p1", "todo-app")).toEqual({ status: "interrupting" });
  });
});

describe("submitPrototypeAnswers", () => {
  it("POSTs {answers} and resolves true on 204", async () => {
    let seenBody: unknown;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/answers`, async ({ request }) => {
        seenBody = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const ok = await submitPrototypeAnswers("p1", "todo-app", { "1": "A" });
    expect(seenBody).toEqual({ answers: { "1": "A" } });
    expect(ok).toBe(true);
  });

  it("resolves false on 409 (no pending question)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/answers`, () =>
        HttpResponse.json({ detail: "no pending questions" }, { status: 409 }),
      ),
    );
    expect(await submitPrototypeAnswers("p1", "todo-app", { "1": "A" })).toBe(false);
  });

  it("rethrows non-409 errors", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/answers`, () =>
        HttpResponse.json({ detail: "no active build session" }, { status: 404 }),
      ),
    );
    await expect(submitPrototypeAnswers("p1", "todo-app", { "1": "A" })).rejects.toMatchObject({ status: 404 });
  });
});

describe("startHost / stopHost / getHost", () => {
  it("startHost POSTs /host and returns HostStatus", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () =>
        HttpResponse.json({ state: "running", port: 4021, log_tail: "" }),
      ),
    );
    expect(await startHost("p1", "todo-app")).toEqual({ state: "running", port: 4021, log_tail: "" });
  });

  it("startHost maps 502 to ApiError with log_tail as detail", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () =>
        HttpResponse.json({ detail: "npm install failed\n..." }, { status: 502 }),
      ),
    );
    await expect(startHost("p1", "todo-app")).rejects.toMatchObject({ status: 502, detail: "npm install failed\n..." });
  });

  it("stopHost DELETEs /host and resolves on 204", async () => {
    server.use(
      http.delete(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(stopHost("p1", "todo-app")).resolves.toBeUndefined();
  });

  it("getHost returns HostStatus on 200", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () =>
        HttpResponse.json({ state: "running", port: 4021, log_tail: "listening on 4021" }),
      ),
    );
    expect(await getHost("p1", "todo-app")).toEqual({ state: "running", port: 4021, log_tail: "listening on 4021" });
  });

  it("getHost returns null on 404 (nothing hosted)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () =>
        HttpResponse.json({ detail: "not hosted" }, { status: 404 }),
      ),
    );
    expect(await getHost("p1", "todo-app")).toBeNull();
  });

  it("getHost rethrows non-404 errors", async () => {
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes/todo-app/host`, () =>
        HttpResponse.json({ detail: "unknown project" }, { status: 500 }),
      ),
    );
    await expect(getHost("p1", "todo-app")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("resetPrototype", () => {
  it("resetPrototype sends DELETE to the prototype resource", async () => {
    let seen: string | null = null;
    server.use(
      http.delete("*/projects/:pid/prototypes/:slug", ({ request }) => {
        seen = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await resetPrototype("proj-1", "todo-app");

    expect(seen).toContain("/projects/proj-1/prototypes/todo-app");
  });

  it("resetPrototype surfaces a 502 as an ApiError so the UI can retry", async () => {
    server.use(
      http.delete("*/projects/:pid/prototypes/:slug", () =>
        HttpResponse.json({ detail: "초기화가 완료되지 않았습니다" }, { status: 502 }),
      ),
    );

    await expect(resetPrototype("proj-1", "todo-app")).rejects.toThrow(ApiError);
  });
});

describe("prototypePreviewUrl", () => {
  it("builds the reverse-proxy URL under API_BASE_URL", () => {
    expect(prototypePreviewUrl("p1", "todo-app")).toBe(`${API_BASE_URL}/proto/p1/todo-app/`);
  });

  it("encodes pid/slug segments", () => {
    expect(prototypePreviewUrl("p 1", "todo app")).toBe(`${API_BASE_URL}/proto/p%201/todo%20app/`);
  });
});

// Minimal fake EventSource — mirrors sse.test.ts's FakeEventSource exactly,
// since streamPrototypeEvents mirrors openStream's frame-handling shape.
class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  close() {
    this.closed = true;
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
  // 개시가 비동기가 됐으므로, 이전 테스트가 남긴 인스턴스를 지워야
  // protoOpened()가 "이번 테스트의" 스트림을 기다린다.
  FakeEventSource.last = null;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

// 센티널이 아닌 턴은 POST로 핸들을 먼저 받으므로 개시가 비동기다.
function mockProtoTurns(turnId = "pt-1") {
  server.use(
    http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/turns`, () =>
      HttpResponse.json({ turn_id: turnId })),
  );
}

async function protoOpened(): Promise<FakeEventSource> {
  await vi.waitFor(() => expect(FakeEventSource.last).not.toBeNull());
  return FakeEventSource.last!;
}

describe("streamPrototypeEvents", () => {
  it("첫 턴 센티널은 URL로 그대로 간다 (9바이트라 길이 문제가 없다)", () => {
    streamPrototypeEvents("p1", "todo-app", "__first__", { onEvent: () => {}, onDone: () => {} });
    expect(FakeEventSource.last!.url).toBe(
      `${API_BASE_URL}/projects/p1/prototypes/todo-app/events?text=${encodeURIComponent("__first__")}`,
    );
  });

  it("긴 입력은 본문으로 가고 URL에는 핸들만 실린다", async () => {
    // 이것이 이 수정의 핵심 — 워크스페이스 채팅과 같은 431 결함이 여기에도 있었다.
    let posted: unknown = null;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/prototypes/todo-app/turns`,
        async ({ request }) => {
          posted = await request.json();
          return HttpResponse.json({ turn_id: "pt-long" });
        }),
    );
    const long = "가".repeat(3000);
    streamPrototypeEvents("p1", "todo-app", long, { onEvent: () => {}, onDone: () => {} });
    const es = await protoOpened();
    expect(es.url).toBe(
      `${API_BASE_URL}/projects/p1/prototypes/todo-app/events?turn=pt-long`);
    expect(es.url).not.toContain(encodeURIComponent("가"));
    expect(posted).toEqual({ text: long });
  });

  it("dispatches each frame and finishes on a done event", async () => {
    mockProtoTurns();
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent, onDone });
    const es = await protoOpened();
    es.emit({ kind: "status", text: "working", path: null, payload: null });
    es.emit({ kind: "message", text: "ok", path: null, payload: null });
    es.emit({ kind: "done", text: null, path: null, payload: null });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("finishes on an error event", async () => {
    mockProtoTurns();
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent, onDone });
    const es = await protoOpened();
    es.emit({ kind: "error", text: "boom", path: null, payload: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("unsubscribe closes the stream", async () => {
    mockProtoTurns();
    const stop = streamPrototypeEvents("p1", "todo-app", "go", { onEvent: () => {}, onDone: () => {} });
    const es = await protoOpened();
    stop();
    expect(es.closed).toBe(true);
  });

  it("a transport error closes the stream and calls onError + onDone", async () => {
    mockProtoTurns();
    const onError = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent: () => {}, onDone, onError });
    const es = await protoOpened();
    es.onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });
});
