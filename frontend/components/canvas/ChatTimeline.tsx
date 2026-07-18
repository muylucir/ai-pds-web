// frontend/components/canvas/ChatTimeline.tsx
import type { ChatItem } from "@/lib/useTurnStream";
import { UserMessage } from "./UserMessage";
import { AiMessage } from "./AiMessage";

export function ChatTimeline({ items }: { items: ChatItem[] }) {
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
          items.map((item) =>
            item.role === "user" ? (
              <UserMessage key={item.id} text={item.text} />
            ) : (
              <AiMessage key={item.id} item={item} />
            ),
          )
        )}
      </div>
    </div>
  );
}
