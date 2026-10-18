// frontend/components/canvas/ContextMeter.tsx — the "컨텍스트 N% 남음" (N% context left) above the input.
//
// Shows how much of the agent's context window is left **until auto-compaction** (the value is
// defined by ContextUsage in lib/api/types.ts). Compaction is an event the user cannot see, but its
// result is visible — the agent starts forgetting details agreed on earlier. The purpose of this
// indicator is to give grounds for deciding "whether to start fresh" before that happens.
//
// The workspace and the build panel use the same component (ChatInput draws it). With no value it
// draws nothing — before the first turn, or when a new conversation is about to start.
"use client";
import type { ContextUsage } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

/** 285000 → "285K", 1000000 → "1M". For display only, so precision is not needed. */
function compact(tokens: number): string {
  if (tokens >= 1_000_000) return `${+(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

export function ContextMeter({ usage }: { usage: ContextUsage }) {
  const t = useT();
  const left = usage.left_pct;
  // Make it prominent below 25% — the one or two turns just before compaction are when irreversible decisions get made.
  const tone = left < 10 ? "text-red-600" : left < 25 ? "text-amber-600" : "text-slate-400";
  const detail = t("chat.contextDetail")
    .replace("{used}", compact(usage.total_tokens))
    .replace("{limit}", compact(usage.compact_at_tokens));
  return (
    <p className={`text-[11px] text-right mb-1 ${tone}`} title={detail} aria-label={detail}>
      {t("chat.contextLeft").replace("{n}", String(left))}
    </p>
  );
}
