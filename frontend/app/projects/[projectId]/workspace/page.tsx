"use client";
import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { StageSidebar } from "@/components/workspace/StageSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { HistorySkeleton } from "@/components/canvas/HistorySkeleton";
import { ChatInput } from "@/components/canvas/ChatInput";
import { WorkspaceRightPanel } from "@/components/workspace/WorkspaceRightPanel";
import { WorkspaceDocPanel } from "@/components/workspace/WorkspaceDocPanel";
import { WelcomeCard } from "@/components/workspace/WelcomeCard";
import { AttachmentChips } from "@/components/workspace/AttachmentChips";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { getState, uploadFile } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useWorkspaceStream } from "@/lib/useWorkspaceStream";
import { useT } from "@/lib/i18n/provider";

// The 4-pane workspace screen — grid ratio 1:3.5:3.5:4 (좌 스테이지 : 채팅 :
// 컨텍스트 : 생성 문서). The 4th column (WorkspaceDocPanel) renders the latest
// generated document INLINE so the user reviews it without leaving the
// workspace for the review route; the chat + question panel narrow to make
// room. Below the `lg` breakpoint every side panel is hidden
// (StageSidebar/WorkspaceRightPanel/WorkspaceDocPanel are `hidden lg:flex`
// internally), leaving a single-column chat; a pending-questions badge over
// the chat opens a bottom-sheet that reuses the SAME QuestionForm widget the
// right panel would otherwise show (mode priority: questions > preview >
// artifacts), and the document-update banner links out to the review route.
export default function WorkspacePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const t = useT();
  const { modelLabel, language } = useProjectMeta(projectId);
  const { items, streaming, send, submitAnswers, interrupt, pendingQuestions, stages, lastDocument, prototypeReady, changedPaths, historyLoading, activeDoc, turnSeq } =
    useWorkspaceStream(projectId);
  // Show the Path A/B welcome starter only once history has finished loading
  // (avoids a flash of the welcome card before restored history arrives) AND
  // the timeline is genuinely empty — a pending interrupt or an in-flight
  // turn means the conversation has already started, so the welcome card
  // must not reappear over it.
  const showWelcome = !historyLoading && items.length === 0 && !pendingQuestions && !streaming;
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  // Dismissible document-update notice (spec §5): track which version the
  // user has already dismissed so a LATER update (new version) re-shows the
  // banner even if an earlier one was dismissed.
  const [dismissedDocVersion, setDismissedDocVersion] = useState<string | null>(null);
  const showDocBanner = lastDocument != null && lastDocument.version !== dismissedDocVersion;
  // File-attachment state (Task 8): uploaded paths waiting to be mentioned in
  // the NEXT outgoing message, and any upload-failure notice to surface.
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  // Bumped right before every send/submitAnswers call site — ChatTimeline
  // watches this to force an unconditional scroll-to-bottom on send, even if
  // the user had scrolled up and stick-to-bottom was off.
  const [stickSignal, setStickSignal] = useState(0);
  // Revise-request draft (Task 5): the review screen's "수정 요청" link routes
  // here with ?draft=<text> instead of opening an inline form (invisible once
  // scrolled, and unable to render follow-up questions). Next 15's
  // useSearchParams() would require a Suspense boundary in this already-
  // "use client" page, and nothing else in this codebase mocks
  // next/navigation's router/search-params hooks for tests, so we parse the
  // URL directly — but only inside a mount effect, never during the
  // useState initializer/render. Reading window.location during render would
  // make server HTML (no draft) and the client's first render (prefilled
  // textarea) diverge, which React 19 flags as a hydration mismatch. Applying
  // the draft post-mount means the FIRST paint always matches SSR (empty),
  // and the draft arrives a tick later; ChatInput is remounted (key={draft})
  // so its initialText+focus-on-mount contract still fires once the draft is
  // known. Once read, strip ?draft= from the URL so a refresh doesn't
  // re-prefill the input.
  const [draft, setDraft] = useState<string | undefined>(undefined);
  useEffect(() => {
    const d = new URLSearchParams(window.location.search).get("draft");
    if (d) {
      setDraft(d);
      window.history.replaceState(null, "", `/projects/${projectId}/workspace`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function submitAnswersFromSheet(answers: Record<string, string>) {
    setStickSignal((n) => n + 1);
    submitAnswers(answers);
    setSheetOpen(false);
  }

  async function handleAttach(file: File) {
    setUploadError(null);
    try {
      const r = await uploadFile(projectId, file);
      setAttachments((prev) => [...prev, r.path]);
    } catch {
      setUploadError(t("page.uploadFailed"));
    }
  }

  // Prepends a "[첨부 파일: ...]" mention line per pending attachment ahead of
  // the user's typed text, then clears the chip tray — attachments are a
  // one-shot mention on the NEXT message, not a standing context.
  function sendWithAttachments(text: string) {
    setStickSignal((n) => n + 1);
    const mentions = attachments.map(
      (p) => `[첨부 파일: ${p} — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 이 파일을 읽어보세요.]`,
    );
    send(mentions.length ? `${mentions.join("\n")}\n\n${text}` : text);
    setAttachments([]);
  }

  // Every other place `send`/`submitAnswers` is invoked (starter buttons,
  // in-chat answer choices, the right-panel question form) also counts as
  // "the user sent a message" for stick-to-bottom purposes — wrap rather
  // than pass the raw hook function straight into a prop.
  function sendAndStick(text: string) {
    setStickSignal((n) => n + 1);
    send(text);
  }

  function submitAnswersAndStick(answers: Record<string, string>) {
    setStickSignal((n) => n + 1);
    submitAnswers(answers);
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
    // relative: 심층 방어 — 후손의 absolute 요소(sr-only 등)가 static 조상
    // 체인을 타고 문서 루트 기준으로 배치되면 <html>에 유령 오버플로가 생겨
    // 포커스 이동만으로 헤더가 말려 올라간다(ui-bug.png). 루트를 포지셔닝
    // 컨텍스트로 만들어 이 클래스의 버그를 페이지 차원에서 차단한다.
    <div className="relative h-screen flex flex-col overflow-hidden">
      <AppHeader activeTab="workspace" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} />
      <div className="flex-1 grid min-h-0 grid-cols-1 lg:grid-cols-[1fr_3.5fr_3.5fr_4fr]">
        <StageSidebar state={state.data} events={stages} />

        <main className="relative flex flex-col min-w-0 min-h-0 bg-slate-50">
          {/* Discovery가 프로토타입을 빌드로 넘겼을 때의 어포던스.
              **에이전트의 안내 문장에 의존하지 않는다** — 2026-08-17까지는 그
              문장이 나오지 않아 사용자가 Discovery에서 막혔다(실측 keumkang-v5:
              Prototypes 탭 안내 0회). 닫기 버튼을 두지 않은 것도 의도다: 다음
              단계로 가는 유일한 문이므로 실수로 닫으면 다시 막힌다. */}
          {prototypeReady && (
            <div
              role="status"
              className="m-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 flex items-center justify-between gap-3 text-sm"
            >
              <span className="text-emerald-900">
                {t("page.prototypeReady")}
              </span>
              <Link
                href={`/projects/${projectId}/prototypes`}
                className="shrink-0 rounded-lg bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-700"
              >
                {t("page.prototypeBuildCta")}
              </Link>
            </div>
          )}
          {showDocBanner && lastDocument && (
            <div
              role="status"
              className="m-3 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 flex items-center justify-between gap-3 text-sm"
            >
              <span className="text-violet-900">
                {t("page.docUpdated").replace("{version}", lastDocument.version)}{" "}
                <Link
                  href={`/projects/${projectId}/review`}
                  className="font-medium text-violet-700 underline hover:text-violet-900"
                >
                  {t("page.docReview")}
                </Link>
              </span>
              <button
                type="button"
                aria-label={t("page.close")}
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
              {t("page.pendingQuestions")}
            </button>
          )}
          {showWelcome ? (
            <div className="flex-1 min-h-0 overflow-y-auto">
              <WelcomeCard onStart={sendAndStick} language={language} />
            </div>
          ) : (
            <>
              {historyLoading && <HistorySkeleton />}
              <ChatTimeline
                items={items}
                projectId={projectId}
                onChoose={sendAndStick}
                onOpenArtifact={() => {}}
                busy={streaming}
                stickSignal={stickSignal}
                historyLoading={historyLoading}
              />
            </>
          )}
          {uploadError && (
            <p role="alert" className="px-4 md:px-8 pb-1 text-xs text-red-600">
              {uploadError}
            </p>
          )}
          <AttachmentChips
            paths={attachments}
            onRemove={(p) => setAttachments((prev) => prev.filter((x) => x !== p))}
          />
          <ChatInput
            key={draft ?? "no-draft"}
            onSend={sendWithAttachments}
            onAttach={handleAttach}
            disabled={streaming}
            initialText={draft}
            onInterrupt={() => void interrupt()}
            interrupting={streaming}
          />
        </main>

        <WorkspaceRightPanel
          projectId={projectId}
          pendingQuestions={pendingQuestions}
          stages={stages}
          changedPaths={changedPaths}
          onSubmitAnswers={submitAnswersAndStick}
          busy={streaming}
        />

        <WorkspaceDocPanel projectId={projectId} activeDoc={activeDoc} turnSeq={turnSeq} />
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
            aria-label={t("page.answerSheetLabel")}
            tabIndex={-1}
            className="bg-white rounded-t-2xl max-h-[85vh] overflow-y-auto p-6 focus:outline-none"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => setSheetOpen(false)}
              className="mb-3 text-xs text-slate-400"
              aria-label={t("page.close")}
            >
              {t("page.closeWithMark")}
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
