// frontend/components/canvas/ChatTimeline.tsx
"use client";
import { useEffect, useRef } from "react";
import type { ChatItem as CanvasChatItem } from "@/lib/useTurnStream";
import type { ChatItem as WorkspaceChatItem } from "@/lib/useWorkspaceStream";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";
import { QuestionCardSlot } from "./QuestionCardSlot";
import { ArtifactCard } from "./ArtifactCard";

// The canvas-era page (useTurnStream) and the Task 11 workspace page
// (useWorkspaceStream) both render through this ONE component, each with its
// own ChatItem union — they share "user"/"ai" but diverge on the card shape
// ("card" with a path vs. "history-card" with a name). Rather than forcing
// one hook's type onto the other, the prop is declared as the union of BOTH,
// and each branch below discriminates on `role` (a plain string switch, not
// an `in` shape-check) so TS narrows correctly regardless of which hook
// produced the item.
export type ChatTimelineItem = CanvasChatItem | WorkspaceChatItem;

export function ChatTimeline({
  items,
  projectId,
  onChoose,
  onOpenArtifact,
  busy,
}: {
  items: ChatTimelineItem[];
  projectId: string;
  onChoose: (text: string) => void;
  onOpenArtifact: () => void;
  busy: boolean;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Smart autoscroll: only snap to the bottom when the user is already
  // (roughly) there — someone scrolled up to re-read earlier turns must not
  // get yanked back down every time a new streaming chunk lands.
  //
  // Deliberately NOT scrollIntoView(): that API scrolls EVERY scrollable
  // ancestor (html included), and during initial history hydration it can
  // scroll the document itself by a transient overflow — leaving the page
  // stuck shifted up (header off-screen, body background showing at the
  // bottom) once layout settles back to 100vh. Setting scrollTop touches
  // only this container, so the page can never move.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [items]);

  return (
    <div
      ref={scrollerRef}
      className="chat-scroll flex-1 min-h-0 overflow-y-auto px-4 md:px-8 py-6"
      aria-label="대화 타임라인"
    >
      <div className="max-w-2xl mx-auto space-y-5">
        {items.length === 0 ? (
          <p className="text-center text-sm text-slate-400 mt-10">
            대화를 시작해 보세요 — 아래에 메시지를 입력하세요.
          </p>
        ) : (
          items.map((item) => {
            if (item.role === "user") return <UserMessage key={item.id} text={item.text} />;
            if (item.role === "ai") return <AiMessage key={item.id} item={item} />;
            if (item.role === "history-card") {
              // A questions file presented in a PAST turn (Task 5's history
              // restore) — a static summary marker, never the live
              // interactive QuestionCardSlot form (mockup 04's ml-11 idiom).
              return (
                <div key={item.id} className="ml-11 max-w-[85%]">
                  <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-xs text-violet-700">
                    📋 질문지 제시됨{item.name ? ` — ${item.name}` : ""}
                  </div>
                </div>
              );
            }
            // role === "card" — inline widget, indented under the AI avatar
            // column (mockup 04's ml-11 idiom for its submitted/artifact cards).
            return (
              <div key={item.id} className="ml-11 max-w-[85%]">
                {item.card === "questions" ? (
                  <QuestionCardSlot projectId={projectId} path={item.path} onChoose={onChoose} busy={busy} />
                ) : (
                  <ArtifactCard path={item.path} onOpen={onOpenArtifact} />
                )}
              </div>
            );
          })
        )}
        {items.length > 0 && (
          <p className="text-center text-[11px] text-slate-400">
            버튼 대신 채팅으로 답해도 됩니다 — <span className="italic">&quot;승인&quot;</span>,{" "}
            <span className="italic">&quot;고객 인용문을 파트장 관점으로 바꿔줘&quot;</span>,{" "}
            <span className="italic">&quot;이전 단계로 돌아가고 싶어&quot;</span>
          </p>
        )}
      </div>
    </div>
  );
}
