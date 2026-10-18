// frontend/components/canvas/ChatTimeline.tsx
"use client";
import { useEffect, useRef } from "react";
import type { ChatItem } from "@/lib/useWorkspaceStream";
import { useT } from "@/lib/i18n/provider";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";
import { HistoryQuestionsCard } from "./HistoryQuestionsCard";
import { answerSummary } from "@/lib/answerSummary";

// The workspace page (useWorkspaceStream) and the prototype build panel
// (usePrototypeStream) both render through this ONE component. The build
// panel's ChatItem is user/ai only, a subset of the workspace union, so the
// workspace union is the prop type.

export function ChatTimeline({
  items,
  stickSignal,
  historyLoading,
}: {
  items: ChatItem[];
  stickSignal?: number;
  historyLoading?: boolean;
}) {
  const t = useT();
  const scrollerRef = useRef<HTMLDivElement>(null);
  // stick-to-bottom: on by default. It turns off when the user scrolls up, and turns back on
  // when they return near the bottom or send a message (stickSignal increments). Even as a
  // long response grows via streaming, it keeps following the bottom as long as stick is on —
  // the former "only within 120px of the bottom" policy was what broke following on long
  // responses.
  const stickRef = useRef(true);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    // Programmatic scrolling (right after scrollTop=scrollHeight) also goes through this handler,
    // but in that case the bottom check is true, so stick is kept. Only when the user scrolls up
    // does it move away from the bottom and turn stick off.
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  useEffect(() => {
    // Sending a message = always return to the bottom (the direct fix for the user's request).
    stickRef.current = true;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [stickSignal]);

  // Deliberately NOT scrollIntoView(): that API scrolls EVERY scrollable
  // ancestor (html included), and during initial history hydration it can
  // scroll the document itself by a transient overflow — leaving the page
  // stuck shifted up (header off-screen, body background showing at the
  // bottom) once layout settles back to 100vh. Setting scrollTop touches
  // only this container, so the page can never move.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [items]);

  return (
    <div
      ref={scrollerRef}
      onScroll={onScroll}
      className="chat-scroll flex-1 min-h-0 overflow-y-auto px-4 md:px-8 py-6"
      aria-label={t("canvas.timelineLabel")}
    >
      {/* Max 896px (max-w-4xl) — the same value as ChatInput, so the input's left and right edges
          line up with the bubble area. On screens where the chat column widened to 5:5 the bubbles
          follow the column, but on very wide screens one line of an AI answer does not grow
          endlessly long. */}
      <div className="max-w-4xl mx-auto space-y-5">
        {items.length === 0 ? (
          // Hide this text during historyLoading — the parent (WorkspacePage) draws
          // HistorySkeleton over the same spot. Without hiding it, the contradiction of
          // "restoring the previous conversation" and "대화를 시작해 보세요 (start a conversation) (=
          // nothing yet)" showing at the same time arises — exactly the scenario this task
          // set out to remove when re-entering a project with a long conversation.
          !historyLoading && (
            <p className="text-center text-sm text-slate-400 mt-10">
              {t("chat.emptyTimeline")}
            </p>
          )
        ) : (
          items.map((item) => {
            if (item.role === "user") {
              // A restored answer-submission turn. It goes down three levels:
              //
              //  1) answers + questions → built with **the same function** live uses.
              //     Question numbers, option letters, and option text come out as is, so
              //     the scrollback matches the screen just seen. This combination comes from
              //     sessions that have answer records (backend/agent/answer_store.py).
              //  2) answers only → a "1: A" listing. Without the payload, option text
              //     cannot be expanded (old Strands transcripts).
              //  3) Neither → the backend text — a sentence the CLI wrote, or free text.
              const text = item.answers && item.questions
                ? answerSummary(item.questions, item.answers, t)
                : item.answers
                  ? `${t("chat.answersSubmitted")} — ${Object.entries(item.answers)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(" · ")}`
                  : item.text;
              return <UserMessage key={item.id} text={text} />;
            }
            if (item.role === "ai") return <AiMessage key={item.id} item={item} />;
            // role === "history-card" — a questions file presented in a PAST
            // turn (Task 5's history restore), a static summary marker
            // (mockup 04's ml-11 idiom). If the payload is restored along with it, it can be
            // expanded to read the questions and options.
            return (
              <div key={item.id} className="ml-11 max-w-[85%]">
                <HistoryQuestionsCard name={item.name} file={item.file} />
              </div>
            );
          })
        )}
        {items.length > 0 && (
          <p className="text-center text-[11px] text-slate-400">
            {t("canvas.chatHintPrefix")}{" "}
            <span className="italic">&quot;{t("canvas.chatHintExample1")}&quot;</span>,{" "}
            <span className="italic">&quot;{t("canvas.chatHintExample2")}&quot;</span>,{" "}
            <span className="italic">&quot;{t("canvas.chatHintExample3")}&quot;</span>
          </p>
        )}
      </div>
    </div>
  );
}
