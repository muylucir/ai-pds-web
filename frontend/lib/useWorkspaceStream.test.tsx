// frontend/lib/useWorkspaceStream.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspaceStream } from "./useWorkspaceStream";
import * as sse from "@/lib/api/sse";
import * as client from "@/lib/api/client";
import * as sessionRecovery from "@/lib/auth/sessionRecovery";
import type { AgentEvent, HistoryItem } from "@/lib/api/types";

vi.mock("@/lib/api/sse");
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig()),
  getPending: vi.fn().mockResolvedValue(null),
  getHistory: vi.fn().mockResolvedValue([]),
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

  // onError의 유일한 실제 배선 지점 — 이 콜이 지워지거나 인자가 바뀌면 사용자는
  // 만료된 세션에서도 로그인으로 보내지지 않는다. navigate는 undefined로 넘겨
  // (기본 전체 페이지 이동을 쓰게) 두고, currentPath는 현재 pathname이어야 한다.
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
      { kind: "status", text: "file_read", path: null },
      { kind: "file_changed", text: null, path: "aiplc-docs/audit.md" },
    ]);
  }
});

describe("useWorkspaceStream — activeDoc/turnSeq (문서 패널 싱크, ui-bug2)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("doc성 file_changed가 activeDoc을 갱신한다 (submit_document 없이도)", async () => {
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

  it("document 이벤트(submit_document)는 version과 함께 activeDoc을 갱신하고, 이후 doc성 file_changed가 최신-승리한다", async () => {
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
    // 마지막에 쓴 문서(prfaq)가 활성 — 대화가 지금 다루는 문서를 따른다.
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
    // 에러로 끝나는 턴도 재읽기 신호를 줘야 한다 (부분 산출물 동기화 가능).
    vi.mocked(sse.streamEvents).mockImplementation((_pid: any, _arg: any, handlers: any) => {
      handlers.onError?.(new Error("boom"));
      return () => {};
    });
    act(() => result.current.send("둘째 턴"));
    expect(result.current.turnSeq).toBe(2);
  });
});

describe("useWorkspaceStream — 중단 이벤트 라우팅 (분기 순서 고정)", () => {
  beforeEach(() => vi.clearAllMocks());

  // applyEvent의 status:"중단됨" 분기는 trace 분기보다 앞에 있고 return으로
  // 끊긴다(useWorkspaceStream.ts). 순서가 바뀌거나 return이 빠지면 "중단됨"이
  // trace에도 쌓여 접힌 "추론 과정" 안에 중복 노출된다 — 그 회귀를 여기서 고정한다.
  it("status:중단됨은 interrupted 필드로만 가고 trace에는 쌓이지 않는다 — 평범한 status는 trace로 간다", async () => {
    // getHistory의 mockResolvedValue는 vi.clearAllMocks()로 지워지지 않는다
    // (호출 기록만 지운다) — 앞선 테스트가 남긴 값이 새는 것을 막기 위해
    // 이 describe에서 명시적으로 빈 히스토리를 고정한다.
    vi.mocked(client.getHistory).mockResolvedValue([]);
    drive(
      [
        { kind: "status", text: "file_read", path: null, payload: null },
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
      expect(ai.interrupted).toBe(true);
      expect(ai.trace).toEqual([{ kind: "status", text: "file_read", path: null }]);
    }
  });
});
