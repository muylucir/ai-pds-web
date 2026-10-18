// frontend/lib/api/sse.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL, ApiError } from "./client";
import { streamEvents, streamAnswers, watchTurn } from "./sse";

// Minimal fake EventSource: records the URL, lets the test push data/error.
class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onmessage: ((ev: { data: string; lastEventId?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  close() {
    this.closed = true;
  }
  emit(obj: unknown, id?: number) {
    this.onmessage?.({ data: JSON.stringify(obj),
                       lastEventId: id === undefined ? "" : String(id) });
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
  // An instance left over by a previous test would defeat the "the stream did not open" assertion.
  FakeEventSource.last = null;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

// Mock the turn start (POST), and wait until the stream opens. The start becoming
// asynchronous is a result of this fix — because the text goes in the body.
function mockTurns(turnId = "t-1") {
  server.use(
    http.post(`${API_BASE_URL}/projects/p1/turns`, () =>
      HttpResponse.json({ turn_id: turnId })),
    http.post(`${API_BASE_URL}/projects/p1/answers`, () =>
      HttpResponse.json({ turn_id: turnId })),
  );
}

async function opened(): Promise<FakeEventSource> {
  await vi.waitFor(() => expect(FakeEventSource.last).not.toBeNull());
  return FakeEventSource.last!;
}

describe("streamEvents", () => {
  it("opens the events URL with the turn handle", async () => {
    mockTurns("t-abc");
    streamEvents("p1", "안녕", { onEvent: () => {}, onDone: () => {} });
    expect((await opened()).url).toBe(`${API_BASE_URL}/projects/p1/events?turn=t-abc`);
  });

  it("dispatches each frame and finishes on a done event", async () => {
    mockTurns();
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamEvents("p1", "go", { onEvent, onDone });
    const es = await opened();
    es.emit({ kind: "status", text: "working", path: null });
    es.emit({ kind: "message", text: "ok", path: null });
    es.emit({ kind: "done", text: null, path: null });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onEvent).toHaveBeenNthCalledWith(1, { kind: "status", text: "working", path: null },
                                            undefined);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("passes each frame's SSE id through as its seq", async () => {
    // This is the value returned as `after` when reattaching (watchTurn).
    mockTurns();
    const onEvent = vi.fn();
    streamEvents("p1", "go", { onEvent, onDone: () => {} });
    const es = await opened();
    es.emit({ kind: "message", text: "ok", path: null }, 7);
    expect(onEvent).toHaveBeenCalledWith({ kind: "message", text: "ok", path: null }, 7);
  });

  it("a 409 carries the id of the turn that is already running", async () => {
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/turns`, () =>
        HttpResponse.json({ detail: { code: "turn_in_progress", turn_id: "t-run" } },
                          { status: 409 })),
    );
    const onError = vi.fn();
    streamEvents("p1", "go", { onEvent: () => {}, onDone: () => {}, onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalled());
    const err = onError.mock.calls[0][0];
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.detail).toBe("turn_in_progress");
    expect(err.turnId).toBe("t-run");
  });

  it("unsubscribe closes the stream", async () => {
    mockTurns();
    const stop = streamEvents("p1", "go", { onEvent: () => {}, onDone: () => {} });
    const es = await opened();
    stop();
    expect(es.closed).toBe(true);
  });
});

describe("streamAnswers", () => {
  it("opens the events URL with the answers turn id", async () => {
    mockTurns("t-ans");
    streamAnswers("p1", { "1": "A" }, { onEvent: () => {}, onDone: () => {} });
    const url = (await opened()).url;
    expect(url).toBe(`${API_BASE_URL}/projects/p1/events?turn=t-ans`);
    // If the answer JSON were still in the URL, the purpose of this fix would be moot.
    expect(url).not.toContain("answers=");
  });

  it("dispatches each frame and finishes on a done event", async () => {
    mockTurns();
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamAnswers("p1", { "1": "A" }, { onEvent, onDone });
    const es = await opened();
    es.emit({ kind: "message", text: "ok", path: null, payload: null });
    es.emit({ kind: "done", text: null, path: null, payload: null });
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("unsubscribe closes the stream", async () => {
    mockTurns();
    const stop = streamAnswers("p1", { "1": "A" }, { onEvent: () => {}, onDone: () => {} });
    const es = await opened();
    stop();
    expect(es.closed).toBe(true);
  });
});


// ---- Two-step handle that moves long input out of the URL (HTTP 431 defect) ----
//
// Observed defect: a 2,164-character Korean input became a 14,376-byte request line via
// encodeURIComponent, and together with the auth cookies (3 JWTs, ~3.7KB) it exceeded Node's maxHeaderSize
// of 16,384 bytes, so the proxy returned 431. EventSource does not expose the status code,
// so the screen only showed "연결이 끊어졌습니다" (connection lost).
const LONG_KO = "가".repeat(3000);

describe("긴 입력은 URL이 아니라 본문으로 간다", () => {
  it("streamEvents가 텍스트를 POST하고 URL에는 짧은 핸들만 싣는다", async () => {
    let posted: unknown = null;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/turns`, async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ turn_id: "abc123" });
      }),
    );
    const stop = streamEvents("p1", LONG_KO, { onEvent: () => {}, onDone: () => {} });
    await vi.waitFor(() => expect(FakeEventSource.last).not.toBeNull());
    const url = FakeEventSource.last!.url;
    // This is the key assertion of this fix — the long text is not in the URL.
    expect(url).toBe(`${API_BASE_URL}/projects/p1/events?turn=abc123`);
    expect(url.length).toBeLessThan(200);
    expect(url).not.toContain(encodeURIComponent("가"));
    expect(posted).toEqual({ text: LONG_KO });
    stop();
  });

  it("streamAnswers도 같은 배관을 쓴다", async () => {
    let posted: unknown = null;
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/answers`, async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ turn_id: "def456" });
      }),
    );
    const answers = { "1": "A", "2": LONG_KO };
    const stop = streamAnswers("p1", answers, { onEvent: () => {}, onDone: () => {} });
    await vi.waitFor(() => expect(FakeEventSource.last).not.toBeNull());
    expect(FakeEventSource.last!.url).toBe(
      `${API_BASE_URL}/projects/p1/events?turn=def456`);
    expect(posted).toEqual({ answers });
    stop();
  });

  it("개시 요청이 실패하면 원인을 알 수 있는 오류를 넘긴다", async () => {
    // This path is the key to diagnosability: EventSource cannot give the status code, but
    // POST can. 431/413 must not be flattened into "연결이 끊어졌습니다" (connection lost).
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/turns`, () =>
        HttpResponse.json({ detail: "too long" }, { status: 431 })),
    );
    const onError = vi.fn();
    const onDone = vi.fn();
    streamEvents("p1", LONG_KO, { onEvent: () => {}, onDone, onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalled());
    const err = onError.mock.calls[0][0];
    expect(err).toMatchObject({ status: 431 });
    // The turn could not be opened, so the stream does not open either.
    expect(FakeEventSource.last).toBeNull();
    expect(onDone).toHaveBeenCalled();
  });

  it("개시 전에 unsubscribe하면 스트림을 열지 않는다", async () => {
    // If the user leaves the screen immediately (unmount), a late-opening stream must not become an orphan.
    let resolvePost: (v: unknown) => void = () => {};
    server.use(
      http.post(`${API_BASE_URL}/projects/p1/turns`, async () => {
        await new Promise((r) => { resolvePost = r; });
        return HttpResponse.json({ turn_id: "late" });
      }),
    );
    const stop = streamEvents("p1", "짧음", { onEvent: () => {}, onDone: () => {} });
    stop();
    resolvePost(null);
    await new Promise((r) => setTimeout(r, 10));
    expect(FakeEventSource.last).toBeNull();
  });
});

describe("watchTurn", () => {
  it("opens the turn's stream from the given seq, with no start request", () => {
    // Watches a turn that is already running — it does not start a turn, so there is no POST.
    watchTurn("p1", "t-9", 4, { onEvent: () => {}, onDone: () => {} });
    expect(FakeEventSource.last?.url)
      .toBe(`${API_BASE_URL}/projects/p1/events?turn=t-9&after=4`);
  });
});
