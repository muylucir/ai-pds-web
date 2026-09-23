// frontend/lib/usePrototypeStream.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrototypeStream } from "./usePrototypeStream";
import { RECONNECT_DELAYS_MS } from "./useWorkspaceStream";
import * as prototypesApi from "@/lib/api/prototypes";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  streamPrototypeEvents: vi.fn(),
  watchBuildTurn: vi.fn(),
  getBuildSession: vi.fn(),
  submitPrototypeAnswers: vi.fn(),
  interruptSession: vi.fn(),
  startSession: vi.fn(),
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
    // 입력창 위 고정 줄이 읽는 값이다. 트레이스에서 마지막 도구를 뽑아 쓰면 도구가
    // 끝나고 텍스트가 흐르는 동안에도 그 도구 이름이 남는다. 두 화면이 같은 바를
    // 쓰므로 두 훅의 규약도 같아야 한다.
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
    // 답 말풍선은 서버가 턴 로그에 남긴 `answers` 이벤트로 그려진다 — 새로고침 뒤
    // 재생도 같은 길을 타므로 같은 대화가 된다(proto/session.send_answers).
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
    // drive()는 마지막에 onDone()까지 동기로 호출해버려 streaming이 이미
    // false가 된 뒤라 mid-stream 상태를 관찰할 수 없다. 여기서는 done을
    // 호출하지 않는 커스텀 mockImplementation으로 그 중간 상태를 붙잡는다.
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
    // drive()를 쓰면 재시작으로 연 두 번째 스트림도 같은 이벤트 배열을 그대로
    // 재생해 build_complete가 다시 서고 만다. 여기서는 handlers를 붙잡아두고
    // 직접 emit해서, 재시작 후 새로 연 스트림이 아직 아무것도 방출하지 않은
    // 상태를 그대로 관찰한다.
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
    // 개시 턴이 다시 발화된다 — 서버가 __first__를 핸드오프 프롬프트로 치환한다.
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenLastCalledWith(
      "p1",
      "todo-app",
      "__first__",
      expect.anything(),
    );
  });

  it("restartForImprovement still opens session B's stream when stream A never reached done (the completion-card race)", async () => {
    // build_complete는 done보다 먼저 서는 이벤트다(applyEvent 참고) — 그래서
    // 에이전트가 마무리 텍스트를 더 보내는 0~5초 창 동안 카드는 보이지만
    // 스트림 A는 아직 열려 있다(onDone 미호출, stopRef가 non-null). 그 창에서
    // "개선 이어서 하기"를 누르면 startSession이 세션 B를 새로 열지만,
    // startBuild()의 `if (stopRef.current) return;` 가드가 A를 아직 살아있는
    // 스트림으로 여겨 B의 __first__ 스트림을 영영 열지 못했다(수정 전 버그) —
    // B가 빌드 슬롯을 쥔 채 아무도 몰고 가지 않는 좀비 세션이 된다. onDone을
    // 절대 부르지 않는 mockImplementation으로 그 창을 고정해 재현한다.
    vi.mocked(prototypesApi.streamPrototypeEvents).mockImplementation(
      (_pid: any, _slug: any, _text: any, handlers: any) => {
        handlers.onEvent({
          kind: "build_complete",
          text: null,
          path: null,
          payload: JSON.stringify({ summary: "완성", remaining: "" }),
        });
        // onDone()을 절대 호출하지 않는다 — 스트림 A는 열린 채로 남는다.
        return () => {};
      },
    );
    vi.mocked(prototypesApi.startSession).mockResolvedValue({ status: "ok" });

    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    act(() => result.current.startBuild());
    expect(result.current.buildComplete).not.toBeNull();
    expect(result.current.streaming).toBe(true); // 스트림 A가 아직 열려 있다.

    await act(async () => {
      await result.current.restartForImprovement();
    });

    expect(prototypesApi.startSession).toHaveBeenCalledWith("p1", "todo-app");
    // 세션 B가 실제로 스트림을 연다 — __first__로 두 번째 호출이 있어야 한다.
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(prototypesApi.streamPrototypeEvents)).toHaveBeenLastCalledWith(
      "p1",
      "todo-app",
      "__first__",
      expect.anything(),
    );
  });
});

// ---- 서브에이전트 행 ----
//
// 이 블록이 지키는 것: 빌드 에이전트가 Agent 도구로 일을 병렬로 넘기는 동안
// 화면이 무엇이 도는지 말할 수 있다. 종전에는 SDK의 `Task*` 메시지가 백엔드
// 번역부에서 버려져 이 값이 존재하지도 않았다(backend/aipds/agent_activity.py).

/** onDone을 부르지 않는 드라이버 — 도는 턴의 상태를 봐야 하는 테스트용.
 *  기본 `drive`는 프레임을 넣고 즉시 턴을 닫으므로 행이 이미 비워져 있다. */
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
    // 행은 "지금 도는 것"이고 트레이스는 "일어난 일"이다. 끝난 에이전트가 행에
    // 남으면 턴이 끝난 뒤에도 도는 스피너가 남는다.
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
    // 실측: task_updated(요약 없음) → task_notification(요약 있음). 백엔드는 어느
    // 종료가 마지막인지 모르므로 요약을 한 번 더 보내고, 접는 것은 여기 몫이다.
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
    // 고정 줄은 총괄 에이전트의 일을 말한다. 서브에이전트의 도구 이름이 그 자리를
    // 덮으면 총괄이 무엇을 기다리는지가 지워지고, 그 줄이 에이전트들 사이에서
    // 깜빡이던 종전 동작이 그대로 돌아온다.
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
    // 진행 표시는 부수 정보다 — 그것 때문에 빌드가 죽으면 안 된다(safeParse의 계약).
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
    // 종전에는 이 값을 버려서 빌드 화면이 Discovery와 달리 맨 `Bash`만 보였다 —
    // 무슨 명령이 도는지 알 수 없는 것이 화면이 멈춘 듯 보인 이유 중 하나다.
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


// ---- 빌드 화면이 다시 열릴 때 (새로고침, 닫았다 엶) ----
// 빌드 턴은 세션의 서버 작업이고 세션은 자기 턴을 전부 보존한다(backend
// aipds/turn_job.py, proto/session.py). 다시 열린 화면은 그 턴들을 재생한다.
const ev = (kind: AgentEvent["kind"], text: string | null = null,
            payload: string | null = null): AgentEvent =>
  ({ kind, text, path: null, payload });

describe("usePrototypeStream — resume", () => {
  beforeEach(() => vi.clearAllMocks());

  it("replays the session's turns in order and attaches to the running one", async () => {
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue({
      status: "building",
      turns: [
        { turn_id: "t1", state: "done", last_seq: 2, input: null },
        { turn_id: "t2", state: "running", last_seq: 1, input: "버튼 색 바꿔 줘" },
      ],
    });
    vi.mocked(prototypesApi.watchBuildTurn).mockImplementation(
      (_pid: any, _slug: any, turnId: any, after: any, handlers: any) => {
        expect(after).toBe(0);                    // 처음부터 재생한다
        if (turnId === "t1") {
          handlers.onEvent(ev("message", "계획을 세웠습니다"), 1);
          handlers.onEvent(ev("done"), 2);
          handlers.onDone();
        } else {
          handlers.onEvent(ev("message", "바꾸는 중"), 1);   // 아직 도는 턴
        }
        return () => {};
      },
    );
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });

    expect(result.current.items.map((i) => [i.role, i.text])).toEqual([
      ["ai", "계획을 세웠습니다"],
      ["user", "버튼 색 바꿔 줘"],
      ["ai", "바꾸는 중"],
    ]);
    expect(result.current.streaming).toBe(true);
  });

  it("restores a question and its answer in the order they happened", async () => {
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue({
      status: "building",
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

    expect(result.current.items.map((i) => [i.role, i.text])).toEqual([
      ["ai", "진행할까요?"],
      ["user", "Q1. 누구?\n→ A. PM"],
      ["ai", "진행합니다"],
    ]);
    // 이미 답한 질문의 카드는 다시 뜨지 않는다.
    expect(result.current.pendingQuestions).toBeNull();
  });

  it("an unanswered question comes back as an open card", async () => {
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue({
      status: "waiting_input",
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

  it("does nothing when there is no session", async () => {
    vi.mocked(prototypesApi.getBuildSession).mockResolvedValue(null);
    const { result } = renderHook(() => usePrototypeStream("p1", "todo-app"));
    await act(async () => { await result.current.resume(); });
    expect(result.current.items).toEqual([]);
    expect(prototypesApi.watchBuildTurn).not.toHaveBeenCalled();
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
    expect(result.current.items.map((i) => i.text)).toEqual(["만드는 중 계속"]);
    expect(result.current.streaming).toBe(false);
  });
});
