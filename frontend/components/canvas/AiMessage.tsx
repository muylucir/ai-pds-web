// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { ReasoningTrace } from "./ReasoningTrace";

export function AiMessage({ item }: { item: AiItem }) {
  return (
    <div className="flex gap-3">
      <span
        className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-bold"
        aria-hidden="true"
      >
        AI
      </span>
      <div className="max-w-[85%] min-w-0">
        <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-md px-4 py-3 text-sm leading-relaxed" aria-live="polite">
          {item.streaming && item.text === "" ? (
            <p className="text-slate-400">AI가 작성 중…</p>
          ) : (
            <p className="whitespace-pre-wrap">{item.text}</p>
          )}
          {item.error && <p className="mt-2 text-rose-600">{item.error}</p>}
        </div>
        <ReasoningTrace entries={item.trace} />
      </div>
    </div>
  );
}
