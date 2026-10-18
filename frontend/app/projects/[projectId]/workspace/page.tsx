"use client";
import { use, useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { StageSidebar } from "@/components/workspace/StageSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { HistorySkeleton } from "@/components/canvas/HistorySkeleton";
import { ChatInput } from "@/components/canvas/ChatInput";
import { LiveActivityBar } from "@/components/canvas/LiveActivityBar";
import { WorkspaceRightPanel } from "@/components/workspace/WorkspaceRightPanel";
import { WorkspaceDocPanel } from "@/components/workspace/WorkspaceDocPanel";
import { OpenDocButton } from "@/components/workspace/OpenDocButton";
import { WelcomeCard } from "@/components/workspace/WelcomeCard";
import { AttachmentChips } from "@/components/workspace/AttachmentChips";
import { AttachFilesModal } from "@/components/workspace/AttachFilesModal";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { HandoffReadyBanner } from "@/components/handoff/HandoffReadyBanner";
import { getState, uploadFile } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useWorkspaceStream } from "@/lib/useWorkspaceStream";
import { liveActivity } from "@/lib/liveActivity";
import { useT } from "@/lib/i18n/provider";
import {
  checkUpload, problemFromError, uploadProblemText, type UploadProblem,
} from "@/lib/uploads";

// The workspace screen — grid ratio 1:5:5 (left stages : chat : context). The
// generated document opens in a drawer (WorkspaceDocPanel) laid OVER the grid
// rather than taking a column: as a 4th column (1:3.5:3.5:4) it left the
// question form 412px at 1440px (AI-PDS team feedback, 2026-10). The grid keeps a
// right gutter (`lg:pr-9`) for the drawer's handle tab so the tab never sits on
// top of the question form. Three openers share one `docOpen` state: the tab,
// the chat's document-update banner, and the question form's "View {name}".
// Below the `lg` breakpoint both side columns are hidden
// (StageSidebar/WorkspaceRightPanel are `hidden lg:flex` internally), leaving
// a single-column chat; a pending-questions badge over the chat opens a
// bottom-sheet that reuses the SAME QuestionForm widget the right panel would
// otherwise show (mode priority: questions > preview > artifacts). The drawer
// opens at every width — full width on a narrow screen.
export default function WorkspacePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const t = useT();
  const { modelLabel, language, readOnly } = useProjectMeta(projectId);
  const { items, streaming, send, submitAnswers, interrupt, pendingQuestions, stages, lastDocument, prototypeReady, changedPaths, historyLoading, context, activeDoc, turnSeq } =
    useWorkspaceStream(projectId);
  // When the stage changes, reread the state file — the handoff banner must appear the moment GTM
  // ends, and that judgment is made by the state file (capability), not live events
  // (HandoffReadyBanner header).
  const reloadState = state.reload;
  useEffect(() => {
    if (stages.length) reloadState();
  }, [stages.length, reloadState]);
  // Show the Path A/B welcome starter only once history has finished loading
  // (avoids a flash of the welcome card before restored history arrives) AND
  // the timeline is genuinely empty — a pending interrupt or an in-flight
  // turn means the conversation has already started, so the welcome card
  // must not reappear over it.
  const live = liveActivity(items);
  // Do not show the start card to read-only members — that button sends the first turn.
  const showWelcome = !readOnly && !historyLoading && items.length === 0 && !pendingQuestions
    && !streaming;
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  // Document drawer. Closed at first — the width for answering questions is the default.
  const [docOpen, setDocOpen] = useState(false);
  const openDoc = useCallback(() => setDocOpen(true), []);
  // The document the drawer should show now (the question form's "{이름} 보기" (view {name})).
  // The drawer reports it.
  const [currentDocPath, setCurrentDocPath] = useState<string | null>(null);
  const currentDocName = currentDocPath ? currentDocPath.slice(currentDocPath.lastIndexOf("/") + 1) : null;
  // Dismissible document-update notice (spec §5): track which version the
  // user has already dismissed so a LATER update (new version) re-shows the
  // banner even if an earlier one was dismissed.
  const [dismissedDocVersion, setDismissedDocVersion] = useState<string | null>(null);
  const showDocBanner = lastDocument != null && lastDocument.version !== dismissedDocVersion;
  // File-attachment state (Task 8): uploaded paths waiting to be mentioned in
  // the NEXT outgoing message, and any upload-failure notice to surface.
  // `original` is set when the upload kept its original next to the converted
  // text (a PDF) — the mention points the agent at both.
  const [attachments, setAttachments] = useState<{ path: string; original: string | null }[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  // While a file is being dragged into the chat area. dragenter/leave come in pairs every time
  // a child element is crossed, so the depth is counted — with a single boolean the overlay
  // flickers over children.
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
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

  // Upload one file and put it in a chip. The attach modal and chat-area drop take the same path.
  // Problems knowable before upload (format, size) are returned right away without sending.
  const attach = useCallback(async (file: File): Promise<UploadProblem | null> => {
    const problem = checkUpload(file);
    if (problem) return problem;
    try {
      const r = await uploadFile(projectId, file);
      setAttachments((prev) => [...prev, { path: r.path, original: r.original }]);
      return null;
    } catch (err) {
      return problemFromError(err);
    }
  }, [projectId]);

  // A drop does not go through the modal — it is a shortcut for people who know the formats. Only
  // the failed files remain above the input, with their reasons.
  async function attachDropped(files: File[]) {
    setUploadError(null);
    const failures: string[] = [];
    for (const file of files) {
      const problem = await attach(file);
      if (problem) failures.push(`${file.name}: ${uploadProblemText(t, problem)}`);
    }
    if (failures.length) setUploadError(failures.join("\n"));
  }

  const closeAttach = useCallback(() => setAttachOpen(false), []);

  function hasFiles(e: DragEvent) {
    return Array.from(e.dataTransfer.types).includes("Files");
  }

  // Prepends a "[첨부 파일: ...]" mention line per pending attachment ahead of
  // the user's typed text, then clears the chip tray — attachments are a
  // one-shot mention on the NEXT message, not a standing context.
  function sendWithAttachments(text: string) {
    setStickSignal((n) => n + 1);
    const mentions = attachments.map(({ path, original }) => original
      ? `[첨부 파일: ${path} — 사용자가 컨텍스트로 제공한 파일을 텍스트로 변환한 것입니다. 필요 시 이 파일을 읽어보세요. 도표·그림·스캔 페이지는 원본 ${original}에 있습니다.]`
      : `[첨부 파일: ${path} — 사용자가 컨텍스트로 제공한 파일입니다. 필요 시 이 파일을 읽어보세요.]`,
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

  // useCallback: this is the only callback passed down to the right panel (memo). If it were
  // a new function on every render, that panel would redraw on every frame while streaming.
  const submitAnswersAndStick = useCallback((answers: Record<string, string>) => {
    setStickSignal((n) => n + 1);
    submitAnswers(answers);
  }, [submitAnswers]);

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
    // relative: defense in depth — if a descendant's absolute element (sr-only, etc.) is
    // positioned relative to the document root by riding up a chain of static ancestors, a
    // ghost overflow appears on <html> and the header rolls up from a mere focus move
    // (ui-bug.png). Making the root a positioning context blocks this class of bug at the
    // page level.
    <div className="relative h-screen flex flex-col overflow-hidden">
      <AppHeader activeTab="workspace" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} readOnly={readOnly} />
      {/* overflow-clip: the closed drawer sits off-screen to the right via translate-x-full. If
          that became scrollable overflow, a single focus move would push the whole screen sideways
          (measured — the preventScroll comment in WorkspaceDocPanel). Why clip rather than
          hidden: hidden allows programmatic scrolling (focus, scrollIntoView), while clip does
          not create a scroll box at all. */}
      <div className="relative flex-1 grid min-h-0 grid-cols-1 pr-9 overflow-clip lg:grid-cols-[1fr_5fr_5fr]">
        <StageSidebar state={state.data} events={stages} />

        <main
          className="relative flex flex-col min-w-0 min-h-0 bg-slate-50"
          onDragEnter={(e) => {
            if (readOnly || !hasFiles(e)) return;
            dragDepth.current += 1;
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            if (!hasFiles(e)) return;
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (dragDepth.current === 0) setDragOver(false);
          }}
          // Always block while a file is being dragged — otherwise, the moment it is dropped
          // the browser opens that file and leaves the workspace. During a turn it is not
          // accepted, like 📎.
          onDragOver={(e) => {
            if (hasFiles(e)) e.preventDefault();
          }}
          onDrop={(e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            dragDepth.current = 0;
            setDragOver(false);
            if (!streaming && !readOnly) void attachDropped(Array.from(e.dataTransfer.files));
          }}
        >
          {dragOver && !streaming && (
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-2 z-20 rounded-2xl border-2 border-dashed border-violet-500 bg-violet-50/80 flex items-center justify-center text-sm font-bold text-violet-700"
            >
              {t("upload.dropOverlay")}
            </div>
          )}
          {/* The affordance for when Discovery has handed the prototype over to build.
              **It does not depend on the agent's guidance sentence** — until 2026-08-17 that
              sentence did not appear and users got stuck in Discovery (measured ws-a-v5: 0
              mentions of the Prototypes tab). Having no close button is also intentional: it is
              the only door to the next stage, so closing it by mistake would leave them stuck
              again. */}
          <HandoffReadyBanner projectId={projectId} state={state.data} className="m-3" />
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
                {/* Open it in the drawer in place instead of leaving for the review screen — the
                    drawer follows activeDoc (= this document), so there is no need to pass what
                    to open. */}
                <button
                  type="button"
                  onClick={() => {
                    setDismissedDocVersion(lastDocument.version);
                    openDoc();
                  }}
                  className="font-medium text-violet-700 underline hover:text-violet-900"
                >
                  {t("page.docOpen")}
                </button>
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
                stickSignal={stickSignal}
                historyLoading={historyLoading}
              />
            </>
          )}
          {uploadError && (
            <p role="alert" className="px-4 md:px-8 pb-1 text-xs text-red-600 whitespace-pre-line">
              {uploadError}
            </p>
          )}
          <AttachmentChips
            paths={attachments.map((a) => a.path)}
            onRemove={(p) => setAttachments((prev) => prev.filter((x) => x.path !== p))}
          />
          {/* Progress belongs to the **screen**, not the conversation flow — outside the scroll,
              just one line of the latest activity right above the input. The former approach of
              updating it alongside the bubble overlapped with token streaming and was
              distracting. The list is held by the collapsed progress record under the bubble
              after the turn ends. */}
          {live && <LiveActivityBar activity={live} />}
          {readOnly ? (
            <p className="mx-4 md:mx-8 mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              {t("readOnly.chat")}
            </p>
          ) : (
          <ChatInput
            key={draft ?? "no-draft"}
            onSend={sendWithAttachments}
            onAttach={() => {
              setUploadError(null);
              setAttachOpen(true);
            }}
            disabled={streaming}
            initialText={draft}
            onInterrupt={() => void interrupt()}
            interrupting={streaming}
            context={context}
          />
          )}
        </main>

        <WorkspaceRightPanel
          projectId={projectId}
          pendingQuestions={pendingQuestions}
          stages={stages}
          changedPaths={changedPaths}
          onSubmitAnswers={submitAnswersAndStick}
          busy={streaming}
          readOnly={readOnly}
          currentDocName={currentDocName}
          onOpenDoc={openDoc}
        />

        <WorkspaceDocPanel projectId={projectId} activeDoc={activeDoc} turnSeq={turnSeq}
                          changedPaths={changedPaths}
                          open={docOpen} onOpenChange={setDocOpen}
                          onCurrentDocChange={setCurrentDocPath} />
      </div>

      {attachOpen && <AttachFilesModal onClose={closeAttach} onUpload={attach} />}

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
            {currentDocName && (
              <OpenDocButton
                name={currentDocName}
                onClick={() => {
                  // The sheet appears above the drawer, so close the drawer first.
                  setSheetOpen(false);
                  openDoc();
                }}
              />
            )}
            <QuestionForm
              file={pendingQuestions.questions}
              onSubmit={submitAnswersFromSheet}
              submitting={streaming}
              readOnly={readOnly}
            />
          </div>
        </div>
      )}
    </div>
  );
}
