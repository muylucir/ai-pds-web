// frontend/lib/useWorkspaceStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { streamEvents, streamAnswers, streamFileAnswers, watchTurn } from "@/lib/api/sse";
import type { StreamHandlers, TurnCreated } from "@/lib/api/sse";
import { ApiError, getContext, getPending, getHistory, getTurn, interruptTurn } from "@/lib/api/client";
import { answerSummary } from "@/lib/answerSummary";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import type { AgentEvent, ContextUsage, QuestionsPayload, StagePayload, DocumentPayload,
  DraftPayload, PrototypeReadyPayload } from "@/lib/api/types";
import { historyItemToChatItem } from "@/lib/chatItems";
import { useTextDeltas } from "@/lib/useTextDeltas";
import type { UserItem, AiItem, HistoryCardItem, TraceEntry, LiveActivity } from "@/lib/chatItems";
import type { Dict } from "@/lib/i18n";

// Drives the three-pane workspace screen. It consumes the structured events
// (questions/stage/document) directly, so its ChatItem union is user/ai plus a
// HISTORY-ONLY card marker (lib/chatItems.ts) restored from GET /history, never
// derived from live file_changed paths.
export type { UserItem, AiItem, HistoryCardItem } from "@/lib/chatItems";
export type ChatItem = UserItem | AiItem | HistoryCardItem;

let counter = 0;
const nextId = () => `wf-item-${counter++}`;

// A failure to start a turn (POST) returns a status code — unlike EventSource's anonymous
// onerror, it is the only point that can tell the cause. 413/431 mean "the input is too long",
// and without that distinction this bug's symptom ("연결이 끊어졌습니다") comes right back.
function isTooLong(err: unknown): boolean {
  const status = (err as { status?: number } | null)?.status;
  return status === 431 || status === 413;
}

// Wait (ms) before reattaching to a dropped stream. Each attempt uses the next value, and when
// they run out we give up (about 48 seconds in total). Why not retry forever: when truly offline,
// the screen would be locked in "in progress" permanently. Even after giving up, the turn keeps
// running on the server, and a refresh reattaches via `GET /turn`.
export const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000];

// Machine phrases the backend puts on terminal events → screen text. Phrases not listed here are
// sentences the driver already produced in the project language (agent/prompts.turn_failed), so they are used as is.
const ERROR_KEYS: Record<string, keyof Dict> = {
  "agent turn failed": "stream.turnError",
  "turn interrupted": "stream.interrupted",
  "no pending questions": "stream.noPendingQuestions",
  "turn already in progress": "stream.turnInProgress",
};

// Must be the same value as the backend's claude_driver.INTERRUPTED_MARKER (proto/builder.py
// uses the same value too). It is a machine signal, not human-readable text — the "중단됨"
// on screen is drawn in the UI language by the AiMessage that receives this flag.
const INTERRUPTED_MARKER = "interrupted";

// Must be the same values as the backend's claude_driver.THINKING_MARKER / THINKING_DONE_MARKER.
// They are the same kind of machine signal, so if they leak through as trace lines, the English
// word "thinking" shows up verbatim in the collapsed accordion — the same regression
// INTERRUPTED_MARKER went through.
const THINKING_MARKER = "thinking";
const THINKING_DONE_MARKER = "thinking-done";

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
  interrupt: () => Promise<void>;
  pendingQuestions: QuestionsPayload | null;
  stages: StagePayload[];
  lastDocument: DocumentPayload | null;
  // Prototypes Discovery handed off to build. This is the basis for showing a "Prototypes 탭으로"
  // card in the chat (it does not rely on the agent's guidance sentence).
  prototypeReady: PrototypeReadyPayload | null;
  changedPaths: string[];
  historyLoading: boolean;
  /** The agent's context usage (shown above the input box). Restores the last turn's value when
   *  the screen opens, and updates via `context` events while a turn runs. null if absent. */
  context: ContextUsage | null;
  // The "document currently being discussed" the doc panel should follow — tracked
  // latest-wins from not only `document` events but also doc-like file_changed (isDocPath below).
  // version is filled only when it came from a `document` event (ui-bug2 sync fix).
  //
  // Since 2026-08-21, `document` is no longer the `submit_document` the model used to call; the
  // backend **derives** it from artifact writes (agent/reconcile.document_events). So it now
  // almost always arrives, and the isDocPath branch below remains as a backstop for writes the
  // hook cannot see (via Bash) — which is why this field was created in the first place.
  activeDoc: { path: string; version: string | null } | null;
  // Increments at the end of every turn — the panel rereads the document with this key. At the
  // time of a mid-turn document event, the VM→S3 sync has not happened yet, so a read may return
  // empty/404, and left alone there would never be a reread (ui-bug2 "empty" fix).
  turnSeq: number;
}

// Is this an artifact path worth following in the doc panel — .md files under aiplc-docs/,
// excluding record-keeping files (audit/state/questions).
//
// Why question files (*-questions.md) are in the same bucket as audit/state: in AI-PDS, the
// delivery path for questions is the AskUserQuestion tool, and the screen where the user answers
// is the QuestionForm in the right panel (the override section of discovery-config/CLAUDE.md). The
// markdown file remains only as the record the upstream rules require.
//
// So showing it in the doc panel puts two versions of the same questions side by side on one
// screen, and the two can never match in the first place: the SDK schema hard-limits questions to
// 1-4 and options to 2-4 (CLI 2.1.226's `questions: dt(...).min(1).max(4)` and "hard schema
// constraints; do not exceed them even if the user requests more — split into
// multiple calls instead"), so a 7-question document required by the rules is split into two
// rounds of 4+3 in the form, with the wording generated separately for each. As measured, this
// panel was exactly the path through which users discovered that mismatch.
export function isDocPath(path: string): boolean {
  return (
    path.startsWith("aiplc-docs/") &&
    path.endsWith(".md") &&
    !path.endsWith("/audit.md") &&
    path !== "aiplc-docs/audit.md" &&
    !path.endsWith("/aiplc-state.md") &&
    path !== "aiplc-docs/aiplc-state.md" &&
    !path.endsWith("-questions.md")
  );
}

export function useWorkspaceStream(projectId: string, initial: ChatItem[] = []): WorkspaceStream {
  const t = useT();
  const [items, setItems] = useState<ChatItem[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [stages, setStages] = useState<StagePayload[]>([]);
  const [lastDocument, setLastDocument] = useState<DocumentPayload | null>(null);
  const [prototypeReady, setPrototypeReady] =
    useState<PrototypeReadyPayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [context, setContext] = useState<ContextUsage | null>(null);
  // Has a live value ever arrived. Keeps the record loaded at mount (GET /context), if it arrives
  // late, from overwriting a live value just received — the record is always from an earlier turn.
  const liveContextRef = useRef(false);
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

  // Text deltas are appended once per frame (useTextDeltas). Every other bubble update goes
  // through patchAi, so flushing first here preserves the event order as is.
  const deltas = useTextDeltas((aiId, text) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai"
      ? { ...it, text: it.text + text, activity: { kind: "writing" } } : it)));
  });
  const flushText = deltas.flush;
  const pushText = deltas.push;

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    flushText();
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, [flushText]);

  // Shared per-frame projection for both streamEvents and streamAnswers: folds
  // message text into the AI bubble, files status/file_changed into its
  // trace, and mirrors the three structured kinds into the workspace's
  // sidebar/panel state (stage history, latest document, pending questions).
  const applyEvent = useCallback(
    (aiId: string, ev: AgentEvent) => {
      // **`activity` is overwritten by whichever event came last.** Once text starts flowing,
      // the preceding tool is no longer "what is happening now" — the old approach of pulling
      // the last tool out of the trace caused that mismatch ("자료를 확인하고 있어요" stayed up
      // even after the tool finished, while the answer was being written). This is the only point
      // that sees events in order, so carrying that order straight into state cannot go wrong —
      // text is batched per frame, but every other event flushes it first (patchAi).
      if (ev.kind === "message") {
        pushText(aiId, ev.text ?? "");
        return;
      }
      flushText();
      if (ev.kind === "file_changed" && ev.path) {
        setChangedPaths((prev) => (prev.includes(ev.path as string) ? prev : [...prev, ev.path as string]));
        // The agent creates most documents with file_write alone, without submit_document
        // (measured: prfaq.md etc.) — doc-like writes must also be tracked as the active
        // document for the panel to follow the conversation (ui-bug2).
        if (isDocPath(ev.path)) setActiveDoc({ path: ev.path, version: null });
      }
      if (ev.kind === "draft") {
        // Only the file being written and its character count go on the progress line (backend agent/tool_input_draft.py).
        const parsed = safeParse<DraftPayload>(ev.payload);
        if (!parsed) return;
        patchAi(aiId, (it) => ({
          ...it, activity: { kind: "drafting", path: ev.path ?? null, chars: parsed.chars ?? 0 },
        }));
        return;
      }
      if (ev.kind === "questions") {
        const parsed = safeParse<QuestionsPayload>(ev.payload);
        if (parsed) setPendingQuestions(parsed);
        return;
      }
      if (ev.kind === "context") {
        const parsed = safeParse<ContextUsage>(ev.payload);
        if (parsed) {
          liveContextRef.current = true;
          setContext(parsed);
        }
        return;
      }
      if (ev.kind === "stage") {
        const parsed = safeParse<StagePayload>(ev.payload);
        if (parsed) setStages((prev) => [...prev, parsed]);
        return;
      }
      if (ev.kind === "prototype_ready") {
        const parsed = safeParse<PrototypeReadyPayload>(ev.payload);
        if (parsed) setPrototypeReady(parsed);
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
        // An interrupt is the turn's termination reason, so it goes to a dedicated field, not the trace.
        // The driver sends it as a status rather than a new kind in order to reuse an event shape
        // that is already handled (claude_driver.interrupt). This marker exists only in the live
        // stream — it is not kept in the transcript, so this line does not reappear after a
        // refresh.
        if (ev.kind === "status" && ev.text === INTERRUPTED_MARKER) {
          return { ...it, interrupted: true };
        }
        // A thinking span. Sets both the `activity` the pinned line reads and the trace line the
        // timeline reads — the former is a single "now", the latter a list of "so far". The line is
        // left at the start for the sake of order: it must come before the tools that follow, and
        // even if the turn breaks off mid-thought, "it got this far" remains.
        if (ev.kind === "status" && ev.text === THINKING_MARKER) {
          return {
            ...it, activity: { kind: "thinking" },
            trace: [...it.trace,
                    { kind: "thinking", text: null, path: null, detail: null }],
          };
        }
        // The done marker does not touch `activity`. The next event (almost always a text
        // delta) overwrites it right away, so clearing it here would flash an empty line in between.
        if (ev.kind === "status" && ev.text === THINKING_DONE_MARKER) return it;
        if (ev.kind === "status" || ev.kind === "file_changed") {
          // A status's detail arrives in the payload (it has to be a field that passes through
          // redaction, and path is treated as a structural field that does not go through
          // redaction — the rationale is in backend/aipds/tool_trace.py).
          const detail = safeParse<{ detail?: string }>(ev.payload)?.detail ?? null;
          const trace: TraceEntry = {
            kind: ev.kind, text: ev.text, path: ev.path, detail,
          };
          const activity: LiveActivity = ev.kind === "file_changed"
            ? { kind: "file", path: ev.path }
            : { kind: "tool", tool: ev.text, detail };
          return { ...it, trace: [...it.trace, trace], activity };
        }
        if (ev.kind === "error") {
          const key: keyof Dict | undefined =
            ev.text ? ERROR_KEYS[ev.text] : "stream.turnError";
          return { ...it, error: key ? t(key) : ev.text };
        }
        return it; // "done" is handled by onDone
      });
    },
    [patchAi, pushText, flushText, t],
  );

  // Function that attaches to a running turn. runTurn calls it to show that turn when it gets a
  // 409 (another turn is running), and since the two reference each other, they are linked via a ref.
  const attachRef = useRef<(turnId: string, input?: string | null) => void>(() => {});

  // Streams one turn to the screen. `opener` opens the first connection, and when it drops we
  // reattach to **the same turn** from the last seq received.
  //
  // A turn is a server job (backend aipds/turn_job.py) — the turn keeps running even if the
  // screen disconnects, and reattaching with the same id resumes from the missed events. So a drop
  // is not a turn failure, and this function's job is to reattach quietly. Sleep and screensavers
  // (turns take 2.5–5.6 minutes and the default screensaver is 5–10 minutes) make this path
  // routine rather than exceptional.
  const runTurn = useCallback(
    (
      opener: (handlers: StreamHandlers) => () => void,
      aiId: string,
      knownTurnId: string | null = null,
    ) => {
      setStreaming(true);
      // A stream can finish SYNCHRONOUSLY inside `opener(...)` (e.g. a test
      // double that drives all frames before returning) — before the
      // assignment below would run. Track that with a local flag instead of
      // relying on assignment order, so `finish()`'s `stopRef.current = null`
      // never gets clobbered by the stale closer being stored afterwards.
      let finished = false;
      let turnId = knownTurnId;
      let lastSeq = 0;
      let attempt = 0;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let closeCurrent: (() => void) | null = null;
      const finish = () => {
        finished = true;
        if (timer) clearTimeout(timer);
        timer = null;
        setStreaming(false);
        stopRef.current = null;
        // Turn-end signal — the doc panel rereads with this sequence. At the time of mid-turn
        // document/file_changed, the VM→S3 sync has not happened, so an S3 read may be empty;
        // the sync finishes after the turn completes, so we bump it here.
        setTurnSeq((n) => n + 1);
      };
      const giveUp = (message: string) => {
        patchAi(aiId, (it) => ({ ...it, streaming: false, error: it.error ?? message }));
        finish();
      };

      // The connection dropped (onError, or closed without a terminal event). If `err` has an HTTP
      // status, the POST that **starts** the turn was rejected — no turn to attach to was created.
      const onDrop = (err: unknown) => {
        if (finished) return;
        // EventSource does not distinguish 401 (token expired) from a network drop, so we
        // check the session and send the user to login if it has expired.
        void redirectIfSessionExpired(undefined, window.location.pathname);
        if (err instanceof ApiError) {
          if (err.status === 409 && err.turnId) {
            // Another turn is already running — this input never reached the agent. Show that
            // turn (a second tab, or a turn the screen did not know about).
            giveUp(t("stream.turnInProgress"));
            attachRef.current(err.turnId);
            return;
          }
          return giveUp(t(isTooLong(err) ? "stream.tooLong" : "stream.disconnected"));
        }
        if (turnId === null || attempt >= RECONNECT_DELAYS_MS.length) {
          return giveUp(t(turnId === null ? "stream.disconnected" : "stream.lost"));
        }
        const id = turnId;
        timer = setTimeout(() => {
          timer = null;
          if (finished) return;
          // Before attaching, check whether the turn still exists. If its retention period has passed or a restart wiped it,
          // there is nowhere to reattach — artifacts come back to the doc panel via the turnSeq that finish() bumps.
          getTurn(projectId)
            .then((turn) => {
              if (finished) return;
              if (!turn || turn.turn_id !== id || turn.state === "interrupted") {
                return giveUp(t(turn?.state === "interrupted"
                  ? "stream.interrupted" : "stream.lost"));
              }
              closeCurrent = watchTurn(projectId, id, lastSeq, connection());
            })
            .catch(() => onDrop(null));
        }, RECONNECT_DELAYS_MS[attempt++]);
      };

      // Handlers for one connection. Created fresh per connection — `terminal` and `dropped` belong to that connection.
      const connection = (): StreamHandlers => {
        let terminal = false;
        let dropped = false;
        return {
          onCreated: (created: TurnCreated) => {
            if (created.turn_id) turnId = created.turn_id;
          },
          onEvent: (ev, seq) => {
            if (seq !== undefined) lastSeq = seq;
            if (ev.kind === "done" || ev.kind === "error") terminal = true;
            // We have started resuming, so the next drop counts again from the first wait.
            attempt = 0;
            applyEvent(aiId, ev);
          },
          onError: (err) => {
            dropped = true;
            onDrop(err);
          },
          onDone: () => {
            if (terminal) {
              patchAi(aiId, (it) => ({ ...it, streaming: false }));
              finish();
            } else if (!dropped) {
              onDrop(null);   // closed without a terminal event = dropped
            }
          },
        };
      };

      closeCurrent = opener(connection());
      const stop = () => {
        if (timer) clearTimeout(timer);
        timer = null;
        closeCurrent?.();
      };
      if (finished) stop();
      else stopRef.current = stop;
    },
    [applyEvent, patchAi, projectId, t],
  );

  // Attach to a running turn — replay from the start (`after=0`) to refill the bubbles. With the turn's input, its bubble
  // comes first: a turn another screen started (approval, an answer change on the handoff tab) otherwise showed an AI reply
  // to nothing until a refresh restored the person's side from the transcript. Not added when history already ends with it.
  const attach = useCallback(
    (turnId: string, input?: string | null) => {
      if (stopRef.current) return;
      liveTurnStartedRef.current = true;
      const aiId = nextId();
      setItems((prev) => {
        const lastUser = [...prev].reverse().find((i) => i.role === "user");
        const user: ChatItem[] = input && lastUser?.text !== input
          ? [{ id: nextId(), role: "user", text: input }] : [];
        return [...prev, ...user,
          { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null }];
      });
      runTurn((handlers) => watchTurn(projectId, turnId, 0, handlers), aiId, turnId);
    },
    [projectId, runTurn],
  );
  attachRef.current = attach;

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
      // Which path to send through is decided **now** — because setPendingQuestions(null) below
      // removes the discriminator.
      const file = pendingQuestions?.file;
      // Fallback text for the parked-turn resume path (= the AskUserQuestion escape hatch). That
      // route does not have the question file in hand and cannot build `summary`, so it uses the
      // frontend renderer. The point is to capture the questions **now** — setPendingQuestions(null)
      // below removes them.
      const localSummary = pendingQuestions
        ? answerSummary(pendingQuestions.questions, answers, t)
        : t("chat.answersSubmitted");
      setPendingQuestions(null);

      const aiId = nextId();
      const userId = nextId();
      setItems((prev) => [
        ...prev,
        // The text is filled in **when the server response arrives** (onCreated below). Why it is
        // empty for now: while the frontend was building this bubble, what the user saw and what was
        // recorded in the transcript differed — because there were two renderers
        // (the measurements are in the header of backend/aipds/answer_summary.py). Now a single
        // string built by the server is used for both the screen and the record.
        //
        // Reserving the slot in advance is for ordering: it must come before the AI bubble.
        { id: userId, role: "user", text: "" },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      const onCreated = (created: { summary?: string }) => {
        // No `summary` means the parked-turn resume path — `localSummary` above is that path's
        // render. Dropping it to a marker would make only that path unreadable, and since restore
        // shows the actual answers via the `answer_store` join, live and restore would diverge in
        // **opposite directions** (agent/answer_store.py).
        //
        // This path is not the only reason the frontend renderer remains — the prototype builder's
        // stream (usePrototypeStream) and history restore (ChatTimeline) use it too.
        // Once those two also move to the backend, the module can be deleted along with this fallback.
        const text = created.summary || localSummary;
        setItems((prev) => prev.map((it) =>
          it.id === userId ? { ...it, text } : it));
      };
      runTurn(
        (handlers) =>
          file
            ? streamFileAnswers(projectId, file, answers, { ...handlers, onCreated })
            : streamAnswers(projectId, answers, { ...handlers, onCreated }),
        aiId,
      );
    },
    [projectId, runTurn, pendingQuestions, t],
  );

  const interrupt = useCallback(async () => {
    // Swallow failures: interrupting is an auxiliary action, and even if it fails the turn runs on
    // as is and the screen is not blocked. The user can press it again.
    try {
      await interruptTurn(projectId);
    } catch {
      /* ignore */
    }
  }, [projectId]);

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

  // Context usage of the last turn. On failure there is simply no display.
  useEffect(() => {
    let cancelled = false;
    getContext(projectId)
      .then((c) => {
        if (!cancelled && !liveContextRef.current) setContext(c);
      })
      .catch(() => {});
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
          const restored = h.map((it) => historyItemToChatItem(it, nextId()));
          // A live turn may have started while history was in flight — history
          // strictly precedes it chronologically, so prepend rather than replace.
          if (!liveTurnStartedRef.current) return restored;
          // An attached turn's input bubble may already be the last person's line of history — keep one.
          const lastUser = [...restored].reverse().find((i) => i.role === "user");
          const firstLive = prev.find((i) => i.role === "user");
          const live = firstLive && lastUser?.text === firstLive.text ? prev.filter((i) => i !== firstLive) : prev;
          return [...restored, ...live];
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

  // When the screen opens, attach if a turn is running (refresh, returning from another screen,
  // approval on the review screen). If the turn was cut off by a restart, leave that fact at the end
  // of the conversation — otherwise the user sees an unfinished conversation with no explanation.
  const tRef = useRef(t);
  tRef.current = t;
  useEffect(() => {
    let cancelled = false;
    getTurn(projectId)
      .then((turn) => {
        if (cancelled || !turn || stopRef.current) return;
        if (turn.state === "running") {
          attachRef.current(turn.turn_id, turn.input);
        } else if (turn.state === "interrupted") {
          liveTurnStartedRef.current = true;
          setItems((prev) => [
            ...prev,
            { id: nextId(), role: "ai", text: "", trace: [], streaming: false,
              error: tRef.current("stream.interrupted") },
          ]);
        }
      })
      .catch(() => {}); // degraded, not broken: if attaching fails, only the history is shown
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
    interrupt,
    pendingQuestions,
    stages,
    lastDocument,
    prototypeReady,
    changedPaths,
    historyLoading,
    context,
    activeDoc,
    turnSeq,
  };
}
