// frontend/lib/useWorkspaceStream.ts
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import { streamEvents, streamAnswers, streamFileAnswers, streamLive } from "@/lib/api/sse";
import { getPending, getHistory, interruptTurn } from "@/lib/api/client";
import { answerSummary } from "@/lib/answerSummary";
import { redirectIfSessionExpired } from "@/lib/auth/sessionRecovery";
import type { AgentEvent, HistoryItem, QuestionFile, QuestionsPayload, StagePayload, DocumentPayload,
  PrototypeReadyPayload } from "@/lib/api/types";
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
  // 그 라운드에서 실제로 물은 질문들(GET /history의 HistoryItem.questions).
  // 트랜스크립트의 tool_use.input에 구조화된 채로 남아 있어 복원할 수 있다 —
  // 종전에는 이것을 버려서 카드가 "질문 제시됨" 한 줄뿐이었다. 여전히
  // **읽기 전용**이다: 라이브 폼(QuestionCardSlot)이 아니다.
  file?: QuestionFile | null;
}
export type ChatItem = UserItem | AiItem | HistoryCardItem;

let counter = 0;
const nextId = () => `wf-item-${counter++}`;

// 턴 개시(POST)의 실패는 상태 코드를 준다 — EventSource의 익명 onerror와 달리
// 원인을 말할 수 있는 유일한 지점이다. 413/431은 "입력이 길다"는 뜻이고, 그
// 구분이 없으면 이 버그의 증상("연결이 끊어졌습니다")이 그대로 돌아온다.
function isTooLong(err: unknown): boolean {
  const status = (err as { status?: number } | null)?.status;
  return status === 431 || status === 413;
}

// 백엔드 claude_driver.INTERRUPTED_MARKER와 같은 값이어야 한다(proto/builder.py도
// 같은 값을 쓴다). 기계 신호이고 사람이 읽는 문구가 아니다 — 화면의 "중단됨"은
// 이 플래그를 받은 AiMessage가 UI 언어로 그린다.
const INTERRUPTED_MARKER = "interrupted";

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
  // Discovery가 빌드로 넘긴 프로토타입. 채팅에 "Prototypes 탭으로"
  // 카드를 띄우는 근거다(에이전트의 안내 문장에 의존하지 않는다).
  prototypeReady: PrototypeReadyPayload | null;
  changedPaths: string[];
  historyLoading: boolean;
  // 문서 패널이 따라가야 할 "지금 대화 중인 문서" — `document` 이벤트뿐 아니라
  // doc성 file_changed(아래 isDocPath)도 최신-승리로 추적한다. version은
  // `document` 이벤트에서 온 경우에만 채워진다 (ui-bug2 싱크 수정).
  //
  // 2026-08-21 이후 `document`는 모델이 부르던 `submit_document`가 아니라 백엔드가
  // 산출물 쓰기에서 **유도한다**(agent/reconcile.document_events). 그래서 이제 거의
  // 항상 도착하고, 아래 isDocPath 분기는 훅이 못 보는 쓰기(Bash 경유)의 백스톱으로
  // 남는다 — 그것이 이 필드가 처음 생긴 이유였다.
  activeDoc: { path: string; version: string | null } | null;
  // 턴이 끝날 때마다 증가 — 패널이 이 키로 문서를 다시 읽는다. 턴 도중
  // 도착한 document 이벤트 시점에는 VM→S3 동기화 전이라 읽기가 빈 내용/404가
  // 될 수 있고, 그대로 두면 재읽기가 영영 없다 (ui-bug2의 "비어 있음" 수정).
  turnSeq: number;
}

// 문서 패널이 따라갈 가치가 있는 산출물 경로인가 — aiplc-docs/ 아래 .md 중
// 기록성 파일(audit/state/질문)은 제외.
//
// 질문 파일(*-questions.md)이 audit/state와 같은 칸에 있는 이유: AI-PDS에서
// 질문의 전달 경로는 AskUserQuestion 도구이고, 사용자가 답하는 화면은 우측
// 패널의 QuestionForm이다(discovery-config/CLAUDE.md의 override 섹션). 마크다운
// 파일은 상류 룰이 요구하는 기록물로만 남는다.
//
// 그래서 이걸 문서 패널에 띄우면 한 화면에 같은 질문의 두 버전이 나란히 뜨는데,
// 그 둘은 애초에 일치할 수 없다: SDK 스키마가 질문 1-4개/보기 2-4개를 하드
// 제한하므로(CLI 2.1.226의 `questions: dt(...).min(1).max(4)`와 "hard schema
// constraints; do not exceed them even if the user requests more — split into
// multiple calls instead"), 룰이 요구하는 7문항 문서는 폼에서 4+3 두 라운드로
// 쪼개지고 문구도 각각 따로 생성된다. 실측으로 사용자가 그 불일치를 발견한
// 경로가 바로 이 패널이었다.
function isDocPath(path: string): boolean {
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

function historyItemToChatItem(it: HistoryItem): ChatItem {
  if (it.role === "card") {
    return { id: nextId(), role: "history-card", name: it.name,
             file: it.questions ?? null };
  }
  // answers와 questions를 그대로 옮긴다 — ChatTimeline이 UI 언어로 문구를
  // 만드는 데 쓴다. 여기서 버리면 백엔드의 한국어 폴백 문구가 영어 UI에 그대로
  // 뜨고, questions를 버리면 라이브와 같은 answerSummary를 부를 수 없어
  // "1: A" 나열로 떨어진다.
  if (it.role === "user") {
    return { id: nextId(), role: "user", text: it.text ?? "",
             answers: it.answers ?? null, questions: it.questions ?? null };
  }
  return {
    id: nextId(),
    role: "ai",
    text: it.text ?? "",
    // 복원된 도구 트레이스 — 라이브 턴의 status/file_changed 이벤트와 같은
    // shape이라 AiMessage의 "추론 과정" 아코디언이 그대로 렌더한다.
    trace: (it.trace ?? []).map((t) => ({
      kind: t.kind, text: t.text, path: t.path, detail: t.detail ?? null })),
    streaming: false,
    error: null,
  };
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
        if (ev.kind === "message") return { ...it, text: it.text + (ev.text ?? "") };
        // 중단은 turn의 종결 사유라 trace가 아니라 전용 필드로 간다.
        // 드라이버가 새 kind 대신 status로 흘리는 이유는 이미 다루는 이벤트
        // 모양을 재사용하기 위해서다(claude_driver.interrupt). 이 마커는
        // 라이브 스트림에만 있다 — 트랜스크립트에는 남지 않으므로 새로고침
        // 후에는 이 줄이 다시 나타나지 않는다.
        if (ev.kind === "status" && ev.text === INTERRUPTED_MARKER) {
          return { ...it, interrupted: true };
        }
        if (ev.kind === "status" || ev.kind === "file_changed") {
          // status의 detail은 payload에 실려 온다(리댁션을 지나는 필드여야 하고,
          // path는 구조적 필드로 취급되어 리댁션을 지나지 않는다 —
          // backend/aipds/tool_trace.py의 근거).
          const trace: TraceEntry = {
            kind: ev.kind, text: ev.text, path: ev.path,
            detail: safeParse<{ detail?: string }>(ev.payload)?.detail ?? null,
          };
          return { ...it, trace: [...it.trace, trace] };
        }
        if (ev.kind === "error") return { ...it, error: ev.text ?? t("stream.turnError") };
        return it; // "done" is handled by onDone
      });
    },
    [patchAi, t],
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
      // 재접속을 이 턴에서 이미 시도했는지. 턴 지역 변수인 것이 요점이다 —
      // 턴마다 한 번씩 기회를 주고, 한 턴 안에서는 루프가 되지 않는다.
      let reattempted = false;
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
        onError: (err) => {
          // 401(토큰 만료)과 네트워크 끊김을 EventSource가 구분해주지 않으므로
          // 세션을 확인해 만료면 로그인으로 보낸다. 살아 있으면 아래 메시지가 맞다.
          void redirectIfSessionExpired(undefined, window.location.pathname);
          const giveUp = () => {
            patchAi(aiId, (it) => ({
              ...it,
              streaming: false,
              error: it.error ?? t(isTooLong(err) ? "stream.tooLong" : "stream.disconnected"),
            }));
            finish();
          };
          // **한 번은 다시 붙어 본다(2026-08-19).** 절전·화면보호기로 끊긴
          // 경우 서버에서는 아무것도 잃지 않는다 — 에이전트는 계속 쓰고
          // `_MessageReader`가 계속 읽는다. 화면만 잃은 것이므로 이어서 볼 수
          // 있다. 턴이 2.5~5.6분이라 화면보호기 기본값(5~10분)과 정면으로
          // 겹치고, 그래서 이 경로가 예외가 아니라 일상이다.
          //
          // 한 번만 시도한다: 재시도 루프는 진짜 오프라인에서 스트림을 무한히
          // 다시 여는 것이 되고, 그때 화면은 "진행 중"으로 영구히 잠긴다.
          //
          // **개시 실패에는 시도하지 않는다.** `err`에 HTTP 상태가 있으면
          // `POST /turns`가 거절된 것이므로(431/413 등, openViaHandle의 catch)
          // 애초에 붙을 턴이 만들어지지 않았다. 시도해도 빈손으로 돌아오지만
          // 그 사이 원인 문구("입력이 너무 깁니다")가 늦어진다 — 431이
          // "연결이 끊어졌습니다"로 뭉개졌던 것이 이 파일이 이미 고친 버그다.
          const hasHttpStatus =
            typeof (err as { status?: unknown } | null)?.status === "number";
          if (reattempted || hasHttpStatus) return giveUp();
          reattempted = true;
          let sawFrame = false;
          stopRef.current = streamLive(projectId, {
            onEvent: (ev) => {
              sawFrame = true;
              applyEvent(aiId, ev);
            },
            onDone: () => {
              // 프레임이 하나도 없었다 = 붙을 턴이 없다(늦게 돌아왔고 그동안
              // 턴이 끝났다). 그때는 원래 메시지가 맞다 — 라이브 뷰는 실제로
              // 잃었고, `finish()`가 올리는 turnSeq가 문서 패널을 다시 읽게
              // 하므로 산출물은 화면에 돌아온다.
              if (!sawFrame) return giveUp();
              patchAi(aiId, (it) => ({ ...it, streaming: false }));
              finish();
            },
            onError: () => giveUp(),
          });
        },
      });
      if (finished) stop();
      else stopRef.current = stop;
    },
    [applyEvent, patchAi, t],
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
      // 어느 경로로 보낼지는 **지금** 정한다 — 아래 setPendingQuestions(null)이
      // 판별자를 없애기 때문이다.
      const file = pendingQuestions?.file;
      // 파킹된 턴 재개 경로(= AskUserQuestion 탈출로)의 폴백 텍스트. 그 라우트는
      // 질문 파일을 손에 들고 있지 않아 `summary`를 만들 수 없으므로 프론트 렌더러를
      // 쓴다. 문항을 **지금** 붙잡는 것이 요점이다 — 아래 setPendingQuestions(null)이
      // 그것을 없앤다.
      const localSummary = pendingQuestions
        ? answerSummary(pendingQuestions.questions, answers, t)
        : t("chat.answersSubmitted");
      setPendingQuestions(null);

      const aiId = nextId();
      const userId = nextId();
      setItems((prev) => [
        ...prev,
        // 텍스트는 **서버 응답이 도착하면** 채운다(아래 onCreated). 지금 비워 두는
        // 이유: 이 말풍선을 프론트가 만들던 동안 사용자가 본 것과 트랜스크립트에
        // 기록된 것이 서로 달랐다 — 렌더가 두 벌이었기 때문이다
        // (backend/aipds/answer_summary.py 헤더에 실측이 있다). 이제 서버가 만든
        // 문자열 하나가 화면과 기록 양쪽에 쓰인다.
        //
        // 자리를 미리 잡는 것은 순서를 위해서다: AI 말풍선보다 앞에 와야 한다.
        { id: userId, role: "user", text: "" },
        { id: aiId, role: "ai", text: "", trace: [], streaming: true, error: null },
      ]);
      const onCreated = (created: { summary?: string }) => {
        // `summary`가 없으면 파킹된 턴 재개 경로다 — 위 `localSummary`가 그 경로의
        // 렌더다. 마커로 떨어뜨리면 그 경로만 읽을 수 없게 되고, 복원은
        // `answer_store` 조인으로 실제 답변을 보여주므로 라이브와 복원이 **반대
        // 방향으로** 갈라진다(agent/answer_store.py).
        //
        // 프론트 렌더러가 남는 것은 이 경로 때문만이 아니다 — 프로토타입 빌더의
        // 스트림(usePrototypeStream)과 히스토리 복원(ChatTimeline)도 그것을 쓴다.
        // 그 둘까지 백엔드로 옮기면 이 폴백과 함께 모듈을 지울 수 있다.
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
    // 실패를 삼킨다: 중단은 보조 동작이고, 실패해도 턴은 그대로 돌아 화면이
    // 막히지 않는다. 사용자는 다시 누를 수 있다.
    try {
      await interruptTurn(projectId);
    } catch {
      /* 무시 */
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
    interrupt,
    pendingQuestions,
    stages,
    lastDocument,
    prototypeReady,
    changedPaths,
    historyLoading,
    activeDoc,
    turnSeq,
  };
}
