// frontend/lib/usePrototypeStream.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrototypeStream } from "./usePrototypeStream";
import * as prototypesApi from "@/lib/api/prototypes";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  streamPrototypeEvents: vi.fn(),
  submitPrototypeAnswers: vi.fn(),
  interruptSession: vi.fn(),
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

function drive(events: AgentEvent[]) {
  vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
    (_pid: any, _slug: any, _text: any, handlers: any) => {
      for (const ev of events) handlers.onEvent(ev);
      handlers.onDone();
      return () => {};
    },
  );
}

describe("usePrototypeStream", () => {
  beforeEach(() => vi.clearAllMocks());

  it("startBuild opens the stream with __first__ and adds ONLY an AI bubble (no user bubble)", () => {
    drive([
      { kind: "message", text: "빌드를 시작합니다", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(vi.mocked(prototypesApi.streamPrototypeEvents).mock.calls[0][2]).toBe("__first__");
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0]).toMatchObject({ role: "ai", text: "빌드를 시작합니다" });
    expect(result.current.streaming).toBe(false);
  });

  it("send appends a user bubble + AI bubble and folds message frames", () => {
    drive([
      { kind: "status", text: "작업 중", path: null, payload: null },
      { kind: "message", text: "네", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("버튼 색 바꿔줘"));

    expect(vi.mocked(prototypesApi.streamPrototypeEvents).mock.calls[0][2]).toBe("버튼 색 바꿔줘");
    expect(result.current.items[0]).toMatchObject({ role: "user", text: "버튼 색 바꿔줘" });
    expect(result.current.items[1]).toMatchObject({ role: "ai", text: "네", streaming: false });
    expect(result.current.items[1]).toMatchObject({
      trace: [{ kind: "status", text: "작업 중", path: null }],
    });
  });

  it("file_changed accumulates unique paths into changedPaths", () => {
    drive([
      { kind: "file_changed", text: null, path: "prototype/a.tsx", payload: null },
      { kind: "file_changed", text: null, path: "prototype/a.tsx", payload: null },
      { kind: "file_changed", text: null, path: "prototype/b.tsx", payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("go"));
    expect(result.current.changedPaths).toEqual(["prototype/a.tsx", "prototype/b.tsx"]);
  });

  it("a questions event sets pendingQuestions and KEEPS streaming true — the turn stays open on the server", () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
        // Deliberately no onDone() — the harness keeps the SSE stream open
        // across the answers roundtrip (routes.py's submit_answers docstring).
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("질문 있어?"));

    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
    expect(result.current.streaming).toBe(true);
  });

  it("malformed questions payload does not crash the stream (fails closed)", () => {
    drive([
      { kind: "questions", text: null, path: null, payload: "not-json{" },
      { kind: "message", text: "계속", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("go"));
    expect(result.current.pendingQuestions).toBeNull();
    expect(result.current.items.some((i) => i.role === "ai" && i.text.includes("계속"))).toBe(true);
  });

  it("submitAnswers true clears pendingQuestions without opening a new stream", async () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(true);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("질문 있어?"));
    expect(result.current.pendingQuestions).not.toBeNull();

    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });

    expect(prototypesApi.submitPrototypeAnswers).toHaveBeenCalledWith("p1", "todo-app", { "1": "A" });
    expect(result.current.pendingQuestions).toBeNull();
    // No second call to streamPrototypeEvents — the same open stream keeps going.
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenCalledTimes(1);
  });

  it("submitAnswers false (409) keeps pendingQuestions and surfaces an error on the AI item", async () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(false);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("질문 있어?"));

    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });

    expect(result.current.pendingQuestions).not.toBeNull();
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" ? ai.error : null).toBeTruthy();
  });

  it("interrupt calls interruptSession; the open stream later reports done/interrupted", async () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "status", text: "작업 중", path: null, payload: null });
        return () => {};
      },
    );
    vi.mocked(prototypesApi.interruptSession).mockResolvedValue({ status: "interrupting" });

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("go"));
    expect(result.current.streaming).toBe(true);

    await act(async () => {
      await result.current.interrupt();
    });
    expect(prototypesApi.interruptSession).toHaveBeenCalledWith("p1", "todo-app");
    // Still streaming — the hook doesn't end the turn itself; it waits for
    // the open stream's own done/error event.
    expect(result.current.streaming).toBe(true);
  });

  it("done ends the turn; error ends the turn and clears pendingQuestions", () => {
    drive([
      { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
      { kind: "error", text: "중단됨", path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("go"));
    expect(result.current.streaming).toBe(false);
    expect(result.current.pendingQuestions).toBeNull();
  });
});
