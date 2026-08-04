// frontend/lib/useTurnStream.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "@/lib/api/client";
import { useTurnStream, type AiItem } from "./useTurnStream";
import { normalTurn, errorTurn, questionsTurn, documentTurn } from "@/test/fixtures/agentEventStreams";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";

// onError의 세션 확인 호출을 검증하기 위한 모킹 — 실제 fetch/navigate 부작용은
// sessionRecovery.test.ts가 별도로 검증하므로, 여기서는 훅이 그 함수를 올바른
// 인자로 "불렀는가"만 확인한다.
vi.mock("@/lib/auth/sessionRecovery", () => ({
  redirectIfSessionExpired: vi.fn(),
}));

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
  // 턴 텍스트가 URL이 아니라 POST 본문으로 가므로(431 결함 수정) 개시가
  // 비동기다 — 목을 깔고 스트림이 열릴 때까지 기다린다.
  FakeEventSource.last = null;
  server.use(
    http.post(`${API_BASE_URL}/projects/pilot1/turns`, () =>
      HttpResponse.json({ turn_id: "t-1" })),
  );
  vi.clearAllMocks();
});
afterEach(() => {
  delete (globalThis as any).EventSource;
});

const ai = (items: ReturnType<typeof useTurnStream>["items"]) =>
  items.filter((i): i is AiItem => i.role === "ai");

async function opened(): Promise<FakeEventSource> {
  await waitFor(() => expect(FakeEventSource.last).not.toBeNull());
  return FakeEventSource.last!;
}

describe("useTurnStream", () => {
  it("appends a user bubble + a streaming AI bubble on send and opens the events stream", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("필터 기능 추가해줘"));
    expect(result.current.items[0]).toMatchObject({ role: "user", text: "필터 기능 추가해줘" });
    expect(result.current.items[1]).toMatchObject({ role: "ai", streaming: true });
    expect(result.current.streaming).toBe(true);
    // 텍스트는 본문으로 갔고 URL에는 핸들만 있다.
    const es = await opened();
    expect(es.url).toContain("/projects/pilot1/events?turn=");
    expect(es.url).not.toContain("text=");
  });

  it("folds message frames into the AI bubble and trace frames into the reasoning trace, then finishes on done", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = await opened();
    for (const frame of normalTurn) act(() => es.emit(frame));

    const last = ai(result.current.items)[0];
    expect(last.text).toBe("기획전 필터 기능을 추가했습니다. 우측 프리뷰에서 확인해 주세요.");
    expect(last.trace.map((t) => t.kind)).toEqual(["status", "file_changed"]);
    expect(last.trace[1].path).toBe("prototype/src/components/FilterBar.tsx");
    expect(last.streaming).toBe(false);
    expect(result.current.streaming).toBe(false);
    expect(es.closed).toBe(true);
  });

  it("surfaces an agent-reported error-kind frame on the AI bubble", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("build"));
    const es = await opened();
    for (const frame of errorTurn) act(() => es.emit(frame));
    expect(ai(result.current.items)[0].error).toMatch(/빌드에 실패했습니다/);
    expect(result.current.streaming).toBe(false);
  });

  it("surfaces a transport error and ignores empty / concurrent sends", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("   ")); // empty after trim → ignored
    expect(result.current.items).toHaveLength(0);

    act(() => result.current.send("go"));
    act(() => result.current.send("두 번째")); // in-flight → ignored
    expect(result.current.items.filter((i) => i.role === "user")).toHaveLength(1);

    const es = await opened();
    act(() => es.fail());
    expect(ai(result.current.items)[0].error).toMatch(/연결/);
    expect(result.current.streaming).toBe(false);
  });

  // onError의 유일한 실제 배선 지점 — 이 콜이 지워지거나 인자가 바뀌면 사용자는
  // 만료된 세션에서도 로그인으로 보내지지 않는다. navigate는 undefined로 넘겨
  // (기본 전체 페이지 이동을 쓰게) 두고, currentPath는 현재 pathname이어야 한다.
  it("checks the session on a transport error (the sole wiring point for redirectIfSessionExpired)", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = await opened();
    act(() => es.fail());
    expect(sessionRecovery.redirectIfSessionExpired).toHaveBeenCalledWith(
      undefined,
      window.location.pathname,
    );
  });

  it("closes the stream if the component unmounts mid-turn", async () => {
    const { result, unmount } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = await opened();
    act(() => es.emit({ kind: "status", text: "진행 중…", path: null, payload: null })); // stream still live, not done/error
    expect(es.closed).toBe(false);

    unmount();

    expect(es.closed).toBe(true);
  });
});

const cards = (items: ReturnType<typeof useTurnStream>["items"]) =>
  items.filter((i) => i.role === "card");

describe("useTurnStream — structured timeline cards (C2)", () => {
  it("appends a QuestionsCardItem when a turn's file_changed path ends in -questions.md", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("Product Strategy 질문 만들어줘"));
    const es = await opened();
    for (const frame of questionsTurn) act(() => es.emit(frame));

    const found = cards(result.current.items);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({
      role: "card",
      card: "questions",
      path: "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    });
  });

  it("appends an ArtifactCardItem when a turn's file_changed path ends in discovery-document.md", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("문서 갱신해줘"));
    const es = await opened();
    for (const frame of documentTurn) act(() => es.emit(frame));

    const found = cards(result.current.items);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({
      role: "card",
      card: "artifact",
      path: "aiplc-docs/discovery/discovery-document.md",
    });
  });

  it("dedupes multiple file_changed events for the same path into a single card", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("go"));
    const es = await opened();
    const repeated = [
      { kind: "file_changed" as const, text: null, path: "aiplc-docs/discovery/discovery-document.md", payload: null },
      { kind: "file_changed" as const, text: null, path: "aiplc-docs/discovery/discovery-document.md", payload: null },
      { kind: "done" as const, text: null, path: null, payload: null },
    ];
    for (const frame of repeated) act(() => es.emit(frame));
    expect(cards(result.current.items)).toHaveLength(1);
  });

  it("does not append a card for file_changed paths matching neither suffix (e.g. prototype source files)", async () => {
    const { result } = renderHook(() => useTurnStream("pilot1"));
    act(() => result.current.send("필터 추가"));
    const es = await opened();
    for (const frame of normalTurn) act(() => es.emit(frame)); // normalTurn's path is prototype/src/components/FilterBar.tsx
    expect(cards(result.current.items)).toHaveLength(0);
  });
});
