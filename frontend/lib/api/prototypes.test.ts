// frontend/lib/api/prototypes.test.ts
// @vitest-environment node
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import {
  listPrototypes,
  startSession,
  closeSession,
  interruptSession,
  submitPrototypeAnswers,
  startHost,
  stopHost,
  getHost,
  prototypePreviewUrl,
  streamPrototypeEvents,
} from "./prototypes";

describe("listPrototypes", () => {
  it("GETs /prototypes and returns the list as-is", async () => {
    const body = [
      { slug: "todo-app", spec_path: "aiplc-docs/discovery/prototypes/todo-app/PROTOTYPE-todo-app.md", state: "built", port: null },
      { slug: "chat-widget", spec_path: "aiplc-docs/discovery/prototypes/chat-widget/PROTOTYPE-chat-widget.md", state: "running", port: 4021 },
    ];
    server.use(
      http.get(`${API_BASE_URL}/projects/p1/prototypes`, () => HttpResponse.json(body)),
    );
    expect(await listPrototypes("p1")).toEqual(body);
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
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

describe("streamPrototypeEvents", () => {
  it("opens the prototype events URL with the text query param", () => {
    streamPrototypeEvents("p1", "todo-app", "__first__", { onEvent: () => {}, onDone: () => {} });
    expect(FakeEventSource.last!.url).toBe(
      `${API_BASE_URL}/projects/p1/prototypes/todo-app/events?text=${encodeURIComponent("__first__")}`,
    );
  });

  it("dispatches each frame and finishes on a done event", () => {
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent, onDone });
    const es = FakeEventSource.last!;
    es.emit({ kind: "status", text: "working", path: null, payload: null });
    es.emit({ kind: "message", text: "ok", path: null, payload: null });
    es.emit({ kind: "done", text: null, path: null, payload: null });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("finishes on an error event", () => {
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent, onDone });
    const es = FakeEventSource.last!;
    es.emit({ kind: "error", text: "boom", path: null, payload: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("unsubscribe closes the stream", () => {
    const stop = streamPrototypeEvents("p1", "todo-app", "go", { onEvent: () => {}, onDone: () => {} });
    stop();
    expect(FakeEventSource.last!.closed).toBe(true);
  });

  it("a transport error closes the stream and calls onError + onDone", () => {
    const onError = vi.fn();
    const onDone = vi.fn();
    streamPrototypeEvents("p1", "todo-app", "go", { onEvent: () => {}, onDone, onError });
    const es = FakeEventSource.last!;
    es.onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });
});
