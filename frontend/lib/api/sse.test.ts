// frontend/lib/api/sse.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "./client";
import { streamEvents, streamAnswers } from "./sse";

// Minimal fake EventSource: records the URL, lets the test push data/error.
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
  // 이전 테스트가 남긴 인스턴스가 "스트림이 열리지 않았다" 단정을 무력화한다.
  FakeEventSource.last = null;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

// 턴 개시(POST)를 목으로 깔고, 스트림이 열릴 때까지 기다린다. 개시가
// 비동기가 된 것은 이 수정의 결과다 — 텍스트가 본문으로 가기 때문이다.
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
    expect(onEvent).toHaveBeenNthCalledWith(1, { kind: "status", text: "working", path: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
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
  it("opens the answers/stream URL with the turn handle", async () => {
    mockTurns("t-ans");
    streamAnswers("p1", { "1": "A" }, { onEvent: () => {}, onDone: () => {} });
    const url = (await opened()).url;
    expect(url).toBe(`${API_BASE_URL}/projects/p1/answers/stream?turn=t-ans`);
    // 답변 JSON이 URL에 남아 있으면 이 수정의 목적이 무의미해진다.
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


// ---- 긴 입력을 URL에서 빼는 2단계 핸들 (HTTP 431 결함) ----
//
// 실측한 결함: 한글 2,164자 입력이 encodeURIComponent로 14,376바이트 요청
// 라인이 되고, 인증 쿠키(JWT 3개 ~3.7KB)와 합쳐 Node의 maxHeaderSize
// 16,384바이트를 넘겨 프록시가 431을 냈다. EventSource는 상태 코드를 노출하지
// 않아 화면에는 "연결이 끊어졌습니다"만 떴다.
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
    // 이것이 이 수정의 핵심 단정 — 긴 텍스트가 URL에 없다.
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
      `${API_BASE_URL}/projects/p1/answers/stream?turn=def456`);
    expect(posted).toEqual({ answers });
    stop();
  });

  it("개시 요청이 실패하면 원인을 알 수 있는 오류를 넘긴다", async () => {
    // 이 경로가 진단 가능성의 핵심이다: EventSource는 상태 코드를 못 주지만
    // POST는 준다. 431/413이 "연결이 끊어졌습니다"로 뭉개지지 않아야 한다.
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
    // 턴을 열지 못했으므로 스트림도 열리지 않는다.
    expect(FakeEventSource.last).toBeNull();
    expect(onDone).toHaveBeenCalled();
  });

  it("개시 전에 unsubscribe하면 스트림을 열지 않는다", async () => {
    // 사용자가 즉시 화면을 떠나면(언마운트) 뒤늦게 스트림이 열려 고아가 되면 안 된다.
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
