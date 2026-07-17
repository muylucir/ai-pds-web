// frontend/lib/api/sse.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { API_BASE_URL } from "./client";
import { streamEvents } from "./sse";

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
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

describe("streamEvents", () => {
  it("opens the events URL with the text query param", () => {
    streamEvents("p1", "안녕", { onEvent: () => {}, onDone: () => {} });
    expect(FakeEventSource.last!.url).toBe(`${API_BASE_URL}/projects/p1/events?text=${encodeURIComponent("안녕")}`);
  });

  it("dispatches each frame and finishes on a done event", () => {
    const onEvent = vi.fn();
    const onDone = vi.fn();
    streamEvents("p1", "go", { onEvent, onDone });
    const es = FakeEventSource.last!;
    es.emit({ kind: "status", text: "working", path: null });
    es.emit({ kind: "message", text: "ok", path: null });
    es.emit({ kind: "done", text: null, path: null });
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onEvent).toHaveBeenNthCalledWith(1, { kind: "status", text: "working", path: null });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
  });

  it("unsubscribe closes the stream", () => {
    const stop = streamEvents("p1", "go", { onEvent: () => {}, onDone: () => {} });
    stop();
    expect(FakeEventSource.last!.closed).toBe(true);
  });
});
