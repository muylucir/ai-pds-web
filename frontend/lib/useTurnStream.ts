// frontend/lib/useTurnStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { streamEvents } from "@/lib/api/sse";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import type { AgentEvent, QuestionFile } from "@/lib/api/types";

// UI VIEW-STATE (not a backend contract): how streamed AgentEvent frames are
// projected into the chat timeline. Backend contract types stay in
// lib/api/types.ts.
export interface TraceEntry {
  kind: "status" | "file_changed";
  text: string | null;
  path: string | null;
  // 도구가 **무엇을 했는지** — 읽은 파일, 돌린 명령, 검색 패턴.
  //
  // 라이브에서는 status 이벤트의 payload(`{"detail": "…"}`)로 오고, 복원에서는
  // HistoryTraceEntry의 필드로 온다. 값을 만드는 곳은 백엔드 한 곳이다
  // (backend/pathfinder/tool_trace.py) — 라이브와 복원이 갈라지면 새로고침 전후로
  // 화면이 달라진다. 아이콘과 구분자만 여기서 붙인다.
  detail?: string | null;
}
export interface UserItem {
  id: string;
  role: "user";
  text: string;
  // 복원된 답변 제출 턴의 구조화된 답변(GET /history의 HistoryItem.answers).
  // 있으면 ChatTimeline이 UI 언어로 문구를 다시 만든다 — 백엔드의 text는 이
  // 필드를 모르는 소비자를 위한 한국어 폴백이다. 라이브 턴에는 없다(그쪽은
  // answerSummary가 선택지 문자를 옵션 텍스트로 펼쳐 이미 만들어 둔다).
  answers?: Record<string, string> | null;
  // answers와 짝인 질문 payload(GET /history의 HistoryItem.questions). 둘이 다
  // 있으면 ChatTimeline이 라이브와 같은 answerSummary()를 불러 같은 문구를
  // 만든다 — 없으면 answers를 "1: A" 식으로 나열하는 폴백으로 떨어진다.
  questions?: QuestionFile | null;
}
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
  // 사용자가 이 턴을 끊었다. trace가 아닌 별도 필드인 이유는 성격이 다르기
  // 때문 — trace는 도구 실행 기록, 이것은 턴의 종결 사유다.
  interrupted?: boolean;
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

// 턴 개시(POST)의 실패는 상태 코드를 준다 — EventSource의 익명 onerror와 달리
// 원인을 말할 수 있는 유일한 지점이다. 413/431은 "입력이 길다"는 뜻이고, 그
// 구분이 없으면 이 버그의 증상("연결이 끊어졌습니다")이 그대로 돌아온다.
function isTooLong(err: unknown): boolean {
  const status = (err as { status?: number } | null)?.status;
  return status === 431 || status === 413;
}

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
  const t = useT();
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
              return { ...it, error: ev.text ?? t("stream.turnError") };
            return it; // "done" is handled by onDone
          });
        },
        onDone: () => {
          patchAi(aiId, (it) => ({ ...it, streaming: false }));
          const derived = deriveCardsFromPaths(turnPaths);
          if (derived.length > 0) setItems((prev) => [...prev, ...derived]);
          finish();
        },
        onError: (err) => {
          // 401(토큰 만료)과 네트워크 끊김을 EventSource가 구분해주지 않으므로
          // 세션을 확인해 만료면 로그인으로 보낸다. 살아 있으면 아래 메시지가 맞다.
          void redirectIfSessionExpired(undefined, window.location.pathname);
          patchAi(aiId, (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? t(isTooLong(err) ? "stream.tooLong" : "stream.disconnected"),
          }));
          finish();
        },
      });
    },
    [projectId, patchAi, t],
  );

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return { items, streaming, send };
}
