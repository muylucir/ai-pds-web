// frontend/lib/useWorkspaceStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamEvents, streamAnswers } from "@/lib/api/sse";
import { getPending, getHistory } from "@/lib/api/client";
import type { AgentEvent, HistoryItem, QuestionsPayload, StagePayload, DocumentPayload } from "@/lib/api/types";
import type { UserItem, AiItem, TraceEntry } from "@/lib/useTurnStream";

// This is a NEW hook cloned+extended from useTurnStream for the Task 11
// three-pane workspace screen. useTurnStream itself is left untouched — the
// canvas/questions pages still use it until Task 11 removes them. Unlike
// useTurnStream, this hook has no CardItem derivation: cards were a
// file-contract workaround for the old flow, and the workspace consumes the
// new structured events (questions/stage/document) directly instead — so this
// hook's own ChatItem union is user/ai plus a HISTORY-ONLY card marker (below)
// restored from GET /history, never re-derived from live file_changed paths.
export type { UserItem, AiItem } from "@/lib/useTurnStream";
// A questions file presented in a PAST turn (Task 5's history restore) —
// deliberately NOT the same shape as useTurnStream's QuestionsCardItem: this
// is a static summary marker (role "history-card"), never rendered as the
// live interactive QuestionCardSlot form.
export interface HistoryCardItem {
  id: string;
  role: "history-card";
  name: string | null;
}
export type ChatItem = UserItem | AiItem | HistoryCardItem;

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
  historyLoading: boolean;
  // 문서 패널이 따라가야 할 "지금 대화 중인 문서" — submit_document뿐 아니라
  // doc성 file_changed(아래 isDocPath)도 최신-승리로 추적한다. version은
  // submit_document에서 온 경우에만 채워진다 (ui-bug2 싱크 수정).
  activeDoc: { path: string; version: string | null } | null;
  // 턴이 끝날 때마다 증가 — 패널이 이 키로 문서를 다시 읽는다. 턴 도중
  // 도착한 document 이벤트 시점에는 VM→S3 동기화 전이라 읽기가 빈 내용/404가
  // 될 수 있고, 그대로 두면 재읽기가 영영 없다 (ui-bug2의 "비어 있음" 수정).
  turnSeq: number;
}

// 문서 패널이 따라갈 가치가 있는 산출물 경로인가 — aiplc-docs/ 아래 .md 중
// 기록성 파일(audit/state)은 제외.
function isDocPath(path: string): boolean {
  return (
    path.startsWith("aiplc-docs/") &&
    path.endsWith(".md") &&
    !path.endsWith("/audit.md") &&
    path !== "aiplc-docs/audit.md" &&
    !path.endsWith("/aiplc-state.md") &&
    path !== "aiplc-docs/aiplc-state.md"
  );
}

function historyItemToChatItem(it: HistoryItem): ChatItem {
  if (it.role === "card") return { id: nextId(), role: "history-card", name: it.name };
  if (it.role === "user") return { id: nextId(), role: "user", text: it.text ?? "" };
  return {
    id: nextId(),
    role: "ai",
    text: it.text ?? "",
    // 복원된 도구 트레이스 — 라이브 턴의 status/file_changed 이벤트와 같은
    // shape이라 AiMessage의 "추론 과정" 아코디언이 그대로 렌더한다.
    trace: (it.trace ?? []).map((t) => ({ kind: t.kind, text: t.text, path: t.path })),
    streaming: false,
    error: null,
  };
}

export function useWorkspaceStream(projectId: string, initial: ChatItem[] = []): WorkspaceStream {
  const [items, setItems] = useState<ChatItem[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [stages, setStages] = useState<StagePayload[]>([]);
  const [lastDocument, setLastDocument] = useState<DocumentPayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [activeDoc, setActiveDoc] = useState<{ path: string; version: string | null } | null>(null);
  const [turnSeq, setTurnSeq] = useState(0);
  const stopRef = useRef<null | (() => void)>(null);
  // Set the instant a live turn (send/submitAnswers) starts. GET /history can
  // still be in flight when that happens — history strictly precedes the live
  // turn chronologically, so the history effect below must PREPEND its
  // restored items to whatever's already in `items` instead of replacing the
  // array outright, or a slow /history response would silently wipe the turn
  // the user just started.
  const liveTurnStartedRef = useRef(false);

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
        // 에이전트는 대부분의 문서를 submit_document 없이 file_write로만
        // 만든다(실측: prfaq.md 등) — doc성 쓰기도 활성 문서로 추적해야
        // 패널이 대화를 따라간다 (ui-bug2).
        if (isDocPath(ev.path)) setActiveDoc({ path: ev.path, version: null });
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
        if (parsed) {
          setLastDocument(parsed);
          setActiveDoc({ path: parsed.path, version: parsed.version });
        }
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
        // 턴 종료 신호 — 문서 패널이 이 시퀀스로 재읽기한다. 턴 중간의
        // document/file_changed 시점에는 VM→S3 동기화 전이라 S3 읽기가
        // 빈 값일 수 있다; 동기화는 턴 완료 후 끝나므로 여기서 올린다.
        setTurnSeq((n) => n + 1);
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

      liveTurnStartedRef.current = true;
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
      liveTurnStartedRef.current = true;
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
    getPending(projectId)
      .then((pending) => {
        if (cancelled) return;
        const parsed = safeParse<QuestionsPayload>(pending);
        if (parsed) setPendingQuestions(parsed);
      })
      .catch(() => {}); // degraded, not broken: pendingQuestions stays null
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Restore the chat timeline itself (Task 5) from GET /history — a SEPARATE
  // mount effect from the /pending restore above: independent endpoints,
  // independent failure domains. Degrades to an empty timeline (not a thrown
  // error) on failure, same fallback posture as /pending.
  useEffect(() => {
    let cancelled = false;
    getHistory(projectId)
      .then((h) => {
        if (cancelled) return;
        setItems((prev) => {
          const restored = h.map(historyItemToChatItem);
          // A live turn may have started while history was in flight — history
          // strictly precedes it chronologically, so prepend rather than replace.
          return liveTurnStartedRef.current ? [...restored, ...prev] : restored;
        });
      })
      .catch(() => {}) // degraded, not broken: items stays at its initial value
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
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
    historyLoading,
    activeDoc,
    turnSeq,
  };
}
