// frontend/lib/usePrototypeStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import {
  streamPrototypeEvents,
  submitPrototypeAnswers,
  interruptSession,
  startSession,
  FIRST_TURN_SENTINEL,
} from "@/lib/api/prototypes";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import { answerSummary } from "@/lib/answerSummary";
import type { AgentEvent } from "@/lib/api/types";
import type { QuestionsPayload, BuildCompletePayload } from "@/lib/api/types";
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

// 턴 개시(POST)의 실패는 상태 코드를 준다 — EventSource의 익명 onerror와 달리
// 원인을 말할 수 있는 유일한 지점이다. 413/431은 "입력이 길다"는 뜻이고, 그
// 구분이 없으면 이 버그의 증상("연결이 끊어졌습니다")이 그대로 돌아온다.
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
  pendingQuestions: QuestionsPayload | null;
  /** 에이전트가 빌드 완료를 선언했을 때의 요약. 이 값이 있으면 세션은 이미
   *  닫혔거나 몇 초 안에 닫힌다(백엔드가 유예 타이머로 닫는다). */
  buildComplete: BuildCompletePayload | null;
  changedPaths: string[];
  startBuild: () => void;
  send: (text: string) => void;
  submitAnswers: (answers: Record<string, string>) => Promise<void>;
  interrupt: () => Promise<void>;
  /** 완료된 빌드를 개선한다: 새 세션을 열고 개시 턴을 발화한다. 서버가
   *  `__first__`를 핸드오프 프롬프트로 치환하므로 새 API가 필요 없다. */
  restartForImprovement: () => Promise<void>;
}

export function usePrototypeStream(projectId: string, slug: string): PrototypeStream {
  const t = useT();
  const [items, setItems] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<QuestionsPayload | null>(null);
  const [buildComplete, setBuildComplete] = useState<BuildCompletePayload | null>(null);
  const [changedPaths, setChangedPaths] = useState<string[]>([]);
  const stopRef = useRef<null | (() => void)>(null);
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

  const patchAi = useCallback((aiId: string, fn: (it: AiItem) => AiItem) => {
    setItems((prev) => prev.map((it) => (it.id === aiId && it.role === "ai" ? fn(it) : it)));
  }, []);

  // Seal whatever bubble is current and open a fresh one, optionally with
  // items (a user bubble) between them. The single place a bubble is born:
  // turn start, an answers roundtrip, and a mid-turn tool boundary all go
  // through here, so the ref/flag bookkeeping can't drift between them.
  const openAiBubble = useCallback((between: ChatItem[] = []): string => {
    const prevId = currentAiIdRef.current;
    const aiId = nextId();
    currentAiIdRef.current = aiId;
    splitArmedRef.current = false;
    hasTextRef.current = false;
    setItems((prev) => [
      // The sealed bubble stops streaming, which moves the typing dots and the
      // live activity line (AiMessage.tsx) onto the new one.
      ...prev.map((it) => (it.id === prevId && it.role === "ai" ? { ...it, streaming: false } : it)),
      ...between,
      { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
    ]);
    return aiId;
  }, []);

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
      if (ev.kind === "file_changed" && ev.path) {
        setChangedPaths((prev) => (prev.includes(ev.path as string) ? prev : [...prev, ev.path as string]));
      }
      if (ev.kind === "questions") {
        const parsed = safeParse<QuestionsPayload>(ev.payload);
        if (parsed) setPendingQuestions(parsed);
        return;
      }
      if (ev.kind === "build_complete") {
        // streaming을 건드리지 않는다 — 뒤따르는 `done`이 onDone으로 평소대로
        // 턴을 닫는다. 백엔드는 이 선언 뒤 유예 타이머로 세션을 닫으므로,
        // 이 시점부터 send()는 더 이상 유효하지 않다.
        const parsed = safeParse<BuildCompletePayload>(ev.payload);
        if (parsed) setBuildComplete(parsed);
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
      } else if (ev.kind === "status" || ev.kind === "file_changed") {
        // Arm only once the bubble has said something — a tool before the
        // first word (every turn opens that way) must not split off an empty
        // bubble. The tool itself is traced on the bubble it interrupted, so
        // "추론 과정" stays attached to the text it belongs to.
        if (hasTextRef.current) splitArmedRef.current = true;
      }
      patchAi(target, (it) => {
        if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
        if (ev.kind === "status" || ev.kind === "file_changed") {
          const trace: TraceEntry = { kind: ev.kind, text: ev.text, path: ev.path };
          return { ...it, trace: [...it.trace, trace] };
        }
        if (ev.kind === "error") return { ...it, error: ev.text ?? t("stream.buildError") };
        return it; // "done" is handled by onDone
      });
    },
    [patchAi, openAiBubble, t],
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
        // Drop a bubble the turn never filled. submitAnswers opens one eagerly
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
      };
      // Every handler resolves the target bubble through the ref (falling back
      // to the id this turn opened with) rather than closing over `aiId`: an
      // answers roundtrip repoints it mid-stream, and `done`/`error` must end
      // the bubble the user is actually watching.
      const liveId = () => currentAiIdRef.current ?? aiId;
      const stop = opener({
        onEvent: (ev) => applyEvent(liveId(), ev),
        onDone: () => {
          patchAi(liveId(), (it) => ({ ...it, streaming: false }));
          finish();
        },
        onError: (err) => {
          // 401(토큰 만료)과 네트워크 끊김을 EventSource가 구분해주지 않으므로
          // 세션을 확인해 만료면 로그인으로 보낸다. 살아 있으면 아래 메시지가 맞다.
          void redirectIfSessionExpired(undefined, window.location.pathname);
          patchAi(liveId(), (it) => ({
            ...it,
            streaming: false,
            error: it.error ?? t(isTooLong(err) ? "stream.tooLong" : "stream.disconnected"),
          }));
          setPendingQuestions(null); // same defensive clear as the error-kind path above
          finish();
        },
      });
      if (finished) stop();
      else stopRef.current = stop;
    },
    [applyEvent, patchAi, t],
  );

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
        // already open. But a new BUBBLE, because the reply belongs after the
        // user's answer: folding it into the pre-question bubble grew a single
        // bubble for the whole build and printed the agent's post-approval
        // text above the answer that triggered it (files/proto.png).
        openAiBubble([{ id: nextId(), role: "user", text: summary }]);
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
    // 완료 선언으로 세션이 닫혔으므로 새로 열어야 한다. 백엔드의
    // _resolve_session_id가 handoff.json을 발견해 새 session_id + 요약
    // 주입으로 분기하고, `__first__` 센티넬이 그 핸드오프 프롬프트로
    // 치환된다 — 그래서 여기서 프롬프트를 만들지 않는다.
    //
    // completion 카드는 build_complete 시점에 뜬다 — 턴의 done보다 먼저다
    // (applyEvent의 build_complete 분기 참고). 그 사이 0~5초 창(백엔드 유예
    // 타이머 한도) 동안 에이전트가 마무리 텍스트를 더 보낼 수 있어, 카드가
    // 보이면서도 스트림 A는 아직 열려 있는(stopRef.current가 non-null,
    // streaming이 true인) 상태가 실재한다. 이 창에서 사용자가 버튼을 누르면:
    // startSession이 성공해 백엔드가 "complete" 상태인 A를 버리고 슬롯을 하나
    // 더 잡아(B) 반환하는데, 뒤이은 startBuild()는 `if (stopRef.current)
    // return;` 가드에 걸려(A가 done을 보낸 적이 없어 stopRef가 여전히
    // non-null) B의 __first__ 스트림을 영영 열지 못한다 — B가 빌드 슬롯과
    // 서브프로세스를 쥔 채 30분 유휴 타임아웃까지 남는 좀비가 된다. 그
    // 가드는 지우지 않는다(BuildPanel 마운트 이펙트와 send()의 중복 시작
    // 방지가 여기 걸려 있다) — 대신 A를 여기서 먼저 끝내 가드가 더 이상 B를
    // 막지 못하게 한다.
    stopRef.current?.();
    stopRef.current = null;
    // streaming도 같이 내린다: 안 그러면 startSession의 네트워크
    // 라운드트립 동안 화면은 방금 닫아버린 A를 근거로 "스트리밍 중"이라고
    // 계속 우긴다(헤더의 중단 버튼이 남는 등). startBuild()가 호출되면 곧
    // 다시 true가 되지만, 그 전까지는 지금 끝난 스트림 상태를 반영하는 게
    // 맞다 — finish()가 정상 종료 때 하는 정리와 같은 이유다.
    setStreaming(false);
    //
    // startSession의 예외를 잡지 않는 것이 의도다. 429(동시 빌드 상한)면
    // 아래 세 줄이 실행되지 않아 완료 카드가 그대로 남고, 호출자
    // (BuildPanel.handleRestart)가 상한 메시지를 보여준다. 여기서 삼키면
    // 카드가 지워진 채 아무 일도 일어나지 않은 화면이 된다.
    await startSession(projectId, slug);
    setBuildComplete(null);
    setChangedPaths([]);
    startBuild();
  }, [projectId, slug, startBuild]);

  // Close the stream if the component unmounts mid-turn.
  useEffect(() => () => stopRef.current?.(), []);

  return {
    items,
    streaming,
    pendingQuestions,
    buildComplete,
    changedPaths,
    startBuild,
    send,
    submitAnswers,
    interrupt,
    restartForImprovement,
  };
}
