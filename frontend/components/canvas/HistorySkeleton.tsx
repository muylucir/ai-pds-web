"use client";
import { useT } from "@/lib/i18n/provider";
// frontend/components/canvas/HistorySkeleton.tsx
//
// A placeholder that fills the chat area while the history is being restored. Previously this
// area was a blank screen while GET /history was running, so reopening a project with a long
// conversation read as "my conversation disappeared".
//
// Using the same avatar and widths as AiMessage is intentional — the layout does not jump when
// loading finishes and the real items come in.
export function HistorySkeleton() {
  const t = useT();
  // The widths vary: three boxes of the same length look like loading bars, while ragged ones
  // look like a conversation.
  const widths = ["w-3/5", "w-4/5", "w-2/5"];
  return (
    <div role="status" aria-label={t("canvas.historyLoading")} className="space-y-4">
      {widths.map((w, i) => (
        <div key={i} className="flex gap-3">
          <span
            className="shrink-0 w-8 h-8 rounded-lg bg-slate-200 animate-pulse"
            aria-hidden="true"
          />
          <div
            data-testid="skeleton-line"
            className={`h-16 ${w} max-w-[85%] rounded-2xl rounded-tl-md bg-slate-100 animate-pulse`}
          />
        </div>
      ))}
    </div>
  );
}
