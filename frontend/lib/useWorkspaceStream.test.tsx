// frontend/lib/useWorkspaceStream.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { RECONNECT_DELAYS_MS, useWorkspaceStream } from "./useWorkspaceStream";
import * as sse from "@/lib/api/sse";
import * as client from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent, HistoryItem } from "@/lib/api/types";

vi.mock("@/lib/api/sse");
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig()),
  getPending: vi.fn().mockResolvedValue(null),
  getHistory: vi.fn().mockResolvedValue([]),
  getTurn: vi.fn().mockResolvedValue(null),
  getContext: vi.fn().mockResolvedValue(null),
}));

// Mocks to verify onError's session check call — the actual fetch/navigate side effects are
// verified separately by sessionRecovery.test.ts, so here we only check that the hook
// "called" that function with the right arguments.
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

// A round that came from a question file. `file` is the discriminator and `interrupt_id` is
// an empty string — there is no parked turn, so the answer goes to that file (backend
// routes/answers.py).
const FILE_QUESTIONS_PAYLOAD = JSON.stringify({
  interrupt_id: "",
  file: "aiplc-docs/discovery/envision/pain-point-questions.md",
  questions: {
    name: "pain-point-questions.md",
    preamble: null,
    parse_ok: true,
    raw_markdown: null,
    questions: [
      {
        number: 3,
        category: null,
        text: "어느 쪽입니까?",
        context: "| # | 항목 |\n|---|---|\n| 1 | 반복 삭감 |",
        answer: null,
        options: [{ letter: "A", text: "왼쪽", is_other: false, recommended: false }],
      },
    ],
  },
});

// Default: **no turn is running** when the screen opens. Tests that exercise the path of
// attaching to a running turn override this.
function noRunningTurn() {
  vi.mocked(client.getTurn).mockResolvedValue(null);
}

// Flush one frame — text deltas are appended then (lib/useTextDeltas.ts).
const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

let createdSummary = "Q1. 질문?\n→ A. 보기 라벨";

function drive(
  events: AgentEvent[],
  impl: "streamEvents" | "streamAnswers" | "streamFileAnswers",
) {
  // handlers is the **last** argument — streamFileAnswers takes one extra file and has 4
  // arguments. Hard-coding position 3 would read answers as handlers for that function.
  vi.mocked(sse[impl]).mockImplementation((...args: any[]) => {
    const handlers = args[args.length - 1];
    // The turn creation response comes first — the bubble text rides on it
    // (`summary` in routes/answers.py). Only file rounds use this callback.
    handlers.onCreated?.({ summary: createdSummary });
    for (const ev of events) handlers.onEvent(ev);
    handlers.onDone();
    return () => {};
  });
}

describe("useWorkspaceStream", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

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

  it("a file-sourced round submits to the file, not to the parked turn", async () => {
    // A round with no parked can_use_tool future — the PostToolUse hook has already ended the
    // turn, so sending it to `/answers` (resume the same turn) would give a 400.
    drive(
      [
        { kind: "questions", text: null, path: null, payload: FILE_QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions?.file).toBe(
      "aiplc-docs/discovery/envision/pain-point-questions.md");

    drive([{ kind: "done", text: null, path: null, payload: null }], "streamFileAnswers");
    act(() => result.current.submitAnswers({ "3": "A" }));

    expect(vi.mocked(sse.streamAnswers)).not.toHaveBeenCalled();
    const call = vi.mocked(sse.streamFileAnswers).mock.calls[0];
    expect(call[1]).toBe("aiplc-docs/discovery/envision/pain-point-questions.md");
    expect(call[2]).toEqual({ "3": "A" });
    expect(result.current.pendingQuestions).toBeNull();
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

  it("restores the last turn's context usage on mount", async () => {
    const usage = { total_tokens: 285000, max_tokens: 750000,
                    compact_at_tokens: 717000, left_pct: 60 };
    vi.mocked(client.getContext).mockResolvedValueOnce(usage);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    expect(result.current.context).toEqual(usage);
  });

  it("a context event updates the usage, and a late record does not overwrite it", async () => {
    // The record (GET /context) always belongs to a turn earlier than the current one.
    let resolveRecord!: (v: any) => void;
    vi.mocked(client.getContext).mockReturnValueOnce(
      new Promise((r) => { resolveRecord = r; }));
    drive([
      { kind: "context", text: null, path: null,
        payload: JSON.stringify({ total_tokens: 30000, max_tokens: 200000,
                                  compact_at_tokens: 200000, left_pct: 85 }) },
      { kind: "done", text: null, path: null, payload: null },
    ], "streamEvents");
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    act(() => result.current.send("안녕"));
    await act(async () => {
      resolveRecord({ total_tokens: 1, max_tokens: 200000, compact_at_tokens: 200000, left_pct: 99 });
    });

    expect(result.current.context?.left_pct).toBe(85);
    // Usage does not belong to a bubble.
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && "text" in ai ? ai.text : null).toBe("");
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

  it("답변 제출 턴의 answers를 items로 옮긴다", async () => {
    // If this plumbing breaks, ChatTimeline loses the basis for building the text in the UI
    // language, and the backend's Korean fallback text shows up as is in the English UI — looking
    // only at the screen it seems "it was not translated", but the cause is here.
    vi.mocked(client.getHistory).mockResolvedValue([
      { role: "user", text: "답변 제출 — 1: A", card: null, name: null, trace: [],
        answers: { "1": "A" } },
      { role: "user", text: "그냥 발화", card: null, name: null, trace: [] },
    ]);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    const [answered, plain] = result.current.items;
    expect(answered.role === "user" && answered.answers).toEqual({ "1": "A" });
    // An ordinary bubble without answers stays null — it must be null, not undefined, for
    // ChatTimeline's `item.answers ?` branch to take the text fallback.
    expect(plain.role === "user" && plain.answers).toBeNull();
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

  // The only real wiring point of onError — if this call is removed or its arguments change,
  // the user is not sent to login even on an expired session. navigate is passed as undefined
  // (so the default full-page navigation is used), and currentPath must be the current pathname.
  it("checks the session on a transport error (the sole wiring point for redirectIfSessionExpired)", async () => {
    vi.mocked(sse.streamEvents).mockImplementation((_pid: any, _arg: any, handlers: any) => {
      handlers.onError(new Error("boom"));
      return () => {};
    });
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {}); // flush the mount-time history/pending effects first
    act(() => result.current.send("hi"));
    expect(sessionRecovery.redirectIfSessionExpired).toHaveBeenCalledWith(
      undefined,
      window.location.pathname,
    );
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
      // The point of detail is that live (status payload) and restore (HistoryTraceEntry field)
      // converge on the same shape — the value is produced in one place, the backend
      // (backend/aipds/tool_trace.py).
      { kind: "status", text: "file_read", path: null, detail: null },
      { kind: "file_changed", text: null, path: "aiplc-docs/audit.md", detail: null },
    ]);
  }
});

describe("useWorkspaceStream — activeDoc/turnSeq (문서 패널 싱크, ui-bug2)", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  it("doc성 file_changed가 activeDoc을 갱신한다 (document 이벤트 없이도)", async () => {
    drive(
      [
        { kind: "file_changed", text: null, path: "aiplc-docs/discovery/envision/prfaq.md", payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("PR/FAQ 작성해줘"));
    expect(result.current.activeDoc).toEqual({
      path: "aiplc-docs/discovery/envision/prfaq.md",
      version: null,
    });
  });

  it("audit.md/aiplc-state.md 쓰기는 activeDoc을 바꾸지 않는다", async () => {
    drive(
      [
        { kind: "file_changed", text: null, path: "aiplc-docs/audit.md", payload: null },
        { kind: "file_changed", text: null, path: "aiplc-docs/aiplc-state.md", payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행"));
    expect(result.current.activeDoc).toBeNull();
  });

  it("질문 문서(*-questions.md) 쓰기는 activeDoc을 바꾸지 않는다", async () => {
    // Questions are delivered via AskUserQuestion, and the form (right panel) is the
    // authoritative screen. If the markdown record of the same questions were also shown in
    // the document panel, the user would see two versions side by side on one screen, and the
    // two never match in the first place: the SDK schema hard-limits to 1-4 questions / 2-4
    // options (CLI 2.1.226: `questions: dt(...).min(1).max(4)`), so a 7-question document
    // required by the rules is split into two rounds of 4+3, and the wording is generated
    // separately. Excluded for the same reason as audit.md/aiplc-state.md — it is a record,
    // not an artifact under review.
    drive(
      [
        { kind: "file_changed", text: null,
          path: "aiplc-docs/discovery/envision/pain-point-questions.md", payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("페인 포인트 알려줘"));
    expect(result.current.activeDoc).toBeNull();
  });

  it("document 이벤트는 version과 함께 activeDoc을 갱신하고, 이후 doc성 file_changed가 최신-승리한다", async () => {
    drive(
      [
        {
          kind: "document", text: null, path: null,
          payload: JSON.stringify({ path: "aiplc-docs/discovery/discovery-document.md", version: "v1", summary: "" }),
        },
        { kind: "file_changed", text: null, path: "aiplc-docs/discovery/envision/prfaq.md", payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("문서 갱신"));
    // The last written document (prfaq) is active — it follows the document the conversation
    // is dealing with now.
    expect(result.current.activeDoc).toEqual({
      path: "aiplc-docs/discovery/envision/prfaq.md",
      version: null,
    });
  });

  it("턴이 끝날 때마다 turnSeq가 증가한다 (에러 종료 포함)", async () => {
    drive([{ kind: "done", text: null, path: null, payload: null }], "streamEvents");
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    expect(result.current.turnSeq).toBe(0);
    act(() => result.current.send("첫 턴"));
    expect(result.current.turnSeq).toBe(1);
    // A turn that ends in an error must also signal a reread (partial artifacts may be synced).
    vi.mocked(sse.streamEvents).mockImplementation((_pid: any, _arg: any, handlers: any) => {
      handlers.onError?.(new Error("boom"));
      return () => {};
    });
    act(() => result.current.send("둘째 턴"));
    expect(result.current.turnSeq).toBe(2);
  });
});

describe("useWorkspaceStream — 중단 이벤트 라우팅 (분기 순서 고정)", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  // applyEvent's status:INTERRUPTED_MARKER branch comes before the trace branch and
  // cuts off with return (useWorkspaceStream.ts). If the order changes or the return is dropped,
  // that marker also piles into the trace and shows up duplicated inside the collapsed
  // "추론 과정" (reasoning) — this pins that regression.
  it("status:interrupted는 interrupted 필드로만 가고 trace에는 쌓이지 않는다 — 평범한 status는 trace로 간다", async () => {
    // getHistory's mockResolvedValue is not cleared by vi.clearAllMocks()
    // (it only clears the call records) — to keep values left by earlier tests from leaking,
    // this describe explicitly pins an empty history.
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "file_read", path: null, payload: null },
        { kind: "status", text: "interrupted", path: null, payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai).toBeDefined();
    if (ai && ai.role === "ai") {
      expect(ai.interrupted).toBe(true);
      expect(ai.trace).toEqual([
        { kind: "status", text: "file_read", path: null, detail: null }]);
    }
  });

  it("한국어 마커를 더 이상 중단으로 보지 않는다", async () => {
    // The backend sends a language-neutral marker, so the Korean string is now an ordinary status
    // trace. Accepting both would count even a tool name in which the agent happened to say
    // '중단됨' (interrupted) as an interruption.
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "중단됨", path: null, payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai).toBeDefined();
    if (ai && ai.role === "ai") {
      expect(ai.interrupted).toBeFalsy();
      expect(ai.trace).toEqual([
        { kind: "status", text: "중단됨", path: null, detail: null }]);
    }
  });
});

describe("턴 개시 실패는 원인을 드러낸다", () => {
  // This is why this defect hid at first: EventSource does not expose status codes, so a 431
  // was mashed into "연결이 끊어졌습니다" (connection lost). Starting (POST) does give a status
  // code, so at least that path must be able to state the cause.
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  it("입력이 너무 길어 거절되면(431) 그 사실을 말한다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    vi.mocked(sse.streamEvents).mockImplementation(
      (_pid: any, _text: any, handlers: any) => {
        handlers.onError?.(new ApiError(431, "too long"));
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("가".repeat(3000)));
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.error).toMatch(/너무 깁니다|too long/i);
    // It must not be mashed into "연결이 끊어졌습니다" — that was this bug's symptom.
    expect(ai && ai.role === "ai" && ai.error).not.toMatch(/연결이 끊어/);
    expect(result.current.streaming).toBe(false);
  });

  it("그 밖의 실패는 기존 연결 오류 문구를 유지한다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    vi.mocked(sse.streamEvents).mockImplementation(
      (_pid: any, _text: any, handlers: any) => {
        handlers.onError?.(new Event("error"));
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("짧음"));
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.error).toMatch(/연결이 끊어/);
  });
});

// ---- Reattaching to a dropped turn ----
// A turn is a server job (backend aipds/turn_job.py) — even if the screen drops, the turn keeps
// running, and reattaching with the same id resumes after the last seq received. Turns take
// 2.5-5.6 minutes and screensaver defaults are 5-10 minutes, so this path is routine.
const msg = (text: string): AgentEvent =>
  ({ kind: "message", text, path: null, payload: null });
const done: AgentEvent = { kind: "done", text: null, path: null, payload: null };
const running = (turnId = "t1") =>
  ({ turn_id: turnId, kind: "message" as const, state: "running" as const, last_seq: 1 });

function sendThatDrops(turnId = "t1") {
  vi.mocked(sse.streamEvents).mockImplementation(
    (_pid: any, _text: any, handlers: any) => {
      handlers.onCreated?.({ turn_id: turnId });
      handlers.onEvent(msg("끊기기 전"), 1);
      handlers.onError?.(new Event("error"));   // sleep
      handlers.onDone();
      return () => {};
    },
  );
}

describe("useWorkspaceStream — 끊긴 턴 재접속", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    noRunningTurn();
    vi.mocked(client.getHistory).mockResolvedValue([]);
    vi.useFakeTimers();
  });
  afterEach(() => { vi.useRealTimers(); });

  it("끊기면 같은 턴에 마지막으로 받은 seq부터 다시 붙는다", async () => {
    sendThatDrops();
    vi.mocked(client.getTurn).mockResolvedValue(null);
    vi.mocked(sse.watchTurn).mockImplementation(
      (_pid: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(msg(" 그리고 이어서"), 2);
        handlers.onEvent(done, 3);
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    vi.mocked(client.getTurn).mockResolvedValue(running());
    act(() => result.current.send("보냄"));
    // Right after the drop it is still in progress — quietly reattaching.
    expect(result.current.streaming).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0]); });

    expect(sse.watchTurn).toHaveBeenCalledWith("p1", "t1", 1, expect.anything());
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.text).toBe("끊기기 전 그리고 이어서");
    expect(ai && ai.role === "ai" && ai.error).toBeNull();
    expect(result.current.streaming).toBe(false);
  });

  it("턴 id를 받기 전에 끊기면 붙을 곳이 없다 — 연결 오류 문구로 끝난다", async () => {
    vi.useRealTimers();
    vi.mocked(sse.streamEvents).mockImplementation(
      (_pid: any, _text: any, handlers: any) => {
        handlers.onError?.(new Event("error"));
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("보냄"));

    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.error).toMatch(/연결이 끊어/);
    expect(result.current.streaming).toBe(false);
    expect(sse.watchTurn).not.toHaveBeenCalled();
  });

  it("개시 실패(431)에는 다시 붙지 않는다 — 턴이 시작되지 않았다", async () => {
    vi.useRealTimers();
    vi.mocked(sse.streamEvents).mockImplementation(
      (_pid: any, _text: any, handlers: any) => {
        handlers.onError?.(new ApiError(431, "too long"));
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("가".repeat(3000)));

    expect(sse.watchTurn).not.toHaveBeenCalled();
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.error).toMatch(/너무 깁니다|too long/i);
  });

  it("다시 붙는 것이 계속 실패하면 정해진 횟수 뒤 포기한다 — 입력이 영구히 잠기지 않는다", async () => {
    sendThatDrops();
    vi.mocked(sse.watchTurn).mockImplementation(
      (_pid: any, _turn: any, _after: any, handlers: any) => {
        handlers.onError?.(new Event("error"));
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    vi.mocked(client.getTurn).mockResolvedValue(running());
    act(() => result.current.send("보냄"));
    for (const delay of RECONNECT_DELAYS_MS) {
      await act(async () => { await vi.advanceTimersByTimeAsync(delay); });
    }

    expect(sse.watchTurn).toHaveBeenCalledTimes(RECONNECT_DELAYS_MS.length);
    expect(result.current.streaming).toBe(false);
    const ai = result.current.items.find((i) => i.role === "ai");
    // The turn may still be running on the server — recommend a refresh, not "다시 시도" (retry).
    expect(ai && ai.role === "ai" && ai.error).toMatch(/새로고침/);
  });

  it("그 턴이 서버에서 이미 사라졌으면 다시 붙지 않는다", async () => {
    sendThatDrops();
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("보냄"));
    await act(async () => { await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0]); });

    expect(sse.watchTurn).not.toHaveBeenCalled();
    expect(result.current.streaming).toBe(false);
  });

  it("다른 턴이 돌고 있어 거절되면(409) 그 턴을 보여 준다", async () => {
    vi.useRealTimers();
    vi.mocked(sse.streamEvents).mockImplementation(
      (_pid: any, _text: any, handlers: any) => {
        handlers.onError?.(new ApiError(409, "turn_in_progress", "t-run"));
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("또"));

    const ais = result.current.items.filter((i) => i.role === "ai");
    expect(ais[0].role === "ai" && ais[0].error).toMatch(/보내지 않았습니다/);
    expect(sse.watchTurn).toHaveBeenCalledWith("p1", "t-run", 0, expect.anything());
    expect(ais).toHaveLength(2);
  });
});

describe("useWorkspaceStream — 화면이 열릴 때 도는 턴", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(client.getHistory).mockResolvedValue([]);
  });

  it("도는 턴이 있으면 처음부터 받아 말풍선을 다시 채우고 입력을 잠근다", async () => {
    vi.mocked(client.getTurn).mockResolvedValue(running("t-live"));
    vi.mocked(sse.watchTurn).mockImplementation(
      (_pid: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent(msg("이미 쓴 부분"), 1);
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    // Text is appended per frame (useTextDeltas) — the turn is still running, so no terminal
    // event flushes it.
    await act(nextFrame);

    expect(sse.watchTurn).toHaveBeenCalledWith("p1", "t-live", 0, expect.anything());
    expect(result.current.streaming).toBe(true);
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.text).toBe("이미 쓴 부분");
  });

  it("재시작으로 끊긴 턴이면 그 사실을 대화 끝에 남긴다", async () => {
    vi.mocked(client.getTurn).mockResolvedValue(
      { turn_id: "t-old", kind: "message", state: "interrupted", last_seq: 0 });
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});

    expect(sse.watchTurn).not.toHaveBeenCalled();
    expect(result.current.streaming).toBe(false);
    const last = result.current.items[result.current.items.length - 1];
    expect(last.role === "ai" && last.error).toMatch(/다시 시작되어/);
  });

  it("끝난 턴에는 붙지 않는다 — 대화는 히스토리가 복원한다", async () => {
    vi.mocked(client.getTurn).mockResolvedValue(
      { turn_id: "t-done", kind: "message", state: "done", last_seq: 9 });
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    expect(sse.watchTurn).not.toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
  });

  it("백엔드의 기계 문구를 화면 문구로 옮긴다", async () => {
    vi.mocked(client.getTurn).mockResolvedValue(running("t-live"));
    vi.mocked(sse.watchTurn).mockImplementation(
      (_pid: any, _turn: any, _after: any, handlers: any) => {
        handlers.onEvent({ kind: "error", text: "turn interrupted", path: null, payload: null }, 1);
        handlers.onDone();
        return () => {};
      },
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai && ai.role === "ai" && ai.error).toMatch(/다시 시작되어/);
    expect(ai && ai.role === "ai" && ai.error).not.toMatch(/turn interrupted/);
  });
});

describe("답변 말풍선은 서버가 만든 텍스트를 그대로 쓴다", () => {
  // **Why the server (2026-08-21).** While this discrimination lived only in the frontend, the
  // bubble the user saw was built only inside the browser and never went to the server. The
  // server recorded a different sentence of its own making in the transcript, and after a refresh
  // the conversation showed machine text — measured: 13 of 16 user utterances in one
  // project. If the render is a single backend copy, they cannot diverge
  // (backend/aipds/answer_summary.py).
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  it("파일 라운드의 말풍선이 서버 응답의 summary다", async () => {
    createdSummary = "Q1. 어떤 방식이 좋습니까?\n→ B. 플랫폼(Platform)";
    drive(
      [
        { kind: "questions", text: null, path: null, payload: FILE_QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("시작"));
    drive([{ kind: "done", text: null, path: null, payload: null }], "streamFileAnswers");

    await act(async () => { result.current.submitAnswers({ "1": "B" }); });

    const bubbles = result.current.items.filter((i) => i.role === "user");
    expect(bubbles[bubbles.length - 1]!.text).toBe(
      "Q1. 어떤 방식이 좋습니까?\n→ B. 플랫폼(Platform)",
    );
  });
});

describe("도구 경로(AskUserQuestion 탈출로)의 말풍선은 읽을 수 있어야 한다", () => {
  // File rounds use the text the server built. On the path that resumes a parked turn (the
  // escape hatch with `AIPDS_FILE_QUESTIONS` turned off), the server provides no `summary` —
  // that route does not have the question file in hand — so the frontend renderer is still
  // used. Falling back to the marker ("답변 제출") would make only that path unreadable, and
  // since the restore shows the real answer via the `answer_store` join, live and restore
  // would diverge **in opposite directions**.
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  it("summary가 없으면 프론트 렌더러로 문항과 답변을 그린다", async () => {
    createdSummary = undefined as unknown as string;
    drive(
      [
        { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("시작"));
    drive([{ kind: "done", text: null, path: null, payload: null }], "streamAnswers");

    await act(async () => { result.current.submitAnswers({ "1": "A" }); });

    const bubbles = result.current.items.filter((i) => i.role === "user");
    const text = bubbles[bubbles.length - 1]!.text;
    // Question 1 of QUESTIONS_PAYLOAD is "누구?" (who?) and option A is "PM".
    expect(text).toContain("누구?");
    expect(text).toContain("PM");
    expect(text).not.toBe("답변 제출");
  });
});

describe("useWorkspaceStream — 사고 구간 신호", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  // The backend sends the start and end of a thinking block as status markers (claude_driver's
  // THINKING_MARKER / THINKING_DONE_MARKER). Thinking **text** does not exist on this path
  // (measured 0 characters on Bedrock), so what we display is the interval.
  it("status:thinking은 activity를 사고로 두고 트레이스에 사고 줄을 남긴다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [{ kind: "status", text: "thinking", path: null, payload: null }],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    const ai = result.current.items.find((i) => i.role === "ai");
    expect(ai).toBeDefined();
    if (ai && ai.role === "ai") {
      expect(ai.activity).toEqual({ kind: "thinking" });
      // If the marker string leaked as an ordinary status line, "thinking" would show up as is in
      // the collapsed trace — the same kind of regression as the interrupted marker.
      expect(ai.trace).toEqual([
        { kind: "thinking", text: null, path: null, detail: null }]);
    }
  });

  it("status:thinking-done은 줄을 더 만들지 않는다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "thinking", path: null, payload: null },
        { kind: "status", text: "thinking-done", path: null, payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    const ai = result.current.items.find((i) => i.role === "ai");
    if (ai && ai.role === "ai") {
      expect(ai.trace).toEqual([
        { kind: "thinking", text: null, path: null, detail: null }]);
    }
  });
});

describe("useWorkspaceStream — activity는 마지막에 온 이벤트가 확정한다", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  // The pinned line shows the single "most recent thing that happened". Taking the last tool
  // from the trace would leave that tool's name in place even after the tool finished and the
  // answer was flowing, lying "자료를 확인하고 있어요" (checking the materials) — overwriting in
  // event order is what prevents that drift. What this test pins is that order.
  const liveAi = (result: { current: { items: unknown[] } }) =>
    (result.current.items as { role: string }[]).find((i) => i.role === "ai") as
      { role: "ai"; activity?: unknown } | undefined;

  it("도구 status 뒤에 message가 오면 activity는 작성으로 넘어간다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "Read", path: null, payload: null },
        { kind: "message", text: "정리했습니다", path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    expect(liveAi(result)?.activity).toEqual({ kind: "writing" });
  });

  it("묶인 텍스트는 뒤따른 도구보다 먼저 붙는다 — 프레임을 기다리지 않아도", async () => {
    // Text deltas are appended once per frame (useTextDeltas). If a tool event arrives in
    // between, the preceding text must be applied first — if late-appended text set activity
    // back to "작성" (writing), the pinned line would lie while the tool is running.
    vi.mocked(client.getHistory).mockResolvedValue([]);
    vi.mocked(sse.streamEvents).mockImplementation((_p: any, _t: any, handlers: any) => {
      handlers.onEvent({ kind: "message", text: "자료를 ", path: null, payload: null });
      handlers.onEvent({ kind: "message", text: "봅니다", path: null, payload: null });
      handlers.onEvent({ kind: "status", text: "Read", path: null, payload: null });
      return () => {};   // the turn is still running — no terminal event flushes it in its place
    });
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    const ai = liveAi(result) as { text?: string; activity?: unknown } | undefined;
    expect(ai?.text).toBe("자료를 봅니다");
    expect(ai?.activity).toEqual({ kind: "tool", tool: "Read", detail: null });
  });

  it("도는 턴의 텍스트 델타는 다음 프레임에 한꺼번에 붙는다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    vi.mocked(sse.streamEvents).mockImplementation((_p: any, _t: any, handlers: any) => {
      for (const piece of ["한 ", "번에 ", "붙는다"]) {
        handlers.onEvent({ kind: "message", text: piece, path: null, payload: null });
      }
      return () => {};
    });
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    expect((liveAi(result) as { text?: string }).text).toBe("");
    await act(nextFrame);
    const ai = liveAi(result) as { text?: string; activity?: unknown };
    expect(ai.text).toBe("한 번에 붙는다");
    expect(ai.activity).toEqual({ kind: "writing" });
  });

  it("도구 status는 이름과 대상을 함께 싣는다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [{ kind: "status", text: "Read", path: null,
         payload: JSON.stringify({ detail: "aiplc-docs/x.md" }) }],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    expect(liveAi(result)?.activity).toEqual(
      { kind: "tool", tool: "Read", detail: "aiplc-docs/x.md" });
  });

  it("file_changed도 activity를 덮는다 — 마지막 일이 그것이면 그것을 보여준다", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "Write", path: null, payload: null },
        { kind: "file_changed", text: null, path: "aiplc-docs/audit.md", payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행 중"));
    expect(liveAi(result)?.activity).toEqual(
      { kind: "file", path: "aiplc-docs/audit.md" });
  });
});

// ---- Progress of the file being written (`draft` event, backend agent/tool_input_draft.py)
// ---- Measured 2026-10-07: generating a single document took 1-2 minutes, with no signal on
// screen in the meantime.
describe("useWorkspaceStream — 쓰고 있는 파일", () => {
  beforeEach(() => { vi.clearAllMocks(); noRunningTurn(); });

  // Stream events one at a time with the turn held open — `drive` calls onDone at the end.
  function openTurn() {
    let handlers: any = null;
    vi.mocked(sse.streamEvents).mockImplementation((...args: any[]) => {
      handlers = args[args.length - 1];
      return () => {};
    });
    return { emit: (ev: AgentEvent) => act(() => handlers.onEvent(ev)) };
  }
  const draftEv = (path: string | null, payload: object): AgentEvent =>
    ({ kind: "draft", text: null, path, payload: JSON.stringify(payload) });
  const DOC = "aiplc-docs/discovery/discovery-document.md";

  it("진행 줄이 무엇을 몇 자째 쓰는지 따라간다", async () => {
    const turn = openTurn();
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("Part 1 써줘"));
    const activity = () => (result.current.items.find((it) => it.role === "ai") as any).activity;

    turn.emit(draftEv(null, { id: "t1", tool: "Write", state: "writing", chars: 0 }));
    expect(activity()).toEqual({ kind: "drafting", path: null, chars: 0 });

    turn.emit(draftEv(DOC, { id: "t1", tool: "Write", state: "writing", chars: 1234 }));
    expect(activity()).toEqual({ kind: "drafting", path: DOC, chars: 1234 });
  });

  it("문서 패널을 끌어오지 않는다 — 문서는 다 쓰인 뒤 file_changed로 나타난다", async () => {
    const turn = openTurn();
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    act(() => result.current.send("진행"));

    turn.emit(draftEv(DOC, { id: "t2", tool: "Write", state: "writing", chars: 40 }));
    expect(result.current.activeDoc).toBeNull();
  });
});

describe("useWorkspaceStream — a turn another screen started", () => {
  // Observed (2026-10-20): after an answer change on the handoff tab moved to the workspace, the AI replied to nothing
  // until a refresh restored the person's side from the transcript. The turn now carries its input.
  const input = "앞서 답한 질문의 답을 바꿨습니다.\n\n이전 답:\nQ10\n→ A\n\n바꾼 답:\nQ10\n→ B";
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(client.getTurn).mockResolvedValue({ ...running(), input });
    vi.mocked(sse.watchTurn).mockImplementation((_pid: any, _turn: any, _after: any, handlers: any) => {
      handlers.onEvent(msg("영향을 평가합니다"), 2);
      handlers.onEvent(done, 3);
      handlers.onDone();
      return () => {};
    });
  });

  it("shows the person's input before the reply it attaches to", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([]);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    expect(result.current.items.map((i) => i.role)).toEqual(["user", "ai"]);
    const user = result.current.items[0];
    expect(user.role === "user" && user.text).toBe(input);
  });

  it("keeps one bubble when history already ends with that input", async () => {
    vi.mocked(client.getHistory).mockResolvedValue([{ role: "user", text: "이전 채팅" }, { role: "user", text: input }] as any);
    const { result } = renderHook(() => useWorkspaceStream("p1"));
    await act(async () => {});
    const users = result.current.items.filter((i) => i.role === "user" && i.text === input);
    expect(users).toHaveLength(1);
  });
});
