// frontend/components/prototypes/BuildPanel.tsx — the prototype build chat
// panel: opened from the prototypes tab grid when a card's build session is
// live. Reuses the SAME chat rendering (ChatTimeline/AiMessage/ChatInput) and
// question wizard (QuestionForm) as the workspace screen — only the stream
// hook (usePrototypeStream) and the surrounding chrome are new.
"use client";
import { useEffect, useRef, useState } from "react";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { LiveActivityBar } from "@/components/canvas/LiveActivityBar";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { Markdown } from "@/components/Markdown";
import { closeSession, startHost } from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import { knownErrorMessage } from "@/lib/api/errorMessage";
import { usePrototypeStream } from "@/lib/usePrototypeStream";
import { liveActivity } from "@/lib/liveActivity";
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
    items, streaming, agents, pendingQuestions, buildComplete, changedPaths,
    historyLoading, context, startBuild, restoreHistory, send, submitAnswers, interrupt,
    restartForImprovement, resume,
  } = usePrototypeStream(projectId, slug);
  const [closing, setClosing] = useState(false);
  const [submittingAnswers, setSubmittingAnswers] = useState(false);
  const [hosting, setHosting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const completeCardRef = useRef<HTMLDivElement | null>(null);
  const completed = buildComplete !== null;
  const live = liveActivity(items);

  useEffect(() => {
    // A newly opened session auto-opens; an already open session (refresh, reopen) restores that
    // session's conversation and attaches to the running turn — not being able to see a stalled turn
    // in an empty panel is the reason for this branch. Either way, the conversations of past sessions
    // are laid out before it (resume lays them out itself).
    if (autoStart) {
      startBuild();
      void restoreHistory();
    } else void resume();
    // Mount-only — startBuild must fire at most once per panel lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The completion card sits **below** the file change list (the reason for that placement is in the comment next to the card).
  // So with many changed files the card appears outside the scroll view, and the user presses on
  // without seeing the completion or the buttons they need. Scroll once at the moment of completion to bring
  // the card into view — since it is `nearest`, it does nothing if the card is already visible.
  //
  // Why an optional call: jsdom has no scrollIntoView. If the component died for lack of it,
  // every test for this panel would break because of a layout change.
  useEffect(() => {
    if (!completed) return;
    completeCardRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [completed]);

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
      // 404 is a normal path: after the completion declaration the backend closes the session first
      // on a grace timer (_COMPLETION_GRACE_SECONDS in proto/session.py). There is no reason to hold
      // the panel open because a session that is already gone could not be closed.
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
      // Do not close the panel — closing it would leave the user looking at a card that failed for
      // no apparent reason in the grid. Show the error here and allow a retry.
      setActionError(
        err instanceof ApiError
          ? knownErrorMessage(t, err.detail, t("proto.hostStartFailed"))
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
      // 429 (concurrent build limit) is a path that can actually be reached. Do not clear
      // the card — clearing it would make the user lose both the completion summary and the hosting options.
      // actionError is shared with hosting: this card has only one error line, and the two
      // actions cannot fail at the same time (each disables the other).
      setActionError(
        err instanceof ApiError
          ? knownErrorMessage(t, err.detail, t("proto.improveStartFailed"))
          : t("proto.improveStartFailed"));
    } finally {
      setRestarting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-900/40 flex items-stretch justify-end">
      {/* Drawer width: the build log, question form, and file change list have to be read together,
          so it is widened from 7xl (80rem) to 1720px. It is the same width the review screen
          (review/page.tsx) uses. On narrow screens w-full simply fills the viewport. */}
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

        {/* Split chat and the right side (questions, file changes) 1:1. With the old 1/3:2/3 the build
            log was narrow and code blocks and tool output kept wrapping — giving it the same width as the
            question form lets the two be read side by side. basis+min-w-0 pins the ratio:
            if the aside had a fixed width, all the extra width of the wider drawer would go to the
            chat. On narrow screens (below md) they stack vertically, so the ratio does not apply.
            **The two bases must sum to exactly 1** — both are shrink-0/flex-none, so an
            overflow is not given back by flex but simply clipped. A test pins this sum. */}
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <div className="flex-1 md:flex-none md:basis-1/2 md:min-w-0 min-h-0 flex flex-col">
            <ChatTimeline
              items={items}
              historyLoading={historyLoading}
            />
            {/* The moment an edit session opens empty. **Not auto-speaking is
                intentional** — someone who pressed "수정하기" (Edit) already knows what to fix,
                but auto-open would first burn a round trip of the agent asking back, and the input box
                stays locked while that question is up (rationale in prompts.handoff_prompt). The price is
                a moment with nothing on screen, so say what to do. */}
            {items.length === 0 && !streaming && !historyLoading && (
              <div className="shrink-0 px-4 md:px-8 pb-2 text-center">
                <p className="text-sm text-slate-500">{t("proto.modifyPrompt")}</p>
                <p className="mt-1 text-xs text-slate-400">{t("proto.modifyExample")}</p>
              </div>
            )}
            {/* After the completion declaration this session closes shortly (5-second grace) or is
                already closed — keeping the input open would lead a message the user sends
                into a 404 on GET .../events, and usePrototypeStream's
                onError would show "연결이 끊어졌습니다" (Connection lost) (when in fact the build has
                simply finished). Block input and instead point to the buttons on
                the completion card on the right. */}
            {buildComplete !== null && (
              <p className="shrink-0 px-4 md:px-8 pt-2 text-xs text-slate-400 text-center">
                {t("proto.sessionClosedNotice")}
              </p>
            )}
            {/* Progress belongs to the screen, not to the conversation flow — the same component as the
                workspace is placed in the same spot (right above the input box). Build turns pour out
                especially many tool activities, so "only the last one" pays off greatly. */}
            {/* While `agents` is not empty, that bar expands into one row per agent.
                Because "only the last one" fails precisely in parallel stretches — a single line
                flickered among three agents and none of them read as progress
                (lib/protoAgents.ts header). */}
            {live && <LiveActivityBar activity={live} agents={agents} />}
            <ChatInput
              onSend={send}
              disabled={streaming || buildComplete !== null}
              onInterrupt={() => void interrupt()}
              interrupting={streaming}
              context={context}
            />
          </div>

          {/* basis-1/2 — must sum to exactly 100% with the chat. The old 2/3 was a leftover
              not fixed when the chat was widened from 1/3 to 1/2 (70783c0); the sum
              became 7/6, overflowing by 287px in the 1720px drawer and clipping the right side of
              option text with no scrollbar (observed). Being shrink-0, flex does not give it back
              either. */}
          <aside className="w-full md:basis-1/2 md:min-w-0 shrink-0 border-t md:border-t-0 md:border-l border-slate-200 flex flex-col min-h-0 overflow-y-auto">
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
            {/* The completion card is **below the file change list**. When it was above, the card
                appeared above the user's scroll position the moment the build finished — they had been
                scrolling down watching files pile up, so to see the completion they had to scroll back
                up (user report). Having the conclusion about the result come after the result itself
                matches the reading direction. When the list is long and the card appears off screen,
                the scrollIntoView in the useEffect above handles it. */}
            {buildComplete && (
              <div ref={completeCardRef} className="p-4 border-t border-slate-200">
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                  <p className="text-sm font-bold text-emerald-800">{t("proto.buildComplete")}</p>
                  {/* Rendered as markdown. This text is the build_complete tool's
                      summary/remaining argument, and the model writes lists, emphasis,
                      and inline code here — the tool schema does not prevent that either.
                      With whitespace-pre-wrap, `- `, `**`, and backticks would appear on
                      screen verbatim. The same agent's prose is already rendered as markdown
                      in the left timeline (AiMessage), so within one screen the display
                      convention for the same text was diverging.
                      The shared Markdown still does not render raw HTML, as
                      before. */}
                  <Markdown
                    text={buildComplete.summary}
                    className="mt-2 text-slate-700 prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5"
                  />
                  {buildComplete.remaining && (
                    <>
                      <p className="mt-3 text-xs font-bold text-slate-500">{t("proto.remainingWork")}</p>
                      <Markdown
                        text={buildComplete.remaining}
                        className="mt-1 text-slate-600 prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5"
                      />
                    </>
                  )}
                </div>
                {actionError && (
                  <p className="mt-3 text-sm text-rose-600">{actionError}</p>
                )}
                {/* build_complete arrives before done, so even after the card appears,
                    streaming can stay true during the 0-5 second window (the backend grace
                    timer's limit) in which the agent sends its wrap-up text. If improve,
                    host, or close is pressed in that window, the still-open stream and the
                    new action get tangled (a real case: a race where session A's stream was
                    not cleaned up while continuing the improvement opened session B
                    anew) — all three buttons are blocked while streaming. */}
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
          </aside>
        </div>
      </div>
    </div>
  );
}
