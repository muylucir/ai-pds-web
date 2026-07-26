// frontend/lib/useTurnStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { streamEvents } from "@/lib/api/sse";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import type { AgentEvent } from "@/lib/api/types";

// UI VIEW-STATE (not a backend contract): how streamed AgentEvent frames are
// projected into the chat timeline. Backend contract types stay in
// lib/api/types.ts.
export interface TraceEntry {
  kind: "status" | "file_changed";
  text: string | null;
  path: string | null;
}
export interface UserItem {
  id: string;
  role: "user";
  text: string;
}
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
}
// C2: structured timeline cards, materialized from file_changed paths seen
// during a completed turn. Pure filename-suffix mapping (see
// deriveCardsFromPaths below) — never content sniffing, never a gate/approval
// inference (see the plan header's "PROMINENT DEVIATION" note).
export interface QuestionsCardItem {
  id: string;
  role: "card";
  card: "questions";
  path: string;
}
export interface ArtifactCardItem {
  id: string;
  role: "card";
  card: "artifact";
  path: string;
}
export type CardItem = QuestionsCardItem | ArtifactCardItem;
export type ChatItem = UserItem | AiItem | CardItem;

let counter = 0;
const nextId = () => `item-${counter++}`;

// Pure filename mapping (zero methodology — same class of check as Plan B's
// established `isClarification` endsWith check in questions/page.tsx): a
// `-questions.md` path materializes a QuestionsCardItem (QuestionCardSlot
// decides AT RENDER TIME, by data shape, whether it's answered/clarification/
// unparsed); a `discovery-document.md` path materializes an ArtifactCardItem.
// One card per UNIQUE path per turn — a turn that touches the same file twice
// still yields a single card. Order follows first-seen order within the turn.
function deriveCardsFromPaths(paths: string[]): CardItem[] {
  const seen = new Set<string>();
  const cards: CardItem[] = [];
  for (const path of paths) {
    if (seen.has(path)) continue;
    seen.add(path);
    if (path.endsWith("-questions.md")) {
      cards.push({ id: nextId(), role: "card", card: "questions", path });
    } else if (path.endsWith("discovery-document.md")) {
      cards.push({ id: nextId(), role: "card", card: "artifact", path });
    }
  }
  return cards;
}

export interface TurnStream {
  items: ChatItem[];
  streaming: boolean;
  send: (text: string) => void;
}

// Drives one live agent turn at a time over the EXISTING GET /events SSE
// (Plan A's streamEvents). status/file_changed frames become the AI bubble's
// "추론 과정" trace; message frames accumulate into its text; an error-KIND
// frame sets its error; done/transport-close finish the turn AND (C2) derive
// zero or more structured cards from the turn's file_changed paths.
export function useTurnStream(projectId: string, initial: ChatItem[] = []): TurnStream {
  const [items, setItems] = useState<ChatItem[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const stopRef = useRef<null | (() => void)>(null);

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard on the live-stream ref (not the `streaming` state) so a stale
      // closure can't slip a concurrent send past a not-yet-flushed setState.
      if (trimmed === "" || stopRef.current) return;

      const aiId = nextId();
      // Local accumulator for THIS turn's file_changed paths — read at onDone
      // to derive cards, independent of React's async state batching.
      const turnPaths: string[] = [];

      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: trimmed },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      setStreaming(true);

      const finish = () => {
        setStreaming(false);
        stopRef.current = null;
      };

      stopRef.current = streamEvents(projectId, trimmed, {
        onEvent: (ev: AgentEvent) => {
          if (ev.kind === "file_changed" && ev.path) turnPaths.push(ev.path);
          patchAi(aiId, (it) => {
            if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
            if (ev.kind === "status" || ev.kind === "file_changed")
              return { ...it, trace: [...it.trace, { kind: ev.kind, text: ev.text, path: ev.path }] };
            if (ev.kind === "error")
              return { ...it, error: ev.text ?? "턴 처리 중 오류가 발생했습니다." };
            return it; // "done" is handled by onDone
          });
        },
        onDone: () => {
          patchAi(aiId, (it) => ({ ...it, streaming: false }));
          const derived = deriveCardsFromPaths(turnPaths);
          if (derived.length > 0) setItems((prev) => [...prev, ...derived]);
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
          finish();
        },
      });
    },
    [projectId, patchAi],
  );

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return { items, streaming, send };
}
