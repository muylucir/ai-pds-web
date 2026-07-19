// frontend/lib/useWorkspaceStream.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspaceStream } from "./useWorkspaceStream";
import * as sse from "@/lib/api/sse";
import * as client from "@/lib/api/client";
import type { AgentEvent } from "@/lib/api/types";

vi.mock("@/lib/api/sse");
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig()),
  getPending: vi.fn().mockResolvedValue(null),
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

  it("questions event fills pendingQuestions; stage event appends stages", () => {
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
    act(() => result.current.send("시작"));
    expect(result.current.pendingQuestions?.interrupt_id).toBe("i-1");
    expect(result.current.stages).toEqual([{ stage: "Envision", status: "in_progress", summary: "" }]);
  });

  it("submitAnswers streams via streamAnswers and clears pendingQuestions", () => {
    drive(
      [
        { kind: "questions", text: null, path: null, payload: QUESTIONS_PAYLOAD },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
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

  it("malformed payload does not crash the stream (fallback: chat keeps going)", () => {
    drive(
      [
        { kind: "questions", text: null, path: null, payload: "not-json{" },
        { kind: "message", text: "계속", path: null, payload: null },
        { kind: "done", text: null, path: null, payload: null },
      ],
      "streamEvents",
    );
    const { result } = renderHook(() => useWorkspaceStream("p1"));
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
});
