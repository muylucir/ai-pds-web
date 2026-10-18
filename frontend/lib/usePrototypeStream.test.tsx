// frontend/lib/usePrototypeStream.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrototypeStream, type ChatItem } from "./usePrototypeStream";
import { RECONNECT_DELAYS_MS } from "./useWorkspaceStream";
import * as prototypesApi from "@/lib/api/prototypes";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  streamPrototypeEvents: vi.fn(),
  watchBuildTurn: vi.fn(),
  getBuildSession: vi.fn(),
  getBuildHistory: vi.fn(),
  submitPrototypeAnswers: vi.fn(),
  interruptSession: vi.fn(),
  startSession: vi.fn(),
}));

// Mock for verifying onError's session-check call. The real fetch/navigate side
// effects are verified separately by sessionRecovery.test.ts, so here we only check
// that the hook "called" that function with the right arguments.
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

// The text of a timeline item. A restored question card (history-card) has no text.
const textOf = (i: ChatItem) => ("text" in i ? i.text : null);

const ANSWERS_EVENT: AgentEvent = { kind: "answers", text: null, path: null, payload: JSON.stringify({ answers: { "1": "A" } }) };

function drive(events: AgentEvent[]) {
  vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
    (_pid: any, _slug: any, _text: any, handlers: any) => {
      for (const ev of events) handlers.onEvent(ev);
      handlers.onDone();
      return () => {};
    },
  );
}


// Flush one frame; text deltas are attached at that point (lib/useTextDeltas.ts).
const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

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

  it("activity는 마지막에 온 이벤트가 확정한다 — 워크스페이스 훅과 같은 규약", () => {
    // This is the value the pinned line above the input box reads. If it were
    // derived from the last tool in the trace, the tool name would linger while
    // text streams after the tool finishes. Both screens use the same bar, so
    // both hooks must follow the same contract.
    drive([
      { kind: "status", text: "Write", path: null, payload: null },
      { kind: "message", text: "만들었습니다", path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.send("만들어줘"));
    const ai = result.current.items.filter((i) => i.role === "ai").at(-1);
    expect(ai).toMatchObject({ activity: { kind: "writing" } });
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
    let captured: any = null;
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        captured = handlers;
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
    // The answer bubble is drawn from the `answers` event the server records in
    // the turn log; replay after a reload takes the same path, so it yields the
    // same conversation (proto/session.send_answers).
    expect(result.current.pendingQuestions).toBeNull();
    act(() => captured.onEvent(ANSWERS_EVENT));

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
      captured.onEvent(ANSWERS_EVENT);
      captured.onEvent({ kind: "message", text: "승인 감사합니다. 빌드를 시작합니다", path: null, payload: null });
      captured.onEvent({ kind: "done", text: null, path: null, payload: null });
    });

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
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

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
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

  it("lands batched text on the bubble it was written to when a tool splits mid-frame", async () => {
    // Text deltas are attached per frame (useTextDeltas). If a tool splits the bubble
    // before a frame arrives, the earlier text must go to the earlier bubble and the later
    // text to the new one; the turn is still running, so no terminal event flushes it for us.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({ kind: "message", text: "빌드를 ", path: null, payload: null });
        handlers.onEvent({ kind: "message", text: "시작합니다", path: null, payload: null });
        handlers.onEvent({ kind: "status", text: "TodoWrite", path: null, payload: null });
        handlers.onEvent({ kind: "message", text: "Task #1", path: null, payload: null });
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    await act(nextFrame);

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
      ["ai", "빌드를 시작합니다"],
      ["ai", "Task #1"],
    ]);
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

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
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
      captured.onEvent(ANSWERS_EVENT);
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

  // The only real wiring point for onError. If this call is removed or its arguments change,
  // the user is not sent to login even on an expired session. navigate is passed as undefined
  // (so the default full-page navigation is used), and currentPath must be the current pathname.
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

describe("build_complete", () => {
  beforeEach(() => vi.clearAllMocks());

  it("a build_complete event lands in buildComplete state with summary and remaining parsed", () => {
    drive([
      {
        kind: "build_complete",
        text: null,
        path: null,
        payload: JSON.stringify({ summary: "할 일 앱", remaining: "다크 모드" }),
      },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.buildComplete).toEqual({ summary: "할 일 앱", remaining: "다크 모드" });
  });

  it("a build_complete event does NOT end streaming — the following done does that", () => {
    // drive() calls onDone() synchronously at the end, so streaming is already
    // false and the mid-stream state cannot be observed. Here a custom
    // mockImplementation that never calls done captures that intermediate state.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({
          kind: "build_complete",
          text: null,
          path: null,
          payload: JSON.stringify({ summary: "완성", remaining: "" }),
        });
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.buildComplete).toEqual({ summary: "완성", remaining: "" });
    expect(result.current.streaming).toBe(true);
  });

  it("a malformed build_complete payload leaves buildComplete null and the stream continues", () => {
    drive([
      { kind: "build_complete", text: null, path: null, payload: "{not json" },
      { kind: "message", text: "계속 진행", path: null, payload: null },
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.buildComplete).toBeNull();
    expect(result.current.items.some((it) => it.role === "ai" && it.text.includes("계속 진행"))).toBe(
      true,
    );
  });

  it("restartForImprovement calls startSession, clears buildComplete, and re-opens the stream with __first__", async () => {
    // With drive(), the second stream opened by the restart would replay the same
    // event array and build_complete would be set again. Here we capture handlers
    // and emit by hand, so we observe the state where the newly opened stream after
    // the restart has not emitted anything yet.
    let captured: any = null;
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        captured = handlers;
        return () => {};
      },
    );
    vi.mocked(prototypesApi.startSession).mockResolvedValue({ status: "ok" });

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    act(() => {
      captured.onEvent({
        kind: "build_complete",
        text: null,
        path: null,
        payload: JSON.stringify({ summary: "완성", remaining: "" }),
      });
      captured.onEvent({ kind: "done", text: null, path: null, payload: null });
    });
    expect(result.current.buildComplete).not.toBeNull();

    await act(async () => {
      await result.current.restartForImprovement();
    });

    expect(prototypesApi.startSession).toHaveBeenCalledWith("p1", "todo-app");
    expect(result.current.buildComplete).toBeNull();
    // The opening turn fires again; the server substitutes the handoff prompt for __first__.
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenLastCalledWith(
      "p1",
      "todo-app",
      "__first__",
      expect.anything(),
    );
  });

  it("restartForImprovement still opens session B's stream when stream A never reached done (the completion-card race)", async () => {
    // build_complete is an event that arrives before done (see applyEvent), so
    // during the 0-5 s window in which the agent sends more closing text, the
    // card is visible but stream A is still open (onDone not called, stopRef
    // non-null). Clicking "개선 이어서 하기" (continue improving) in that window makes
    // startSession open a new session B, but the `if (stopRef.current) return;`
    // guard in startBuild() treated A as a still-live stream and never opened
    // B's __first__ stream (the bug before the fix), leaving B as a zombie
    // session holding the build slot with nobody driving it. A
    // mockImplementation that never calls onDone pins that window open to
    // reproduce it.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({
          kind: "build_complete",
          text: null,
          path: null,
          payload: JSON.stringify({ summary: "완성", remaining: "" }),
        });
        // Never call onDone(); stream A stays open.
        return () => {};
      },
    );
    vi.mocked(prototypesApi.startSession).mockResolvedValue({ status: "ok" });

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.buildComplete).not.toBeNull();
    expect(result.current.streaming).toBe(true); // Stream A is still open.

    await act(async () => {
      await result.current.restartForImprovement();
    });

    expect(prototypesApi.startSession).toHaveBeenCalledWith("p1", "todo-app");
    // Session B actually opens a stream; there must be a second call with __first__.
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenLastCalledWith(
      "p1",
      "todo-app",
      "__first__",
      expect.anything(),
    );
  });
});

// ---- Sub-agent rows ----
//
// What this block protects: while the build agent hands work off in parallel
// via the Agent tool, the screen can say what is running. Previously the SDK's
// `Task*` messages were dropped in the backend translator, so this value did not
// even exist (backend/aipds/agent_activity.py).

/** A driver that never calls onDone, for tests that need to see the state of
 *  a running turn. The default `drive` feeds the frames and closes the turn
 *  immediately, so the rows are already cleared. */
function driveOpen(events: AgentEvent[]) {
  vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
    (_pid: any, _slug: any, _text: any, handlers: any) => {
      for (const ev of events) handlers.onEvent(ev);
      return () => {};
    },
  );
}

const activity = (payload: Record<string, unknown>): AgentEvent =>
  ({ kind: "agent_activity", text: null, path: null, payload: JSON.stringify(payload) });

describe("usePrototypeStream — 서브에이전트 행", () => {
  beforeEach(() => vi.clearAllMocks());

  it("도는 에이전트를 행으로 내보낸다", () => {
    driveOpen([
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
      activity({ task_id: "a2", state: "started", label: "데이터 모델" }),
      activity({ task_id: "a1", state: "progress", tool: "Write", detail: "app/page.tsx" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.agents.map((a) => a.label)).toEqual(["화면 골격", "데이터 모델"]);
    expect(result.current.agents[0]).toMatchObject({ tool: "Write", detail: "app/page.tsx" });
  });

  it("끝난 에이전트는 행에서 빠지고 트레이스에 남는다", () => {
    // Rows are "what is running now"; the trace is "what happened". If a finished agent
    // stayed in the rows, a spinning spinner would remain even after the turn ends.
    driveOpen([
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
      activity({ task_id: "a1", state: "done", status: "completed", summary: "만들었다" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    expect(result.current.agents).toEqual([]);
    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.trace).toEqual([
      { kind: "agent", text: "화면 골격", path: null, detail: "만들었다",
        status: "completed", taskId: "a1" },
    ]);
  });

  it("늦게 온 요약이 완료 줄을 갱신한다 — 두 줄이 되지 않는다", () => {
    // Observed: task_updated (no summary) → task_notification (with summary).
    // The backend does not know which termination is the last, so it sends the
    // summary again; folding them is this side's job.
    driveOpen([
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
      activity({ task_id: "a1", state: "done", status: "completed" }),
      activity({ task_id: "a1", state: "done", status: "completed", summary: "만들었다" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.trace).toEqual([
      { kind: "agent", text: "화면 골격", path: null, detail: "만들었다",
        status: "completed", taskId: "a1" },
    ]);
  });

  it("에이전트가 여럿이면 각자 자기 완료 줄을 갖는다", () => {
    driveOpen([
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
      activity({ task_id: "a2", state: "started", label: "데이터 모델" }),
      activity({ task_id: "a1", state: "done", status: "completed", summary: "A" }),
      activity({ task_id: "a2", state: "done", status: "failed", summary: "B" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.trace.map((e) => [e.taskId, e.status, e.detail])).toEqual([
      ["a1", "completed", "A"],
      ["a2", "failed", "B"],
    ]);
  });

  it("서브에이전트 활동이 총괄 고정 줄을 덮지 않는다", () => {
    // The pinned line describes the orchestrating agent's work. If a sub-agent's tool name overwrote
    // that slot, what the orchestrating agent is waiting on would be erased, and the old behavior of
    // that line flickering between agents would come right back.
    driveOpen([
      { kind: "status", text: "Agent", path: null,
        payload: JSON.stringify({ detail: "화면 골격 만들기" }) },
      activity({ task_id: "a1", state: "progress", tool: "Bash", detail: "npm i" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.activity).toEqual({ kind: "tool", tool: "Agent", detail: "화면 골격 만들기" });
  });

  it("턴이 끝나면 행이 비워진다", () => {
    drive([
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
      { kind: "done", text: null, path: null, payload: null },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.agents).toEqual([]);
  });

  it("깨진 payload가 스트림을 멈추지 않는다", () => {
    // Progress display is auxiliary info; the build must not die because of it
    // (safeParse's contract).
    driveOpen([
      { kind: "agent_activity", text: null, path: null, payload: "{not json" },
      activity({ task_id: "a1", state: "started", label: "화면 골격" }),
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.agents.map((a) => a.label)).toEqual(["화면 골격"]);
  });
});

describe("usePrototypeStream — 도구가 무엇을 했는지", () => {
  beforeEach(() => vi.clearAllMocks());

  it("status의 detail을 트레이스와 고정 줄에 함께 싣는다", () => {
    // Previously this value was dropped, so unlike Discovery the build screen
    // showed only a bare `Bash`; not knowing which command was running is one
    // reason the screen looked frozen.
    driveOpen([
      { kind: "status", text: "Bash", path: null,
        payload: JSON.stringify({ detail: "npm run build" }) },
    ]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.trace[0]).toMatchObject({ kind: "status", text: "Bash", detail: "npm run build" });
    expect(ai.activity).toEqual({ kind: "tool", tool: "Bash", detail: "npm run build" });
  });

  it("detail이 없는 status도 그대로 동작한다", () => {
    driveOpen([{ kind: "status", text: "Write", path: null, payload: null }]);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());

    const ai = result.current.items.find((it) => it.role === "ai")!;
    expect(ai.trace[0]).toMatchObject({ kind: "status", text: "Write", detail: null });
  });
});


// ---- When the build screen reopens (reload, close and reopen) ----
// A build turn is server work owned by the session, and the session keeps all
// of its turns (backend aipds/turn_job.py, proto/session.py). The reopened
// screen replays those turns.
const ev = (kind: AgentEvent["kind"], text: string | null = null,
            payload: string | null = null): AgentEvent =>
  ({ kind, text, path: null, payload });

describe("usePrototypeStream — resume", () => {
  beforeEach(() => vi.clearAllMocks());

  it("replays the session's turns in order and attaches to the running one", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [],
      turns: [
        { turn_id: "t1", state: "done", last_seq: 2, input: null },
        { turn_id: "t2", state: "running", last_seq: 1, input: "버튼 색 바꿔 줘" },
      ],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, turnId: any, after: any, handlers: any) => {
        expect(after).toBe(0);                    // replays from the beginning
        if (turnId === "t1") {
          handlers.onEvent(ev("message", "계획을 세웠습니다"), 1);
          handlers.onEvent(ev("done"), 2);
          handlers.onDone();
        } else {
          handlers.onEvent(ev("message", "바꾸는 중"), 1);   // turn still running
        }
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    await act(nextFrame);   // the running turn's last text is attached on the next frame

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
      ["ai", "계획을 세웠습니다"],
      ["user", "버튼 색 바꿔 줘"],
      ["ai", "바꾸는 중"],
    ]);
    expect(result.current.streaming).toBe(true);
  });

  it("restores a question and its answer in the order they happened", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [],
      turns: [{ turn_id: "t1", state: "running", last_seq: 4, input: null }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("message", "진행할까요?"), 1);
        handlers.onEvent(ev("questions", null, QUESTIONS_PAYLOAD), 2);
        handlers.onEvent(ANSWERS_EVENT, 3);
        handlers.onEvent(ev("message", "진행합니다"), 4);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    await act(nextFrame);   // the running turn's last text is attached on the next frame

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
      ["ai", "진행할까요?"],
      ["user", "Q1. 누구?\n→ A. PM"],
      ["ai", "진행합니다"],
    ]);
    // The card for an already-answered question does not reappear.
    expect(result.current.pendingQuestions).toBeNull();
  });

  it("an unanswered question comes back as an open card", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [],
      turns: [{ turn_id: "t1", state: "running", last_seq: 2, input: null }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("questions", null, QUESTIONS_PAYLOAD), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
  });

  it("does nothing when there is no session and no past conversation", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({ items: [], turns: [] });
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    expect(result.current.items).toEqual([]);
    expect(prototypesApi.watchBuildTurn).not.toHaveBeenCalled();
  });

  it("lays the past conversation under the open session's turns", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [
        { role: "user", text: "만들어줘", card: null, name: null, trace: [] },
        { role: "ai", text: "만들었습니다", card: null, name: null,
          trace: [{ kind: "file_changed", text: null, path: "app/page.tsx" }] },
        { role: "card", text: null, card: "questions", name: "prototype-questions", trace: [] },
      ],
      turns: [{ turn_id: "t1", state: "running", last_seq: 1, input: "버튼 색 바꿔 줘" }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("message", "바꾸는 중"), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    await act(nextFrame);   // the running turn's last text is attached on the next frame

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
      ["user", "만들어줘"],
      ["ai", "만들었습니다"],
      ["history-card", null],
      ["user", "버튼 색 바꿔 줘"],
      ["ai", "바꾸는 중"],
    ]);
    // Restored bubbles are finished; the typing indicator is only for the running turn.
    const restored = result.current.items[1];
    expect(restored.role === "ai" && restored.streaming).toBe(false);
    // File changes from past conversation are not this session's "changed files" list.
    expect(result.current.changedPaths).toEqual([]);
    expect(result.current.historyLoading).toBe(false);
  });

  it("still attaches to the open session when the history cannot be read", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockRejectedValue(new Error("500"));
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue({
      status: "building",
      turns: [{ turn_id: "t1", state: "running", last_seq: 1, input: null }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("message", "만드는 중"), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    await act(nextFrame);   // the running turn's last text is attached on the next frame

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([["ai", "만드는 중"]]);
    expect(result.current.streaming).toBe(true);
  });
});

describe("usePrototypeStream — context usage", () => {
  beforeEach(() => vi.clearAllMocks());
  const usage = (left: number) => ({ total_tokens: 1000, max_tokens: 750000,
                                     compact_at_tokens: 717000, left_pct: left });

  it("takes the last turn's value from the history, then the replayed turn's events", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [], context: usage(70),
      turns: [{ turn_id: "t1", state: "running", last_seq: 1, input: null }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("context", null, JSON.stringify(usage(55))), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });

    // The replayed turn's value overwrote the history's value (70).
    expect(result.current.context?.left_pct).toBe(55);
    // It neither creates nor splits a bubble.
    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([["ai", ""]]);
  });

  it("a closed session's panel shows the last turn's value from the history", async () => {
    vi.mocked(prototypesApi.getBuildHistory).mockResolvedValue({
      items: [], turns: [], context: usage(70) });
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    expect(result.current.context?.left_pct).toBe(70);
  });

  it("an improvement session starts without the previous session's value", async () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent(ev("context", null, JSON.stringify(usage(20))), 1);
        handlers.onEvent(ev("done"), 2);
        handlers.onDone();
        return () => {};
      },
    );
    vi.mocked(prototypesApi.startSession).mockResolvedValue({ status: "starting" });
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.context?.left_pct).toBe(20);

    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(() => () => {});
    await act(async () => { await result.current.restartForImprovement(); });
    expect(result.current.context).toBeNull();
  });
});

describe("usePrototypeStream — restoreHistory", () => {
  beforeEach(() => vi.clearAllMocks());

  it("puts the past conversation BEFORE a turn that already started", async () => {
    // A freshly opened session: the opening turn appears on screen before the history response.
    // Replacing would erase the turn that just started.
    let resolveHistory!: (v: { items: any[]; turns: any[] }) => void;
    vi.mocked(prototypesApi.getBuildHistory).mockReturnValue(
      new Promise((r) => { resolveHistory = r; }));
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent(ev("message", "다시 만듭니다"), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    let pending!: Promise<void>;
    act(() => {
      result.current.startBuild();
      pending = result.current.restoreHistory();
    });
    expect(result.current.historyLoading).toBe(true);
    await act(async () => {
      resolveHistory({ items: [{ role: "ai", text: "지난 빌드는 실패했습니다",
                                 card: null, name: null, trace: [] }], turns: [] });
      await pending;
    });
    await act(nextFrame);   // running turn's text lands on the next frame (useTextDeltas)

    expect(result.current.items.map((i) => [i.role, textOf(i)])).toEqual([
      ["ai", "지난 빌드는 실패했습니다"],
      ["ai", "다시 만듭니다"],
    ]);
    expect(result.current.historyLoading).toBe(false);
  });
});

describe("usePrototypeStream — reconnect", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("reattaches to the same build turn from the last seq it received", async () => {
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onCreated?.({ turn_id: "t1" });
        handlers.onEvent(ev("message", "만드는 중"), 1);
        handlers.onError?.(new Event("error"));
        handlers.onDone();
        return () => {};
      },
    );
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue({
      status: "building",
      turns: [{ turn_id: "t1", state: "running", last_seq: 3, input: null }],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(ev("message", " 계속"), 2);
        handlers.onEvent(ev("done"), 3);
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.streaming).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0]); });

    expect(prototypesApi.watchBuildTurn).toHaveBeenCalledWith(
      "p1", "todo-app", "t1", 1, expect.anything());
    expect(result.current.items.map(textOf)).toEqual(["만드는 중 계속"]);
    expect(result.current.streaming).toBe(false);
  });
});
