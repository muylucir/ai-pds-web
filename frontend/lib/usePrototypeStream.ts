// frontend/lib/usePrototypeStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamPrototypeEvents, submitPrototypeAnswers, interruptSession } from "@/lib/api/prototypes";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";
import type { QuestionsPayload } from "@/lib/api/types";
import type { UserItem, AiItem, TraceEntry } from "@/lib/useTurnStream";

// A NEW hook modeled on useWorkspaceStream (the workspace's CURRENT stream
// pattern — useTurnStream itself is retired-canvas-only) for the prototype
// build chat panel. Simpler than useWorkspaceStream: no stage/document/
// history/activeDoc/turnSeq branches — a build session has no multi-document
// sidebar and Task 7's routes expose no history-restore endpoint for
// prototype sessions, so `items` always starts empty on mount.
export type { UserItem, AiItem } from "@/lib/useTurnStream";
export type ChatItem = UserItem | AiItem;

let counter = 0;
const nextId = () => `proto-item-${counter++}`;

// Malformed JSON in a structured payload must not stop the stream — parsing
// fails closed to `null` (same fail-closed contract as
// useWorkspaceStream.ts's safeParse).
function safeParse<T>(payload: string | null): T | null {
  if (!payload) return null;
  try {
    return JSON.parse(payload) as T;
  } catch {
    return null;
  }
}

export interface PrototypeStream {
  items: ChatItem[];
  streaming: boolean;
  pendingQuestions: QuestionsPayload | null;
  changedPaths: string[];
  startBuild: () => void;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => Promise<void>;
  interrupt: () => Promise<void>;
}

export function usePrototypeStream(projectId: string, slug: string): PrototypeStream {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const stopRef = useRef<null | (() => void)>(null);
  // The AI bubble id for whichever turn currently owns the open SSE stream —
  // a "questions" event does NOT close it (the harness keeps the turn open
  // across the answers roundtrip, routes.py's submit_answers docstring), so
  // submitAnswers needs this to surface a 409 (no pending question to
  // resolve) on the SAME bubble the question came from.
  const currentAiIdRef = useRef<string | null>(null);

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, []);

  // Shared per-frame projection: folds message text into the AI bubble,
  // status/file_changed into its trace + the changedPaths list, and mirrors
  // "questions" into pendingQuestions WITHOUT touching streaming — the turn
  // stays open on the server until /answers resolves it.
  const applyEvent = useCallback(
    (aiId: string, ev: AgentEvent) => {
      if (ev.kind === "file_changed" && ev.path) {
        setChangedPaths((prev) => (prev.includes(ev.path as string) ? prev : [...prev, ev.path as string]));
      }
      if (ev.kind === "questions") {
        const parsed = safeParse<QuestionsPayload>(ev.payload);
        if (parsed) setPendingQuestions(parsed);
        return;
      }
      if (ev.kind === "error") {
        // A pending question can never be answered once the turn itself has
        // errored out — mirror the harness's own interrupt-clears-pending
        // fix (sdk_driver.py's interrupt()) on this side of the same race,
        // so the form doesn't linger unanswerable.
        setPendingQuestions(null);
      }
      patchAi(aiId, (it) => {
        if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
        if (ev.kind === "status" || ev.kind === "file_changed") {
          const trace: TraceEntry = { kind: ev.kind, text: ev.text, path: ev.path };
          return { ...it, trace: [...it.trace, trace] };
        }
        if (ev.kind === "error") return { ...it, error: ev.text ?? "빌드 중 오류가 발생했습니다." };
        return it; // "done" is handled by onDone
      });
    },
    [patchAi],
  );

  const runTurn = useCallback(
    (
      opener: (handlers: {
        onEvent: (ev: AgentEvent) => void;
        onDone: () => void;
        onError?: (err: unknown) => void;
      }) => () => void,
      aiId: string,
    ) => {
      currentAiIdRef.current = aiId;
      setStreaming(true);
      // A stream can finish SYNCHRONOUSLY inside `opener(...)` (e.g. a test
      // double that drives every frame before returning) — before the
      // assignment below runs. Track that with a local flag rather than
      // relying on assignment order (same guard as useWorkspaceStream).
      let finished = false;
      const finish = () => {
        finished = true;
        setStreaming(false);
        stopRef.current = null;
        currentAiIdRef.current = null;
      };
      const stop = opener({
        onEvent: (ev) => applyEvent(aiId, ev),
        onDone: () => {
          patchAi(aiId, (it) => ({ ...it, streaming: false }));
          finish();
        },
        onError: () => {
          // 401(토큰 만료)과 네트워크 끊김을 EventSource가 구분해주지 않으므로
          // 세션을 확인해 만료면 로그인으로 보낸다. 살아 있으면 아래 메시지가 맞다.
          void redirectIfSessionExpired(undefined, window.location.pathname);
          patchAi(aiId, (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? "연결이 끊어졌습니다. 다시 시도해 주세요.",
          }));
          setPendingQuestions(null); // same defensive clear as the error-kind path above
          finish();
        },
      });
      if (finished) stop();
      else stopRef.current = stop;
    },
    [applyEvent, patchAi],
  );

  // The auto first-build turn: opens the events stream with the "__first__"
  // sentinel (routes.py substitutes session.first_prompt() server-side) and
  // adds ONLY an AI bubble — there is no user-authored text for this turn.
  const startBuild = useCallback(() => {
    if (stopRef.current) return;
    const aiId = nextId();
    setItems((prev) => [...prev, { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null }]);
    runTurn((handlers) => streamPrototypeEvents(projectId, slug, "__first__", handlers), aiId);
  }, [projectId, slug, runTurn]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard on the live-stream ref (not `streaming` state) so a stale
      // closure can't slip a concurrent send past a not-yet-flushed setState.
      if (trimmed === "" || stopRef.current) return;
      const aiId = nextId();
      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: trimmed },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      runTurn((handlers) => streamPrototypeEvents(projectId, slug, trimmed, handlers), aiId);
    },
    [projectId, slug, runTurn],
  );

  const submitAnswers = useCallback(
    async (answers: Record<string, string>) => {
      const ok = await submitPrototypeAnswers(projectId, slug, answers);
      if (ok) {
        // Events keep flowing on the SAME open stream — no new stream to open.
        setPendingQuestions(null);
        return;
      }
      // 409: no pending question to resolve server-side. Surface it without
      // discarding the form (the user may still want to retry), and log for
      // diagnosis.
      console.warn("submitPrototypeAnswers: no pending question to resolve (409)");
      const aiId = currentAiIdRef.current;
      if (aiId) {
        patchAi(aiId, (it) => ({
          ...it,
          error: it.error ?? "답변을 제출하지 못했습니다. 다시 시도해 주세요.",
        }));
      }
    },
    [projectId, slug, patchAi],
  );

  const interrupt = useCallback(async () => {
    await interruptSession(projectId, slug);
    // No local state change here — the harness reports the aborted turn
    // (a "status" event with text "interrupted", then "done"/"error") on the
    // SAME open SSE stream; applyEvent/runTurn's onDone/onError handle it
    // exactly like any other turn end.
  }, [projectId, slug]);

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return {
    items,
    streaming,
    pendingQuestions,
    changedPaths,
    startBuild,
    send,
    submitAnswers,
    interrupt,
  };
}
