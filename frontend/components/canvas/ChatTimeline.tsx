// frontend/components/canvas/ChatTimeline.tsx
import type { ChatItem } from "@/lib/useTurnStream";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";
import { QuestionCardSlot } from "./QuestionCardSlot";
import { ArtifactCard } from "./ArtifactCard";

export function ChatTimeline({
  items,
  projectId,
  onChoose,
  onOpenArtifact,
  busy,
}: {
  items: ChatItem[];
  projectId: string;
  onChoose: (text: string) => void;
  onOpenArtifact: () => void;
  busy: boolean;
}) {
  return (
    <div
      className="chat-scroll flex-1 overflow-y-auto px-4 md:px-8 py-6"
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
