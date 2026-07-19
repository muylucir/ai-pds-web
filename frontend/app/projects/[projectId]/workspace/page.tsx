"use client";
import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { StageSidebar } from "@/components/workspace/StageSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { WorkspaceRightPanel } from "@/components/workspace/WorkspaceRightPanel";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { getState } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useWorkspaceStream } from "@/lib/useWorkspaceStream";

// The 3-pane workspace screen (Task 11) that replaces the separate
// questions/canvas tabs — grid ratio 1:4.5:4.5 (좌 스테이지 : 중앙 채팅 : 우
// 컨텍스트) per the approved spec. Below the `lg` breakpoint the side/right
// panels are hidden (WorkspaceRightPanel/StageSidebar are `hidden lg:flex`
// internally); a pending-questions badge over the chat opens a bottom-sheet
// that reuses the SAME QuestionForm widget the right panel would otherwise
// show for that mode (mode priority: questions > preview > artifacts).
export default function WorkspacePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const { items, streaming, send, submitAnswers, pendingQuestions, stages, lastDocument, changedPaths } =
    useWorkspaceStream(projectId);
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  // Dismissible document-update notice (spec §5): track which version the
  // user has already dismissed so a LATER update (new version) re-shows the
  // banner even if an earlier one was dismissed.
  const [dismissedDocVersion, setDismissedDocVersion] = useState<string | null>(null);
  const showDocBanner = lastDocument != null && lastDocument.version !== dismissedDocVersion;

  function submitAnswersFromSheet(answers: Record<string, string>) {
    submitAnswers(answers);
    setSheetOpen(false);
  }

  // Minimal accessibility for the mobile bottom-sheet: move focus into the
  // dialog when it opens (so screen-reader/keyboard users land inside it,
  // not on the badge button that's now behind an overlay), and close on
  // Escape (the standard dismiss gesture for a modal). Full focus-trap +
  // focus-restore-to-trigger is a deliberate follow-up, not done here.
  useEffect(() => {
    if (!sheetOpen) return;
    sheetRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSheetOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [sheetOpen]);

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <AppHeader activeTab="workspace" projectId={projectId} />
      <div className="flex-1 grid min-h-0 grid-cols-1 lg:grid-cols-[1fr_4.5fr_4.5fr]">
        <StageSidebar state={state.data} events={stages} />

        <main className="relative flex flex-col min-w-0 bg-slate-50">
          {showDocBanner && lastDocument && (
            <div
              role="status"
              className="m-3 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 flex items-center justify-between gap-3 text-sm"
            >
              <span className="text-violet-900">
                문서가 갱신되었습니다 (v{lastDocument.version}){" "}
                <Link
                  href={`/projects/${projectId}/review`}
                  className="font-medium text-violet-700 underline hover:text-violet-900"
                >
                  문서 리뷰
                </Link>
              </span>
              <button
                type="button"
                aria-label="닫기"
                onClick={() => setDismissedDocVersion(lastDocument.version)}
                className="shrink-0 text-violet-400 hover:text-violet-600"
              >
                ✕
              </button>
            </div>
          )}
          {pendingQuestions && (
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="lg:hidden absolute top-3 right-3 z-10 px-3 py-1.5 rounded-full bg-violet-600 text-white text-xs font-bold shadow-lg"
            >
              답변 대기 중인 질문 →
            </button>
          )}
          <ChatTimeline
            items={items}
            projectId={projectId}
            onChoose={send}
            onOpenArtifact={() => {}}
            busy={streaming}
          />
          <ChatInput onSend={send} disabled={streaming} />
        </main>

        <WorkspaceRightPanel
          projectId={projectId}
          pendingQuestions={pendingQuestions}
          stages={stages}
          changedPaths={changedPaths}
          onSubmitAnswers={submitAnswers}
          busy={streaming}
        />
      </div>

      {sheetOpen && pendingQuestions && (
        <div
          className="lg:hidden fixed inset-0 z-30 bg-slate-900/40 flex flex-col justify-end"
          onClick={() => setSheetOpen(false)}
        >
          <div
            ref={sheetRef}
            role="dialog"
            aria-modal="true"
            aria-label="질문 답변 시트"
            tabIndex={-1}
            className="bg-white rounded-t-2xl max-h-[85vh] overflow-y-auto p-6 focus:outline-none"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => setSheetOpen(false)}
              className="mb-3 text-xs text-slate-400"
              aria-label="닫기"
            >
              닫기 ✕
            </button>
            <QuestionForm
              file={pendingQuestions.questions}
              onSubmit={submitAnswersFromSheet}
              submitting={streaming}
            />
          </div>
        </div>
      )}
    </div>
  );
}
