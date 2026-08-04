"use client";
// frontend/components/workspace/StageSidebar.tsx
import type { ProjectState, StageState, StagePayload } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";

// Merge server-fetched stage state (GET /state — aiplc-state.md's parsed
// snapshot, the fallback shown before any live event arrives) with the
// workspace stream's accumulated "stage" events (newest last). Events
// override by stage-name match: an event's status always wins, and its
// summary becomes the row's `note` UNLESS the summary is empty, in which
// case the previous note is kept (an empty summary isn't "no note", it's
// "no update to report" — see CanvasSidebar's StageRow, which renders `note`
// under an in_progress row). A stage the server didn't know about yet is
// synthesized starting from "pending" so it still renders.
export function mergeStages(server: StageState[], events: StagePayload[]): StageState[] {
  const byName = new Map(server.map((s) => [s.name, { ...s }]));
  for (const ev of events) {
    const cur = byName.get(ev.stage) ?? { name: ev.stage, status: "pending" as const, note: null };
    byName.set(ev.stage, { ...cur, status: ev.status, note: ev.summary || cur.note });
  }
  return [...byName.values()];
}

// Visual pattern ported verbatim from CanvasSidebar's StageRow (same
// completed/in_progress/pending row chrome) — kept private to this module
// since the workspace sidebar is now the only consumer of the merged list.
function StageRow({ stage, index }: { stage: StageState; index: number }) {
  if (stage.status === "completed") {
    return (
      <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-slate-500">
        <span
          className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-[10px]"
          aria-hidden="true"
        >
          ✓
        </span>
        {stage.name}
      </div>
    );
  }
  if (stage.status === "in_progress") {
    return (
      <div className="px-2.5 py-2 rounded-lg bg-violet-50 border border-violet-200">
        <div className="flex items-center gap-2.5">
          <span
            className="w-5 h-5 rounded-full bg-violet-600 text-white flex items-center justify-center text-[10px] font-bold animate-pulse"
            aria-hidden="true"
          >
            ●
          </span>
          <span className="font-bold text-violet-800">{stage.name}</span>
        </div>
        {stage.note && <p className="mt-1.5 ml-7 text-[11px] text-violet-600">{stage.note}</p>}
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
      {stage.name}
    </div>
  );
}

export function StageSidebar({
  state,
  events,
}: {
  state: ProjectState | null;
  events: StagePayload[];
}) {
  const t = useT();
  const stages = mergeStages(state?.stages ?? [], events);
  const merged: ProjectState = {
    project_type: state?.project_type ?? null,
    current_stage: state?.current_stage ?? null,
    stages,
  };
  const pct = progressPercent(merged);
  const { completed, total } = stageCounts(merged);
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
          {completed} / {total} 스테이지{merged.project_type ? ` · ${merged.project_type}` : ""}
        </p>
      </div>
      <nav className="flex-1 overflow-y-auto p-3 text-sm space-y-0.5">
        {stages.map((stage, i) => (
          <StageRow key={stage.name} stage={stage} index={i} />
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
}
