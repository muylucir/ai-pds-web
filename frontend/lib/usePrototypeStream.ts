// frontend/lib/usePrototypeStream.ts
"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import {
  streamPrototypeEvents,
  submitPrototypeAnswers,
  interruptSession,
  startSession,
  watchBuildTurn,
  getBuildSession,
  getBuildHistory,
  FIRST_TURN_SENTINEL,
  type BuildTurnSummary,
} from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import type { StreamHandlers, TurnCreated } from "@/lib/api/sse";
import { RECONNECT_DELAYS_MS } from "@/lib/useWorkspaceStream";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import { answerSummary } from "@/lib/answerSummary";
import type { AgentEvent } from "@/lib/api/types";
import type {
  QuestionsPayload,
  BuildCompletePayload,
  AgentActivityPayload,
  ContextUsage,
} from "@/lib/api/types";
import { applyAgentActivity, runningAgents, type AgentRow } from "@/lib/protoAgents";
import { historyItemToChatItem } from "@/lib/chatItems";
import { useTextDeltas } from "@/lib/useTextDeltas";
import type { UserItem, AiItem, HistoryCardItem, TraceEntry, LiveActivity } from "@/lib/chatItems";

// A NEW hook modeled on useWorkspaceStream (the workspace's stream pattern)
// for the prototype build chat panel. Simpler than useWorkspaceStream: no
// stage/document/activeDoc/turnSeq branches — a build session has no
// multi-document sidebar.
//
// **The conversation is restored from two places.** Past sessions' conversations come from the
// transcript (GET /history — backend proto/history.py), and the open session's turns come from
// that session's turn log. A build turn is the session's server job, not a request (backend
// aipds/turn_job.py), and the session keeps all of its turns. So when the panel reopens
// (refresh, close and reopen), `resume()` lays down the history, then replays the open session's
// turns from the start to restore the bubbles, question cards, answers, and completion card in
// order, and attaches to a running turn as is. The history arrives cut off before those turns,
// so the same turn never appears twice.
export type { UserItem, AiItem, HistoryCardItem } from "@/lib/chatItems";
export type ChatItem = UserItem | AiItem | HistoryCardItem;

let counter = 0;
const nextId = () => `proto-item-${counter++}`;

// A failure to start a turn (POST) gives a status code — unlike EventSource's anonymous onerror,
// it is the only point where the cause can be told. 413/431 mean "the input is too long", and
// without that distinction this bug's symptom ("연결이 끊어졌습니다") comes right back.
function isTooLong(err: unknown): boolean {
  const status = (err as { status?: number } | null)?.status;
  return status === 431 || status === 413;
}

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
  /** The sub-agents running right now. This value keeps the screen from looking frozen while the
   *  build agent hands work off in parallel via the Agent tool — the pinned line draws it as
   *  rows (protoAgents.ts header). Outside a parallel section it is an empty array. */
  agents: AgentRow[];
  pendingQuestions: QuestionsPayload | null;
  /** The summary from when the agent declared the build complete. If this value is set, the
   *  session has already closed or will close within seconds (the backend closes it with a
   *  grace timer). */
  buildComplete: BuildCompletePayload | null;
  changedPaths: string[];
  /** Loading the past conversation. Keeps the empty panel's placeholder text from showing before
   *  the history and then disappearing. */
  historyLoading: boolean;
  /** The build agent's context usage (shown above the input). The history gives the last turn's
   *  value (null if the next conversation is a new session), and `context` events update it
   *  while a turn runs. */
  context: ContextUsage | null;
  startBuild: () => void;
  /** Lays the past conversation **before** the current one. Used by a newly opened session (auto
   *  opening) — for an already open session, `resume()` does the same thing. */
  restoreHistory: () => Promise<void>;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => Promise<void>;
  interrupt: () => Promise<void>;
  /** Improves a completed build: opens a new session and fires the opening turn. The server
   *  substitutes `__first__` with the handoff prompt, so no new API is needed. */
  restartForImprovement: () => Promise<void>;
  /** Restores an already open session's conversation and attaches to a running turn (when the
   *  panel reopens). */
  resume: () => Promise<void>;
}

export function usePrototypeStream(projectId: string, slug: string): PrototypeStream {
  const t = useT();
  const [items, setItems] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [buildComplete, setBuildComplete] = useState<BuildCompletePayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [context, setContext] = useState<ContextUsage | null>(null);
  // Whether a live value has already arrived — keeps a late-arriving history record from
  // overwriting it (same reason as the same ref in useWorkspaceStream).
  const liveContextRef = useRef(false);
  // **The rows belong to the screen, not to a bubble** — the pinned line reads them, so they
  // must sit in the same place as `activity`, but putting them on a bubble like `activity`
  // would make it ambiguous which bubble they belong to in a parallel section (one turn splits
  // into several bubbles). It is a per-turn value, so the hook holds it directly and clears it
  // at the start of every turn.
  const [agents, setAgents] = useState<AgentRow[]>([]);
  // A ref mirror of the same value. **It exists because a read is needed**: the `done` frame has no
  // label (started is the only source), and the trace line must use that label, so the merge
  // **result** has to be read within the same frame. Writing the trace inside a setState updater
  // attaches the line twice under StrictMode's double invocation — so the merge is done on the ref
  // and the state receives its result. Same discipline as the other refs in this file.
  const agentsRef = useRef<AgentRow[]>([]);
  const stopRef = useRef<null | (() => void)>(null);
  // The last question received. The answer bubble's text is built from that question's option
  // labels (answerSummary), so it must be held even after the form closes — the answer arrives
  // **later**, as the turn log's `answers` event.
  const lastQuestionsRef = useRef<QuestionsPayload | null>(null);
  // The function that attaches to a running turn. runTurn calls it to show that turn when it
  // gets a 409 (another turn is running), and since the two reference each other they are
  // joined via a ref.
  const attachRef = useRef<(turnId: string) => void>(() => {});
  const resumingRef = useRef(false);
  // The AI bubble that events are landing in RIGHT NOW — not the bubble the
  // turn started with. A "questions" event does NOT close the stream (the
  // harness parks on its pending-answer future, builder.py's
  // `await self._pending_question`), so one stream spans the whole build and
  // this pointer is what keeps that from becoming one endless bubble:
  // submitAnswers moves it to a fresh bubble after the user's answer, and
  // every writer below (applyEvent, onDone/onError, the 409 path) targets
  // whatever it points at instead of a captured id.
  const currentAiIdRef = useRef<string | null>(null);
  // A tool ran AFTER the current bubble had already said something — so the
  // next text belongs to a new bubble (see openAiBubble/applyEvent).
  const splitArmedRef = useRef(false);
  // Whether the current bubble holds any text yet. Splitting is gated on this
  // so a tool run before the first word doesn't emit an empty bubble (the
  // common "status → first message" opening of every turn).
  const hasTextRef = useRef(false);

  // Text deltas are appended once per frame (useTextDeltas). Every other path that touches a bubble
  // — patchAi, bubble splitting (openAiBubble), cleanup of empty bubbles at turn end — flushes
  // first. That preserves order, and keeps a bubble with not-yet-appended text from being deleted
  // as an "empty bubble".
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

  // Seal whatever bubble is current and open a fresh one, optionally with
  // items (a user bubble) between them. The single place a bubble is born:
  // turn start, an answers roundtrip, and a mid-turn tool boundary all go
  // through here, so the ref/flag bookkeeping can't drift between them.
  const openAiBubble = useCallback((between: ChatItem[] = []): string => {
    flushText();
    const prevId = currentAiIdRef.current;
    const aiId = nextId();
    currentAiIdRef.current = aiId;
    splitArmedRef.current = false;
    hasTextRef.current = false;
    setItems((prev) => [
      // The sealed bubble stops streaming, which moves the typing dots onto the
      // new one (and reveals the sealed bubble's collapsed 진행 기록). The live
      // activity line is no longer per-bubble — it is the screen's pinned bar
      // (LiveActivityBar), fed by `activity` on whichever bubble is streaming.
      ...prev.map((it) => (it.id === prevId && it.role === "ai" ? { ...it, streaming: false } : it)),
      ...between,
      { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
    ]);
    return aiId;
  }, [flushText]);

  // Shared per-frame projection: folds message text into the AI bubble,
  // status/file_changed into its trace + the changedPaths list, and mirrors
  // "questions" into pendingQuestions WITHOUT touching streaming — the turn
  // stays open on the server until /answers resolves it.
  //
  // `aiId` is read from currentAiIdRef at CALL time, not captured when the
  // turn opened, so frames arriving after a bubble split (a tool boundary or
  // an answers roundtrip) land in the bubble that split opened.
  const applyEvent = useCallback(
    (aiId: string, ev: AgentEvent) => {
      if (ev.kind !== "message") flushText();
      if (ev.kind === "file_changed" && ev.path) {
        setChangedPaths((prev) => (prev.includes(ev.path as string) ? prev : [...prev, ev.path as string]));
      }
      if (ev.kind === "questions") {
        const parsed = safeParse<QuestionsPayload>(ev.payload);
        if (parsed) {
          lastQuestionsRef.current = parsed;
          setPendingQuestions(parsed);
        }
        return;
      }
      if (ev.kind === "answers") {
        // The user answered (the server records it in the turn log — proto/session.send_answers).
        // The answer comes **after** the question and **before** the agent's response: open a new
        // bubble and put the answer bubble in between. Live and replay take the same path, so the
        // conversation is the same after a refresh.
        const answers = safeParse<{ answers?: Record<string, string> }>(ev.payload)?.answers ?? {};
        const questions = lastQuestionsRef.current;
        const summary = questions
          ? answerSummary(questions.questions, answers, t)
          : t("chat.answersSubmitted");
        openAiBubble([{ id: nextId(), role: "user", text: summary }]);
        setPendingQuestions(null);
        return;
      }
      if (ev.kind === "context") {
        // This belongs to the screen, not a bubble — it does not factor into bubble-splitting
        // decisions either.
        const parsed = safeParse<ContextUsage>(ev.payload);
        if (parsed) {
          liveContextRef.current = true;
          setContext(parsed);
        }
        return;
      }
      if (ev.kind === "build_complete") {
        // Do not touch streaming — the following `done` closes the turn via onDone as usual.
        // The backend closes the session with a grace timer after this declaration, so
        // from this point send() is no longer valid.
        const parsed = safeParse<BuildCompletePayload>(ev.payload);
        if (parsed) setBuildComplete(parsed);
        return;
      }
      if (ev.kind === "agent_activity") {
        const parsed = safeParse<AgentActivityPayload>(ev.payload);
        if (!parsed) return;
        const next = applyAgentActivity(agentsRef.current, parsed, Date.now());
        agentsRef.current = next;
        setAgents(next);
        // Only finished agents are recorded in the trace. The start is already recorded by the
        // `Agent` tool's status, down to what was delegated (tool_trace's `Agent → description`),
        // so recording it again here would make the same fact two lines.
        if (parsed.state === "done") {
          // Read the label and summary from the **merged row** — the two values come from
          // different frames (the label from started, the summary from the late-arriving end).
          // Looking only at the payload, the completion line would lose its name or summary.
          const row = next.find((r) => r.id === parsed.task_id);
          const line: TraceEntry = {
            kind: "agent",
            text: row?.label ?? null,
            path: null,
            detail: row?.summary ?? null,
            status: row?.status ?? null,
            taskId: parsed.task_id,
          };
          patchAi(aiId, (it) => {
            // **Update rather than append.** One agent's end can arrive twice
            // (task_updated → task_notification), and the summary is only on the later one.
            // Simply appending would show completion as two lines.
            const at = it.trace.findIndex(
              (e) => e.kind === "agent" && e.taskId === parsed.task_id);
            if (at === -1) return { ...it, trace: [...it.trace, line] };
            const trace = [...it.trace];
            trace[at] = line;
            return { ...it, trace };
          });
        }
        // **Do not touch** the pinned line's `activity`: that line describes the orchestrating agent's
        // work, and the rows below describe the sub-agents' work. Overwriting it here would erase
        // what the lead is waiting on with a sub-agent's tool name.
        return;
      }
      if (ev.kind === "error") {
        // A pending question can never be answered once the turn itself has
        // errored out — mirror the harness's own interrupt-clears-pending
        // fix (sdk_driver.py's interrupt()) on this side of the same race,
        // so the form doesn't linger unanswerable.
        setPendingQuestions(null);
      }
      let target = aiId;
      if (ev.kind === "message") {
        // Text after a tool run is a new utterance: the harness emits one
        // "message" per TextBlock (builder.py's _translate), and a build turn
        // interleaves dozens of them with tool calls. Concatenating the lot
        // produced the run-on bubble in files/proto.png.
        if (splitArmedRef.current) target = openAiBubble();
        if (ev.text) hasTextRef.current = true;
        pushText(target, ev.text ?? "");
        return;
      } else if (ev.kind === "status" || ev.kind === "file_changed") {
        // Arm only once the bubble has said something — a tool before the
        // first word (every turn opens that way) must not split off an empty
        // bubble. The tool itself is traced on the bubble it interrupted, so
        // "추론 과정" stays attached to the text it belongs to.
        if (hasTextRef.current) splitArmedRef.current = true;
      }
      patchAi(target, (it) => {
        // `activity` is the single "what it is doing now" that the pinned line above the input
        // reads. The last event to arrive overwrites it, so when a tool finishes and text flows
        // it automatically moves on to writing — the same convention as the workspace hook
        // (useWorkspaceStream), and if they diverge the same bar behaves differently on the two
        // screens. Text (`message`) went to pushText above — when appended it moves `activity`
        // to writing (useTextDeltas's apply).
        if (ev.kind === "status" || ev.kind === "file_changed") {
          // The backend sends what it did as `{"detail": "…"}` (tool_trace.py).
          // Previously this value was dropped, so the build screen's trace and pinned line showed
          // only the bare tool name, unlike Discovery — with just `Bash`, you cannot tell what
          // command is running, which is one of the reasons the screen looked frozen.
          const detail = safeParse<{ detail?: string }>(ev.payload)?.detail ?? null;
          const trace: TraceEntry = { kind: ev.kind, text: ev.text, path: ev.path, detail };
          const activity: LiveActivity = ev.kind === "file_changed"
            ? { kind: "file", path: ev.path }
            : { kind: "tool", tool: ev.text, detail };
          return { ...it, trace: [...it.trace, trace], activity };
        }
        if (ev.kind === "error") return { ...it, error: ev.text ?? t("stream.buildError") };
        return it; // "done" is handled by onDone
      });
    },
    [patchAi, openAiBubble, pushText, flushText, t],
  );

  // Streams one turn to the screen. `opener` opens the first connection, and on a drop it
  // reattaches to the **same turn** from the last seq received (same convention as
  // useWorkspaceStream's runTurn). `onFinished` is used by replay to stream turns one at a time
  // in order.
  const runTurn = useCallback(
    (
      opener: (handlers: StreamHandlers) => () => void,
      aiId: string,
      knownTurnId: string | null = null,
      onFinished?: () => void,
    ) => {
      currentAiIdRef.current = aiId;
      setStreaming(true);
      // A sub-agent's lifetime is the lifetime of the turn that spawned it (the backend also clears
      // the join table every turn — builder.run). If left in place, the rows of already-dead agents
      // would attach to the second turn's first frame.
      agentsRef.current = [];
      setAgents([]);
      // A stream can finish SYNCHRONOUSLY inside `opener(...)` (e.g. a test
      // double that drives every frame before returning) — before the
      // assignment below runs. Track that with a local flag rather than
      // relying on assignment order (same guard as useWorkspaceStream).
      let finished = false;
      let turnId = knownTurnId;
      let lastSeq = 0;
      let attempt = 0;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let closeCurrent: (() => void) | null = null;
      const finish = () => {
        finished = true;
        // So the empty-bubble cleanup below does not treat not-yet-appended text as "empty".
        flushText();
        if (timer) clearTimeout(timer);
        timer = null;
        setStreaming(false);
        stopRef.current = null;
        // When the turn ends the rows disappear — the record of finished agents is already held by
        // the bubble's collapsed trace (the agent_activity branch of applyEvent). Without clearing
        // here, a spinning spinner would remain even after the turn ended.
        agentsRef.current = [];
        setAgents([]);
        // Drop a bubble the turn never filled. An answers roundtrip opens one
        // for the reply, and a turn that ends first (done right after the
        // answers, or an interrupt) would otherwise leave a blank white box
        // under the answer. Anything at all in it — text, an error, a tool
        // trace — is content, so only the truly empty one is pruned.
        const lastId = currentAiIdRef.current;
        if (lastId) {
          setItems((prev) =>
            prev.filter(
              (it) =>
                !(
                  it.role === "ai" &&
                  it.id === lastId &&
                  it.text === "" &&
                  it.error === null &&
                  it.trace.length === 0
                ),
            ),
          );
        }
        currentAiIdRef.current = null;
        onFinished?.();
      };
      // Every handler resolves the target bubble through the ref (falling back
      // to the id this turn opened with) rather than closing over `aiId`: an
      // answers roundtrip repoints it mid-stream, and `done`/`error` must end
      // the bubble the user is actually watching.
      const liveId = () => currentAiIdRef.current ?? aiId;
      const giveUp = (message: string) => {
        patchAi(liveId(), (it) => ({ ...it, streaming: false, error: it.error ?? message }));
        setPendingQuestions(null); // same defensive clear as the error-kind path
        finish();
      };
      // The connection dropped. If `err` has an HTTP status, the POST that **starts** the turn was
      // rejected — no turn was created to attach to.
      const onDrop = (err: unknown) => {
        if (finished) return;
        // EventSource does not distinguish a 401 (token expired) from a network drop, so
        // check the session and send the user to login if it expired.
        void redirectIfSessionExpired(undefined, window.location.pathname);
        if (err instanceof ApiError) {
          if (err.status === 409 && err.turnId) {
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
          // Before attaching, check whether that turn is still in the session. If the session
          // closed (idle, completion grace period) there is nowhere to reattach.
          getBuildSession(projectId, slug)
            .then((sess) => {
              if (finished) return;
              if (!sess?.turns.some((x) => x.turn_id === id)) {
                return giveUp(t("stream.lost"));
              }
              closeCurrent = watchBuildTurn(projectId, slug, id, lastSeq, connection());
            })
            .catch(() => onDrop(null));
        }, RECONNECT_DELAYS_MS[attempt++]);
      };
      // Handlers for one connection. Created fresh for each connection — `terminal` and
      // `dropped` belong to that connection.
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
            attempt = 0;
            applyEvent(liveId(), ev);
          },
          onError: (err) => {
            dropped = true;
            onDrop(err);
          },
          onDone: () => {
            if (terminal) {
              patchAi(liveId(), (it) => ({ ...it, streaming: false }));
              finish();
            } else if (!dropped) {
              onDrop(null);   // closed without a terminal event = a drop
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
    [applyEvent, patchAi, flushText, projectId, slug, t],
  );

  // Attach to a running turn — replay it from the start to fill the bubbles.
  const attach = useCallback(
    (turnId: string) => {
      if (stopRef.current) return;
      const aiId = openAiBubble();
      runTurn((handlers) => watchBuildTurn(projectId, slug, turnId, 0, handlers), aiId, turnId);
    },
    [projectId, slug, runTurn, openAiBubble],
  );
  attachRef.current = attach;

  // Loads the past conversation and lays it before the current one. Returns the list of turns
  // to replay next — the history arrives cut off before those turns, so both must come from one
  // response (getBuildHistory). null if it cannot be loaded: the panel is usable with an empty
  // conversation too.
  //
  // **Prepend it.** In a newly opened session the opening turn can appear on screen before the
  // history response, and the history is always the earlier conversation — replacing would erase
  // the turn that just started (same judgment as useWorkspaceStream's liveTurnStartedRef).
  const loadHistory = useCallback(async (): Promise<BuildTurnSummary[] | null> => {
    setHistoryLoading(true);
    try {
      const h = await getBuildHistory(projectId, slug);
      const restored = h.items.map((it) => historyItemToChatItem(it, nextId()));
      setItems((prev) => [...restored, ...prev]);
      if (!liveContextRef.current) setContext(h.context ?? null);
      return h.turns;
    } catch {
      return null;
    } finally {
      setHistoryLoading(false);
    }
  }, [projectId, slug]);

  const restoreHistory = useCallback(async () => {
    await loadHistory();
  }, [loadHistory]);

  // Restores an already open session's conversation. Lays down the past conversation first, replays
  // the open session's turns one at a time **in order** from the start (the next turn opens only
  // after the previous one ends — bubble order is conversation order), and attaches to a running
  // turn to keep receiving it.
  const resume = useCallback(async () => {
    if (resumingRef.current || stopRef.current) return;
    resumingRef.current = true;
    try {
      // Even if the history cannot be loaded, restore the open session's turns — attaching to a
      // running turn is more urgent than showing the past conversation (if it cannot attach,
      // sending is blocked with 409).
      const turns = (await loadHistory())
        ?? (await getBuildSession(projectId, slug))?.turns ?? [];
      for (const turn of turns) {
        if (stopRef.current) return;
        const between: ChatItem[] = turn.input
          ? [{ id: nextId(), role: "user", text: turn.input }] : [];
        const aiId = openAiBubble(between);
        const open = (handlers: StreamHandlers) =>
          watchBuildTurn(projectId, slug, turn.turn_id, 0, handlers);
        if (turn.state === "running") {
          // A running turn is the last one (a session has only one running turn). Attach and
          // return — waiting for that turn to end would keep resume from finishing for the
          // whole build.
          runTurn(open, aiId, turn.turn_id);
          return;
        }
        await new Promise<void>((resolve) => runTurn(open, aiId, turn.turn_id, resolve));
      }
    } catch {
      /* Even if restoring fails the panel is usable — just keep talking in an empty conversation */
    } finally {
      resumingRef.current = false;
    }
  }, [projectId, slug, runTurn, openAiBubble, loadHistory]);

  // The auto first-build turn: opens the events stream with the "__first__"
  // sentinel (routes.py substitutes session.first_prompt() server-side) and
  // adds ONLY an AI bubble — there is no user-authored text for this turn.
  const startBuild = useCallback(() => {
    if (stopRef.current) return;
    const aiId = openAiBubble();
    runTurn((handlers) => streamPrototypeEvents(projectId, slug, FIRST_TURN_SENTINEL, handlers), aiId);
  }, [projectId, slug, runTurn, openAiBubble]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      // Guard on the live-stream ref (not `streaming` state) so a stale
      // closure can't slip a concurrent send past a not-yet-flushed setState.
      if (trimmed === "" || stopRef.current) return;
      const aiId = openAiBubble([{ id: nextId(), role: "user", text: trimmed }]);
      runTurn((handlers) => streamPrototypeEvents(projectId, slug, trimmed, handlers), aiId);
    },
    [projectId, slug, runTurn, openAiBubble],
  );

  const submitAnswers = useCallback(
    async (answers: Record<string, string>) => {
      // Read the questions before the submit: the bubble needs their text and
      // the success path clears them. Only appended once the server accepts —
      // a bubble on the 409 path would claim a submission that never landed.
      const summary = pendingQuestions
        ? answerSummary(pendingQuestions.questions, answers, t)
        : t("chat.answersSubmitted");
      const ok = await submitPrototypeAnswers(projectId, slug, answers);
      if (ok) {
        // No new STREAM here, unlike `send` — events keep flowing on the one
        // already open. The answer bubble is NOT drawn here either: the server
        // records the answers in the turn log (proto/session.send_answers) and
        // the `answers` event opens it in applyEvent, between the question and
        // the reply. One path for live and replay keeps a refreshed panel
        // showing the same conversation.
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
          error: it.error ?? t("stream.answersFailed"),
        }));
      }
    },
    [projectId, slug, patchAi, pendingQuestions, openAiBubble, t],
  );

  const interrupt = useCallback(async () => {
    await interruptSession(projectId, slug);
    // No local state change here — the harness reports the aborted turn
    // (a "status" event with text "interrupted", then "done"/"error") on the
    // SAME open SSE stream; applyEvent/runTurn's onDone/onError handle it
    // exactly like any other turn end.
  }, [projectId, slug]);

  const restartForImprovement = useCallback(async () => {
    // The completion declaration closed the session, so a new one must be opened. The backend's
    // _resolve_session_id finds handoff.json and branches to a new session_id + summary
    // injection, and the `__first__` sentinel is substituted with that handoff prompt
    // — so the prompt is not built here.
    //
    // The completion card appears at build_complete time — before the turn's done
    // (see the build_complete branch of applyEvent). During the 0–5 second window in between (the
    // backend grace timer limit) the agent can send more wrap-up text, so a state really exists
    // where the card is visible while stream A is still open (stopRef.current is non-null,
    // streaming is true). If the user presses the button in this window:
    // startSession succeeds, the backend discards A in its "complete" state, grabs one more slot
    // (B) and returns it, but the following startBuild() hits the `if (stopRef.current)
    // return;` guard (A never sent done, so stopRef is still
    // non-null) and never opens B's __first__ stream — B becomes a zombie holding the build slot
    // and subprocess until the 30-minute idle timeout. That
    // guard is not removed (the BuildPanel mount effect and send()'s duplicate-start
    // prevention depend on it) — instead A is ended here first so the guard no longer
    // blocks B.
    stopRef.current?.();
    stopRef.current = null;
    // Also lower streaming: otherwise, during startSession's network
    // round trip the screen keeps insisting it is "streaming" based on A, which was just closed
    // (the header's stop button remains, and so on). It becomes true again soon once startBuild()
    // is called, but until then it is right to reflect the state of the stream that just ended
    // — same reason as the cleanup finish() does on a normal end.
    setStreaming(false);
    //
    // Not catching startSession's exception is intentional. On a 429 (concurrent build limit)
    // the three lines below do not run, so the completion card stays, and the caller
    // (BuildPanel.handleRestart) shows the limit message. Swallowing it here would leave
    // a screen where the card is gone and nothing happened.
    await startSession(projectId, slug);
    setBuildComplete(null);
    setChangedPaths([]);
    // An improvement session is a new conversation — the previous session's usage does not
    // belong to this conversation. The first response provides a new value.
    setContext(null);
    startBuild();
  }, [projectId, slug, startBuild]);

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  // Emit only running rows: finished agents drop out of the pinned line and move on to the trace.
  const running = useMemo(() => runningAgents(agents), [agents]);

  return {
    items,
    streaming,
    agents: running,
    pendingQuestions,
    buildComplete,
    changedPaths,
    historyLoading,
    context,
    startBuild,
    restoreHistory,
    send,
    submitAnswers,
    interrupt,
    restartForImprovement,
    resume,
  };
}
