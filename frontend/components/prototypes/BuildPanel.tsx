// frontend/components/prototypes/BuildPanel.tsx — the prototype build chat
// panel: opened from the prototypes tab grid when a card's build session is
// live. Reuses the SAME chat rendering (ChatTimeline/AiMessage/ChatInput) and
// question wizard (QuestionForm) as the workspace screen — only the stream
// hook (usePrototypeStream) and the surrounding chrome are new.
"use client";
import { useEffect, useState } from "react";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { closeSession, startHost } from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import { usePrototypeStream } from "@/lib/usePrototypeStream";
import { useT } from "@/lib/i18n/provider";

export function BuildPanel({
  projectId,
  slug,
  onClose,
  autoStart = false,
}: {
  projectId: string;
  slug: string;
  onClose: () => void;
  // Fire the auto first-build turn ("__first__") on mount — ONLY true right
  // after this session was newly created (a POST /session 202). A 409
  // reopen of an already-live session must NOT re-fire it (the harness would
  // otherwise receive a second "turn already in progress" style conflict).
  autoStart?: boolean;
}) {
  const t = useT();
  const {
    items, streaming, pendingQuestions, buildComplete, changedPaths,
    startBuild, send, submitAnswers, interrupt, restartForImprovement,
  } = usePrototypeStream(projectId, slug);
  const [closing, setClosing] = useState(false);
  const [submittingAnswers, setSubmittingAnswers] = useState(false);
  const [hosting, setHosting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (autoStart) startBuild();
    // Mount-only — startBuild must fire at most once per panel lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmitAnswers(answers: Record<string, string>) {
    setSubmittingAnswers(true);
    try {
      await submitAnswers(answers);
    } finally {
      setSubmittingAnswers(false);
    }
  }

  async function handleDone() {
    setClosing(true);
    try {
      await closeSession(projectId, slug);
      onClose();
    } catch (err) {
      // 404는 정상 경로다: 완료 선언 뒤 백엔드가 유예 타이머로 세션을 먼저
      // 닫는다(proto/session.py의 _COMPLETION_GRACE_SECONDS). 이미 없는
      // 세션을 못 닫았다고 패널을 붙잡아 둘 이유가 없다.
      if (err instanceof ApiError && err.status === 404) {
        onClose();
        return;
      }
      throw err;
    } finally {
      setClosing(false);
    }
  }

  async function handleStartHost() {
    setHosting(true);
    setActionError(null);
    try {
      await startHost(projectId, slug);
      onClose();
    } catch (err) {
      // 패널을 닫지 않는다 — 닫으면 사용자는 그리드에서 이유 없이 실패한
      // 카드를 보게 된다. 여기서 오류를 보여주고 재시도할 수 있게 둔다.
      setActionError(
        err instanceof ApiError && err.message
          ? err.message
          : t("proto.hostStartFailed"));
    } finally {
      setHosting(false);
    }
  }

  async function handleRestart() {
    setRestarting(true);
    setActionError(null);
    try {
      await restartForImprovement();
    } catch (err) {
      // 429(동시 빌드 상한)가 실제로 도달 가능한 경로다. 카드를 지우지
      // 않는다 — 지우면 사용자는 완료 요약과 호스팅 선택지를 모두 잃는다.
      // actionError를 호스팅과 공유한다: 이 카드에 오류 줄은 하나뿐이고, 두
      // 동작이 동시에 실패할 수는 없다(둘 다 서로를 disabled로 막는다).
      setActionError(
        err instanceof ApiError && err.message
          ? err.message
          : t("proto.improveStartFailed"));
    } finally {
      setRestarting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-900/40 flex items-stretch justify-end">
      {/* 드로어 폭: 빌드 로그·질문 폼·파일 변경 목록을 함께 읽어야 해서
          7xl(80rem)에서 1720px까지 넓힌다. 리뷰 화면(review/page.tsx)이 쓰는
          것과 같은 폭이다. 좁은 화면에서는 w-full이 그대로 뷰포트를 채운다. */}
      <div className="w-full max-w-[1720px] h-full bg-white flex flex-col min-h-0 shadow-2xl">
        <header className="shrink-0 border-b border-slate-200 px-4 md:px-6 py-3 flex items-center justify-between gap-3">
          <h1 className="font-bold text-lg truncate">{slug}</h1>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={handleDone}
              disabled={closing}
              className="px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-medium"
            >
              {t("proto.done")}
            </button>
          </div>
        </header>

        {/* 채팅과 우측(질문·파일 변경)을 1:1로 나눈다. 종전 1/3:2/3에서는 빌드
            로그가 좁아 코드 블록과 도구 출력이 계속 줄바꿈됐다 — 질문 폼과 같은
            폭을 줘서 둘을 나란히 읽을 수 있게 한다. basis+min-w-0으로 비율을
            고정한다: aside가 고정 폭이면 넓어진 드로어의 여유 폭이 전부 채팅으로
            간다. 좁은 화면(md 미만)에서는 세로로 쌓이므로 비율은 적용되지 않는다.
            **두 basis의 합은 정확히 1이어야 한다** — 둘 다 shrink-0/flex-none이라
            넘치면 flex가 되돌려주지 않고 그대로 잘린다. 테스트가 이 합을 고정한다. */}
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <div className="flex-1 md:flex-none md:basis-1/2 md:min-w-0 min-h-0 flex flex-col">
            <ChatTimeline
              items={items}
              projectId={projectId}
              onChoose={send}
              onOpenArtifact={() => {}}
              busy={streaming}
            />
            {/* 완료 선언 뒤에는 이 세션이 곧(유예 5초) 닫히거나 이미 닫혀
                있다 — 입력을 계속 열어두면 사용자가 보낸 메시지가
                GET .../events의 404로 이어지고, usePrototypeStream의
                onError가 "연결이 끊어졌습니다"를 띄운다(실제로는 빌드가
                끝난 것뿐이다). 입력을 막고, 대신 오른쪽 완료 카드의 버튼을
                가리킨다. */}
            {buildComplete !== null && (
              <p className="shrink-0 px-4 md:px-8 pt-2 text-xs text-slate-400 text-center">
                {t("proto.sessionClosedNotice")}
              </p>
            )}
            <ChatInput
              onSend={send}
              disabled={streaming || buildComplete !== null}
              onInterrupt={() => void interrupt()}
              interrupting={streaming}
            />
          </div>

          {/* basis-1/2 — 채팅과 합이 정확히 100%여야 한다. 종전 2/3은 채팅을
              1/3에서 1/2로 넓힐 때(70783c0) 함께 고치지 않은 잔재였고, 합이
              7/6이 되어 1720px 드로어에서 287px가 넘쳐 보기 텍스트의 오른쪽이
              스크롤바도 없이 잘렸다(실측). shrink-0이라 flex가 되돌려주지도
              않는다. */}
          <aside className="w-full md:basis-1/2 md:min-w-0 shrink-0 border-t md:border-t-0 md:border-l border-slate-200 flex flex-col min-h-0 overflow-y-auto">
            {buildComplete && (
              <div className="p-4 border-b border-slate-200">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                  <p className="text-sm font-bold text-emerald-800">{t("proto.buildComplete")}</p>
                  <p className="mt-2 text-sm text-slate-700 whitespace-pre-wrap">
                    {buildComplete.summary}
                  </p>
                  {buildComplete.remaining && (
                    <>
                      <p className="mt-3 text-xs font-bold text-slate-500">{t("proto.remainingWork")}</p>
                      <p className="mt-1 text-sm text-slate-600 whitespace-pre-wrap">
                        {buildComplete.remaining}
                      </p>
                    </>
                  )}
                </div>
                {actionError && (
                  <p className="mt-3 text-sm text-rose-600">{actionError}</p>
                )}
                {/* build_complete는 done보다 먼저 서므로, 카드가 뜬 뒤에도
                    에이전트가 마무리 텍스트를 보내는 0~5초 창(백엔드 유예
                    타이머 한도) 동안 streaming이 true로 남을 수 있다. 그
                    창에서 개선/호스팅/닫기 중 하나가 눌리면 아직 열려 있는
                    스트림과 새 동작이 뒤엉킨다(개선 이어서 하기가 세션 B를
                    새로 여는 동안 세션 A의 스트림이 정리되지 않는 경합이
                    실제 사례) — 세 버튼 모두 streaming 중엔 막는다. */}
                {streaming && (
                  <p className="mt-3 text-xs text-slate-400">
                    {t("proto.finishingBuild")}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleStartHost()}
                    disabled={hosting || restarting || closing || streaming}
                    className="px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-medium"
                  >
                    {t("proto.startHosting")}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleRestart()}
                    disabled={hosting || restarting || closing || streaming}
                    className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 text-sm font-medium text-slate-700"
                  >
                    {t("proto.continueImproving")}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDone()}
                    disabled={hosting || restarting || closing || streaming}
                    className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 text-sm font-medium text-slate-700"
                  >
                    {t("proto.close")}
                  </button>
                </div>
              </div>
            )}
            {pendingQuestions && (
              <div className="p-4 border-b border-slate-200">
                <QuestionForm
                  file={pendingQuestions.questions}
                  onSubmit={handleSubmitAnswers}
                  submitting={submittingAnswers}
                />
              </div>
            )}
            <div className="p-4">
              <p className="text-xs font-bold text-slate-400 uppercase tracking-wide mb-3">{t("proto.changedFiles")}</p>
              {changedPaths.length === 0 ? (
                <p className="text-sm text-slate-400">{t("proto.noChangedFiles")}</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {changedPaths.map((path) => (
                    <li key={path} className="rounded-lg border border-slate-200 px-3 py-2 text-slate-600 break-all">
                      {path}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
