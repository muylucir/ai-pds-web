// frontend/components/canvas/UserMessage.tsx
import { memo } from "react";

// `memo`: same reason as AiMessage — even when the timeline redraws during streaming, past bubbles
// stay as they are.
export const UserMessage = memo(function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] bg-violet-600 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
});
