// frontend/lib/useWorkspaceStream.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspaceStream } from "./useWorkspaceStream";
import * as sse from "@/lib/api/sse";
import * as client from "@/lib/api/client";
import type { AgentEvent, HistoryItem } from "@/lib/api/types";

vi.mock("@/lib/api/sse");
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig()),
  getPending: vi.fn().mockResolvedValue(null),
  getHistory: vi.fn().mockResolvedValue([]),
}));

const QUESTIONS_PAYLOAD = JSON.stringify({
  interrupt_id: "i-1",
  questions: {
    name: "q",
    preamble: null,
    parse_ok: true,
    raw_markdown: null,
    questions: [
      {
        number: 1,
        category: null,
        text: "누구?",
        answer: null,
        options: [{ letter: "A", text: "PM", is_other: false, recommended: true }],
      },
    ],
  },
});

function drive(events: AgentEvent[], impl: "streamEvents" | "streamAnswers") {
  vi.mocked(sse[impl]).mockImplementation((_pid: any, _arg: any, handlers: any) => {
    for (const ev of events) handlers.onEvent(ev);
    handlers.onDone();
    return () => {};
  });
}

describe("useWorkspaceStream", () => {
  beforeEach(() => vi.clearAllMocks());

  it("questions event fills pendingQuestions; stage event appends stages", async () => {
    drive(
      [
        { kind: "message", text: "준비", path: null, payload: null },
        {
          kind: "stage",
          text: null,
          path: null,
          payload: JSON.stringify({ stage: "Envision", status: "in_progress", summary: "" }),
        },
        { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {}); // flush the mount-time history/pending effects first
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
    expect(result.current.stages).toEqual([{ stage: "Envision", status: "in_progress", summary: "" }]);
  });

  it("submitAnswers streams via streamAnswers and clears pendingQuestions", async () => {
    drive(
      [
        { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {}); // flush the mount-time history/pending effects first
    act(() => result.current.send("시작"));
    drive(
      [
        {
          kind: "document",
          text: null,
          path: null,
          payload: JSON.stringify({ path: "d.md", version: "v1", summary: "" }),
        },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamAnswers",
    );
    act(() => result.current.submitAnswers({ "1": "A" }));
    expect(vi.mocked(sse.streamAnswers).mock.calls[0][1]).toEqual({ "1": "A" });
    expect(result.current.pendingQuestions).toBeNull();
    expect(result.current.lastDocument?.version).toBe("v1");
  });

  it("malformed payload does not crash the stream (fallback: chat keeps going)", async () => {
    drive(
      [
        { kind: "questions", text: null, path: null, payload: "not-json{" },
        { kind: "message", text: "계속", path: null, payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {}); // flush the mount-time history/pending effects first
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions).toBeNull();
    expect(result.current.items.some((i) => i.role === "ai" && i.text.includes("계속"))).toBe(true);
  });

  it("restores pending questions from GET /pending on mount", async () => {
    vi.mocked(client.getPending).mockResolvedValue(QUESTIONS_PAYLOAD);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {}); // flush the mount effect
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
  });

  it("mounts without an unhandled rejection when GET /pending errors (degrades to null)", async () => {
    vi.mocked(client.getPending).mockRejectedValue(new Error("network down"));
    const onUnhandledRejection = vi.fn();
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    try {
      const { result } = renderHook(() => useWorkspaceStream("p1"));
      await act(async () => {}); // flush the mount effect
      expect(result.current.pendingQuestions).toBeNull();
      expect(onUnhandledRejection).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    }
  });

  it("loads history into items on mount", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([
      { role: "user", text: "시작", card: null, name: null , trace: [] },
      { role: "ai", text: "환영", card: null, name: null , trace: [] },
      { role: "card", text: null, card: "questions", name: "mode-selection" , trace: [] },
    ]);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    expect(result.current.historyLoading).toBe(true);
    await act(async () => {});
    expect(result.current.historyLoading).toBe(false);
    expect(result.current.items.map((i) => i.role)).toEqual(["user", "ai", "history-card"]);
  });

  it("history load failure degrades to empty chat", async () => {
    vi.mocked(client.getHistory).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    expect(result.current.historyLoading).toBe(false);
    expect(result.current.items).toEqual([]);
  });

  it("a live turn started while GET /history is still pending is not wiped when history resolves", async () => {
    let resolveHistory!: (items: HistoryItem[]) => void;
    vi.mocked(client.getHistory).mockReturnValue(
      new Promise((resolve) => {
        resolveHistory = resolve;
      }),
    );
    drive([{ kind: "done", text: null, path: null, payload: null }], "streamEvents");

    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("hi"));
    expect(result.current.items).toHaveLength(2); // live user + ai bubble, history still in flight

    await act(async () => {
      resolveHistory([{ role: "user", text: "시작", card: null, name: null , trace: [] }]);
    });
    expect(result.current.historyLoading).toBe(false);
    // History strictly precedes the live turn chronologically — prepended, not replacing it.
    expect(result.current.items).toHaveLength(3);
    expect(result.current.items.map((i) => i.role)).toEqual(["user", "user", "ai"]);
    expect(result.current.items[0]).toMatchObject({ role: "user", text: "시작" });
    expect(result.current.items[1]).toMatchObject({ role: "user", text: "hi" });
  });

  it("a live turn started while GET /history is pending survives an EMPTY history resolution", async () => {
    let resolveHistory!: (items: HistoryItem[]) => void;
    vi.mocked(client.getHistory).mockReturnValue(
      new Promise((resolve) => {
        resolveHistory = resolve;
      }),
    );
    drive([{ kind: "done", text: null, path: null, payload: null }], "streamEvents");

    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("hi"));
    expect(result.current.items).toHaveLength(2);

    await act(async () => {
      resolveHistory([]);
    });
    expect(result.current.historyLoading).toBe(false);
    expect(result.current.items).toHaveLength(2);
  });
});

it("restores tool traces onto AI history items", async () => {
  vi.mocked(client.getHistory).mockResolvedValue([
    { role: "ai", text: "작업", card: null, name: null,
      trace: [{ kind: "status", text: "file_read", path: null },
              { kind: "file_changed", text: null, path: "aiplc-docs/audit.md" }] },
  ]);
  const { result } = renderHook(() => useWorkspaceStream("p1"));
  await act(async () => {});
  const ai = result.current.items[0];
  expect(ai.role).toBe("ai");
  if (ai.role === "ai") {
    expect(ai.trace).toEqual([
      { kind: "status", text: "file_read", path: null },
      { kind: "file_changed", text: null, path: "aiplc-docs/audit.md" },
    ]);
  }
});
