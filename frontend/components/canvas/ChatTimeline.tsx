// frontend/components/canvas/ChatTimeline.tsx
"use client";
import { useEffect, useRef } from "react";
import type { ChatItem as CanvasChatItem } from "@/lib/useTurnStream";
import type { ChatItem as WorkspaceChatItem } from "@/lib/useWorkspaceStream";
import { useT } from "@/lib/i18n/provider";
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
  stickSignal,
  historyLoading,
}: {
  items: ChatTimelineItem[];
  projectId: string;
  onChoose: (text: string) => void;
  onOpenArtifact: () => void;
  busy: boolean;
  stickSignal?: number;
  historyLoading?: boolean;
}) {
  const t = useT();
  const scrollerRef = useRef<HTMLDivElement>(null);
  // stick-to-bottom: 기본 켜짐. 사용자가 위로 스크롤하면 꺼지고, 바닥 근처로
  // 돌아오거나 메시지를 보내면(stickSignal 증가) 다시 켜진다. 스트리밍으로
  // 긴 응답이 자라도 stick이 켜져 있는 한 계속 바닥을 따라간다 — 기존
  // "바닥 120px 이내일 때만" 정책은 긴 응답에서 따라가기가 끊기는 원인이었다.
  const stickRef = useRef(true);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    // 프로그램적 스크롤(scrollTop=scrollHeight 직후)도 이 핸들러를 타지만,
    // 그 경우 바닥 판정이 참이라 stick이 유지된다. 사용자가 위로 올렸을 때만
    // 바닥에서 멀어져 stick이 꺼진다.
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  useEffect(() => {
    // 메시지 전송 = 무조건 바닥 복귀 (사용자 요청의 직접 해결).
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
      <div className="max-w-2xl mx-auto space-y-5">
        {items.length === 0 ? (
          // historyLoading 중에는 이 문구를 숨긴다 — 부모(WorkspacePage)가
          // 같은 자리에 HistorySkeleton을 겹쳐 그린다. 숨기지 않으면 "이전
          // 대화를 복원하는 중"과 "대화를 시작해 보세요(=아직 아무것도 없음)"가
          // 동시에 뜨는 모순이 생긴다 — 대화가 많은 프로젝트를 재진입할 때
          // 정확히 이 태스크가 없애려던 시나리오다.
          !historyLoading && (
            <p className="text-center text-sm text-slate-400 mt-10">
              대화를 시작해 보세요 — 아래에 메시지를 입력하세요.
            </p>
          )
        ) : (
          items.map((item) => {
            if (item.role === "user") {
              // answers가 있으면 UI 언어로 문구를 다시 만든다 — 백엔드의 text는
              // 이 필드를 모르는 구 프론트를 위한 한국어 폴백일 뿐이다.
              const text = item.answers
                ? `${t("chat.answersSubmitted")} — ${Object.entries(item.answers)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(" · ")}`
                : item.text;
              return <UserMessage key={item.id} text={text} />;
            }
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
