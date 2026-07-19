// frontend/lib/useWorkspaceStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamEvents, streamAnswers } from "@/lib/api/sse";
import { getPending } from "@/lib/api/client";
import type { AgentEvent, QuestionsPayload, StagePayload, DocumentPayload } from "@/lib/api/types";
import type { UserItem, AiItem, TraceEntry } from "@/lib/useTurnStream";

// This is a NEW hook cloned+extended from useTurnStream for the Task 11
// three-pane workspace screen. useTurnStream itself is left untouched — the
// canvas/questions pages still use it until Task 11 removes them. Unlike
// useTurnStream, this hook has no CardItem derivation: cards were a
// file-contract workaround for the old flow, and the workspace consumes the
// new structured events (questions/stage/document) directly instead — so this
// hook's own ChatItem union is user/ai only, no CardItem.
export type { UserItem, AiItem } from "@/lib/useTurnStream";
export type ChatItem = UserItem | AiItem;

let counter = 0;
const nextId = () => `wf-item-${counter++}`;

// Malformed JSON in a structured payload must not stop the stream — parsing
// fails closed to `null` and the event is otherwise ignored (spec §4's
// fallback principle: progress is never blocked by one bad frame).
function safeParse<T>(payload: string | null): T | null {
  if (!payload) return null;
  try {
    return JSON.parse(payload) as T;
  } catch {
    return null;
  }
}

export interface WorkspaceStream {
  items: ChatItem[];
  streaming: boolean;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => void;
  pendingQuestions: QuestionsPayload | null;
  stages: StagePayload[];
  lastDocument: DocumentPayload | null;
  changedPaths: string[];
}

export function useWorkspaceStream(projectId: string, initial: ChatItem[] = []): WorkspaceStream {
  const [items, setItems] = useState<ChatItem[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [stages, setStages] = useState<StagePayload[]>([]);
  const [lastDocument, setLastDocument] = useState<DocumentPayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const stopRef = useRef<null | (() => void)>(null);

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, []);

  // Shared per-frame projection for both streamEvents and streamAnswers: folds
  // message text into the AI bubble, files status/file_changed into its
  // trace, and mirrors the three structured kinds into the workspace's
  // sidebar/panel state (stage history, latest document, pending questions).
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
      if (ev.kind === "stage") {
        const parsed = safeParse<StagePayload>(ev.payload);
        if (parsed) setStages((prev) => [...prev, parsed]);
        return;
      }
      if (ev.kind === "document") {
        const parsed = safeParse<DocumentPayload>(ev.payload);
        if (parsed) setLastDocument(parsed);
        return;
      }
      patchAi(aiId, (it) => {
        if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
        if (ev.kind === "status" || ev.kind === "file_changed") {
          const trace: TraceEntry = { kind: ev.kind, text: ev.text, path: ev.path };
          return { ...it, trace: [...it.trace, trace] };
        }
        if (ev.kind === "error") return { ...it, error: ev.text ?? "턴 처리 중 오류가 발생했습니다." };
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
      setStreaming(true);
      // A stream can finish SYNCHRONOUSLY inside `opener(...)` (e.g. a test
      // double that drives all frames before returning) — before the
      // assignment below would run. Track that with a local flag instead of
      // relying on assignment order, so `finish()`'s `stopRef.current = null`
      // never gets clobbered by the stale closer being stored afterwards.
      let finished = false;
      const finish = () => {
        finished = true;
        setStreaming(false);
        stopRef.current = null;
      };
      const stop = opener({
        onEvent: (ev) => applyEvent(aiId, ev),
        onDone: () => {
          patchAi(aiId, (it) => ({ ...it, streaming: false }));
          finish();
        },
        onError: () => {
          patchAi(aiId, (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? "연결이 끊어졌습니다. 다시 시도해 주세요.",
          }));
          finish();
        },
      });
      if (finished) stop();
      else stopRef.current = stop;
    },
    [applyEvent, patchAi],
  );

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard on the live-stream ref (not the `streaming` state) so a stale
      // closure can't slip a concurrent send past a not-yet-flushed setState.
      if (trimmed === "" || stopRef.current) return;

      const aiId = nextId();
      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: trimmed },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      runTurn((handlers) => streamEvents(projectId, trimmed, handlers), aiId);
    },
    [projectId, runTurn],
  );

  const submitAnswers = useCallback(
    (answers: Record<string, string>) => {
      if (stopRef.current) return;
      setPendingQuestions(null);

      const aiId = nextId();
      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: "답변 제출" },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      runTurn((handlers) => streamAnswers(projectId, answers, handlers), aiId);
    },
    [projectId, runTurn],
  );

  // Restore an in-flight question interrupt (e.g. after a page refresh) from
  // GET /pending — same payload shape/parsing as a live "questions" event.
  useEffect(() => {
    let cancelled = false;
    getPending(projectId).then((pending) => {
      if (cancelled) return;
      const parsed = safeParse<QuestionsPayload>(pending);
      if (parsed) setPendingQuestions(parsed);
    });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return {
    items,
    streaming,
    send,
    submitAnswers,
    pendingQuestions,
    stages,
    lastDocument,
    changedPaths,
  };
}
