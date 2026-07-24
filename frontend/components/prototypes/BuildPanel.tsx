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
import { closeSession } from "@/lib/api/prototypes";
import { usePrototypeStream } from "@/lib/usePrototypeStream";

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
  const { items, streaming, pendingQuestions, changedPaths, startBuild, send, submitAnswers, interrupt } =
    usePrototypeStream(projectId, slug);
  const [closing, setClosing] = useState(false);
  const [submittingAnswers, setSubmittingAnswers] = useState(false);

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
    } finally {
      setClosing(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-900/40 flex items-stretch justify-end">
      {/* 드로어 폭: 빌드 로그·질문 폼·파일 변경 목록을 함께 읽어야 해서
          기존 3xl(48rem)의 두 배인 7xl(80rem)까지 넓힌다. 좁은 화면에서는
          w-full이 그대로 뷰포트를 채운다. */}
      <div className="w-full max-w-7xl h-full bg-white flex flex-col min-h-0 shadow-2xl">
        <header className="shrink-0 border-b border-slate-200 px-4 md:px-6 py-3 flex items-center justify-between gap-3">
          <h1 className="font-bold text-lg truncate">{slug}</h1>
          <div className="flex items-center gap-2 shrink-0">
            {streaming && (
              <button
                type="button"
                onClick={() => void interrupt()}
                className="px-3.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 text-sm font-medium text-slate-700"
              >
                중단
              </button>
            )}
            <button
              type="button"
              onClick={handleDone}
              disabled={closing}
              className="px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-medium"
            >
              완료
            </button>
          </div>
        </header>

        {/* 채팅 1/3, 우측(질문·파일 변경) 2/3. basis+min-w-0으로 비율을 고정한다
            — aside가 고정 폭이면 넓어진 드로어의 여유 폭이 전부 채팅으로 갔다.
            좁은 화면(md 미만)에서는 세로로 쌓이므로 비율은 적용되지 않는다. */}
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <div className="flex-1 md:flex-none md:basis-1/3 md:min-w-0 min-h-0 flex flex-col">
            <ChatTimeline
              items={items}
              projectId={projectId}
              onChoose={send}
              onOpenArtifact={() => {}}
              busy={streaming}
            />
            <ChatInput onSend={send} disabled={streaming} />
          </div>

          <aside className="w-full md:basis-2/3 md:min-w-0 shrink-0 border-t md:border-t-0 md:border-l border-slate-200 flex flex-col min-h-0 overflow-y-auto">
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
              <p className="text-xs font-bold text-slate-400 uppercase tracking-wide mb-3">파일 변경 목록</p>
              {changedPaths.length === 0 ? (
                <p className="text-sm text-slate-400">아직 변경된 파일이 없습니다.</p>
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
