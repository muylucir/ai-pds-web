"use client";
// frontend/components/canvas/AiMessage.tsx
import { memo } from "react";
import type { AiItem } from "@/lib/chatItems";
import { Markdown } from "@/components/Markdown";
import { ReasoningTrace } from "./ReasoningTrace";
import { useT } from "@/lib/i18n/provider";

function TypingDots() {
  const t = useT();
  return (
    <span aria-label={t("canvas.aiWriting")} className="inline-flex items-center gap-1 py-1">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

// `memo`: during streaming the timeline re-renders every frame, but only the running bubble
// changes. The hook's patchAi leaves the references of unchanged items as they are, so past
// bubbles stop here and do not re-parse their markdown.
export const AiMessage = memo(function AiMessage({ item }: { item: AiItem }) {
  const t = useT();
  // **Progress is not this component's job (2026-09-04).** The progress indicator lives in the
  // pinned line above the input (LiveActivityBar), owned by the screen. The earlier approach,
  // where the progress indicator and an expanded progress log updated together beside the
  // bubble, became distracting once token streaming arrived — three things were moving at once.
  //
  // So the progress log attaches, collapsed, **only after the turn ends** — drawing it while
  // the turn runs would defeat the reason for moving it. The only motion left here is the bubble's text.
  return (
    <div className="flex gap-3">
      <span
        className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-bold"
        aria-hidden="true"
      >
        AI
      </span>
      <div className="max-w-[85%] min-w-0">
        {/* Draw the bubble only when there is something to show. A restored turn has streaming
            false, so with no text not even the typing dots appear, leaving only an empty gray
            box — that is what an interrupted turn (idle timeout, SSE drop) looks like, and in the
            live view the progress indicator occupied that spot. ReasoningTrace below still
            renders, so how far the tools ran is preserved. */}
        {(item.streaming || item.text !== "" || item.error) && (
          <div data-testid="ai-bubble" className="bg-white border border-slate-200 rounded-2xl rounded-tl-md px-4 py-3 text-sm leading-relaxed" aria-live="polite">
            {item.streaming && item.text === "" ? (
              <TypingDots />
            ) : (
              <Markdown text={item.text} />
            )}
            {item.error && <p className="mt-2 text-rose-600">{item.error}</p>}
          </div>
        )}
        {item.interrupted && (
          <p className="mt-1.5 text-xs text-slate-400">{t("canvas.interrupted")}</p>
        )}
        {!item.streaming && <ReasoningTrace entries={item.trace} />}
      </div>
    </div>
  );
});
