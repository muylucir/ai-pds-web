// frontend/lib/usePrototypeStream.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrototypeStream } from "./usePrototypeStream";
import * as prototypesApi from "@/lib/api/prototypes";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  streamPrototypeEvents: vi.fn(),
  submitPrototypeAnswers: vi.fn(),
  interruptSession: vi.fn(),
}));

// onError의 세션 확인 호출을 검증하기 위한 모킹 — 실제 fetch/navigate 부작용은
// sessionRecovery.test.ts가 별도로 검증하므로, 여기서는 훅이 그 함수를 올바른
// 인자로 "불렀는가"만 확인한다.
vi.mock("@/lib/auth/sessionRecovery", () => ({
  redirectIfSessionExpired: vi.fn(),
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

  it("submitAnswers records the answers as a readable user bubble", async () => {
    // Without this the transcript jumped straight from the question to the
    // agent's next message, so scrolling back gave no hint what was chosen.
    // The letter alone ("A") is not enough — the option text is what makes the
    // bubble legible.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(true);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("질문 있어?"));

    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });

    const bubbles = result.current.items.filter((i) => i.role === "user").map((i) => i.text);
    expect(bubbles).toContain("Q1. 누구?\n→ A. PM");
  });

  it("splits the AI bubble at an answers roundtrip — post-answer text lands on a NEW bubble after the user answer", async () => {
    // The server keeps ONE SSE stream open across the roundtrip, so without a
    // client-side split every message of a whole build session folds into the
    // bubble startBuild created — and the answer's user bubble (appended at the
    // end) reads as if it came AFTER text it actually preceded (files/proto.png).
    let captured: any = null;
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        captured = handlers;
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(true);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    act(() => {
      captured.onEvent({ kind: "message", text: "이 계획대로 진행할까요?", path: null, payload: null });
      captured.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
    });

    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });
    act(() => {
      captured.onEvent({ kind: "message", text: "승인 감사합니다. 빌드를 시작합니다", path: null, payload: null });
      captured.onEvent({ kind: "done", text: null, path: null, payload: null });
    });

    expect(result.current.items.map((i) => [i.role, i.text])).toEqual([
      ["ai", "이 계획대로 진행할까요?"],
      ["user", "Q1. 누구?\n→ A. PM"],
      ["ai", "승인 감사합니다. 빌드를 시작합니다"],
    ]);
  });

  it("splits the AI bubble at a tool boundary — text after a tool run starts a NEW bubble", () => {
    // One build turn emits many TextBlocks separated by tool calls
    // (builder.py's _translate: one "message" event per block). Folding them
    // all together produced the run-on bubble in files/proto.png
    // ("…빌드를 시작합니다.작업 목록을 만들고…"), so a tool run between two
    // texts ends the bubble — the same boundary Claude Code itself renders at.
    drive([
      { kind: "message", text: "빌드를 시작합니다", path: null, payload: null },
      { kind: "status", text: "TodoWrite", path: null, payload: null },
      { kind: "message", text: "Task #1을 시작합니다", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.items.map((i) => [i.role, i.text])).toEqual([
      ["ai", "빌드를 시작합니다"],
      ["ai", "Task #1을 시작합니다"],
    ]);
    // The tool that ended the first bubble is part of ITS trace, and the last
    // bubble is the one `done` closes.
    expect(result.current.items[0]).toMatchObject({
      streaming: false,
      trace: [{ kind: "status", text: "TodoWrite", path: null }],
    });
    expect(result.current.items[1]).toMatchObject({ streaming: false, trace: [] });
  });

  it("keeps consecutive text blocks with no tool between them in ONE bubble", () => {
    // A single AssistantMessage can carry several TextBlocks — that is one
    // utterance, not two, so it must not be split.
    drive([
      { kind: "message", text: "먼저 구조를 잡고", path: null, payload: null },
      { kind: "message", text: " 파일을 만듭니다", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.items.map((i) => [i.role, i.text])).toEqual([
      ["ai", "먼저 구조를 잡고 파일을 만듭니다"],
    ]);
  });

  it("drops the trailing bubble when the turn ends without ever filling it", async () => {
    // submitAnswers opens a bubble for the reply, but the build may finish
    // (or be interrupted) before any text arrives — an empty bubble would
    // render as a blank white box under the answer.
    let captured: any = null;
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        captured = handlers;
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(true);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    act(() => {
      captured.onEvent({ kind: "message", text: "진행할까요?", path: null, payload: null });
      captured.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
    });
    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });
    // The real client relays the frame and THEN calls onDone (prototypes.ts).
    act(() => {
      captured.onEvent({ kind: "done", text: null, path: null, payload: null });
      captured.onDone();
    });

    expect(result.current.items.map((i) => i.role)).toEqual(["ai", "user"]);
  });

  it("keeps an otherwise-empty trailing bubble that carries an error", () => {
    // The error message is the bubble's whole content — pruning it would
    // swallow the only report the user gets.
    drive([{ kind: "error", text: "빌드 실패", path: null, payload: null }]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0]).toMatchObject({ role: "ai", error: "빌드 실패" });
  });

  it("submitAnswers adds no bubble when the 409 path rejects the submission", async () => {
    // Nothing was accepted server-side, so a bubble claiming otherwise would
    // misrepresent the transcript.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD });
        return () => {};
      },
    );
    vi.mocked(prototypesApi.submitPrototypeAnswers).mockResolvedValue(false);

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("질문 있어?"));
    const before = result.current.items.filter((i) => i.role === "user").length;

    await act(async () => {
      await result.current.submitAnswers({ "1": "A" });
    });

    expect(result.current.items.filter((i) => i.role === "user").length).toBe(before);
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

  // onError의 유일한 실제 배선 지점 — 이 콜이 지워지거나 인자가 바뀌면 사용자는
  // 만료된 세션에서도 로그인으로 보내지지 않는다. navigate는 undefined로 넘겨
  // (기본 전체 페이지 이동을 쓰게) 두고, currentPath는 현재 pathname이어야 한다.
  it("checks the session on a transport error (the sole wiring point for redirectIfSessionExpired)", () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onError();
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("go"));
    expect(sessionRecovery.redirectIfSessionExpired).toHaveBeenCalledWith(
      undefined,
      window.location.pathname,
    );
  });
});
