// frontend/components/canvas/UserMessage.tsx
import { memo } from "react";

// `memo`: AiMessage와 같은 이유 — 스트리밍 중 타임라인이 다시 그려져도 지난 말풍선은
// 그대로다.
export const UserMessage = memo(function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] bg-violet-600 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
});
