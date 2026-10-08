"use client";
// frontend/components/workspace/StageSidebar.tsx
import { memo } from "react";
import type { CapabilityState, ProjectState, StagePayload } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";

// The six capabilities to show: the newest snapshot a live "stage" event
// carried, else the server's (GET /state — aiplc-state.md's parsed snapshot,
// shown before any live event arrives). A snapshot is the whole picture, so
// the latest one simply replaces the rest — the backend rolls the agent's
// free-form stage list up into the official six (aipds/capabilities.py) and
// this side never re-derives it.
export function latestCapabilities(
  state: ProjectState | null,
  events: StagePayload[],
): CapabilityState[] {
  for (let i = events.length - 1; i >= 0; i--) {
    const snapshot = events[i].capabilities;
    if (snapshot) return snapshot;
  }
  return state?.capabilities ?? [];
}

// Completed/in_progress/pending/not-on-this-path row chrome — kept private to
// this module since the workspace sidebar is the only consumer.
function CapabilityRow({ cap, index }: { cap: CapabilityState; index: number }) {
  const t = useT();
  if (cap.status === "completed") {
    return (
      <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-500">
        <span
          className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-[10px]"
          aria-hidden="true"
        >
          ✓
        </span>
        {cap.name}
      </div>
    );
  }
  if (cap.status === "in_progress") {
    return (
      <div className="px-2.5 py-2 rounded-lg bg-violet-50 border border-violet-200">
        <div className="flex items-center gap-2.5">
          <span
            className="w-5 h-5 rounded-full bg-violet-600 text-white flex items-center justify-center text-[10px] font-bold animate-pulse"
            aria-hidden="true"
          >
            ●
          </span>
          <span className="font-bold text-violet-800">{cap.name}</span>
        </div>
        {cap.note && <p className="mt-1.5 ml-7 text-[11px] text-violet-600">{cap.note}</p>}
      </div>
    );
  }
  if (cap.status === "not_applicable") {
    return (
      <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-300">
        <span
          className="w-5 h-5 rounded-full border border-dashed border-slate-200 flex items-center justify-center text-[10px]"
          aria-hidden="true"
        >
          –
        </span>
        <span>
          {cap.name}
          <span className="block text-[10px]">{t("canvas.capabilityNotApplicable")}</span>
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-400">
      <span
        className="w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center text-[10px]"
        aria-hidden="true"
      >
        {index + 1}
      </span>
      {cap.name}
    </div>
  );
}

// `memo`: 스트리밍 중 워크스페이스 페이지는 프레임마다 다시 그려지지만(채팅 말풍선이
// 자란다) 이 패널의 props는 그동안 그대로다.
export const StageSidebar = memo(function StageSidebar({
  state,
  events,
}: {
  state: ProjectState | null;
  events: StagePayload[];
}) {
  const t = useT();
  const capabilities = latestCapabilities(state, events);
  const pct = progressPercent({ capabilities });
  const { completed, total } = stageCounts({ capabilities });
  return (
    <aside
      className="hidden lg:flex flex-col min-h-0 bg-white border-r border-slate-200"
      aria-label={t("canvas.stageProgressLabel")}
    >
      <div className="px-4 py-3 border-b border-slate-100">
        <p className="text-xs font-bold text-slate-400 uppercase tracking-wide">{t("canvas.discoveryProgress")}</p>
        <div className="mt-2 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-slate-400 mt-1">
          {completed} / {total} {t("canvas.stageUnit")}{state?.project_type ? ` · ${state.project_type}` : ""}
        </p>
      </div>
      <nav className="flex-1 overflow-y-auto p-3 text-sm space-y-0.5">
        {capabilities.map((cap, i) => (
          <CapabilityRow key={cap.key} cap={cap} index={i} />
        ))}
      </nav>
      <div className="p-3 border-t border-slate-100 text-[11px] text-slate-400 leading-relaxed">
        {t("canvas.sidebarAdaptive")}
        <br />
        {t("canvas.sidebarHintPrefix")} <b>{t("canvas.sidebarHintBold")}</b>
        {t("canvas.sidebarHintSuffix")}
      </div>
    </aside>
  );
});
