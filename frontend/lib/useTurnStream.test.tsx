// frontend/lib/useTurnStream.test.tsx
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTurnStream, type AiItem } from "./useTurnStream";
import { normalTurn, errorTurn } from "@/test/fixtures/agentEventStreams";
import type { AgentEvent } from "@/lib/api/types";

// Minimal fake EventSource (mirrors lib/api/sse.test.ts): records URL, lets the
// test push frames / trigger a transport error.
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
  emit(obj: AgentEvent) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

const ai = (items: ReturnType<typeof useTurnStream>["items"]) =>
  items.filter((i): i is AiItem => i.role === "ai");

describe("useTurnStream", () => {
  it("appends a user bubble + a streaming AI bubble on send and opens the events stream", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("필터 기능 추가해줘"));
    expect(result.current.items[0]).toMatchObject({ role: "user", text: "필터 기능 추가해줘" });
    expect(result.current.items[1]).toMatchObject({ role: "ai", streaming: true });
    expect(result.current.streaming).toBe(true);
    expect(FakeEventSource.last!.url).toContain("/projects/pilot1/events?text=");
  });

  it("folds message frames into the AI bubble and trace frames into the reasoning trace, then finishes on done", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = FakeEventSource.last!;
    for (const frame of normalTurn) act(() => es.emit(frame));

    const last = ai(result.current.items)[0];
    expect(last.text).toBe("기획전 필터 기능을 추가했습니다. 우측 프리뷰에서 확인해 주세요.");
    expect(last.trace.map((t) => t.kind)).toEqual(["status", "file_changed"]);
    expect(last.trace[1].path).toBe("prototype/src/components/FilterBar.tsx");
    expect(last.streaming).toBe(false);
    expect(result.current.streaming).toBe(false);
    expect(es.closed).toBe(true);
  });

  it("surfaces an agent-reported error-kind frame on the AI bubble", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("build"));
    const es = FakeEventSource.last!;
    for (const frame of errorTurn) act(() => es.emit(frame));
    expect(ai(result.current.items)[0].error).toMatch(/빌드에 실패했습니다/);
    expect(result.current.streaming).toBe(false);
  });

  it("surfaces a transport error and ignores empty / concurrent sends", () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("   ")); // empty after trim → ignored
    expect(result.current.items).toHaveLength(0);

    act(() => result.current.send("go"));
    act(() => result.current.send("두 번째")); // in-flight → ignored
    expect(result.current.items.filter((i) => i.role === "user")).toHaveLength(1);

    act(() => FakeEventSource.last!.fail());
    expect(ai(result.current.items)[0].error).toMatch(/연결/);
    expect(result.current.streaming).toBe(false);
  });
});
