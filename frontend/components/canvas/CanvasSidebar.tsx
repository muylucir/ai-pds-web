"use client";
import type { ProjectState, StageState } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";

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

export function CanvasSidebar({ state }: { state: ProjectState }) {
  const t = useT();
  const pct = progressPercent(state);
  const { completed, total } = stageCounts(state);
  return (
    <aside
      className="hidden lg:flex w-60 shrink-0 bg-white border-r border-slate-200 flex-col"
      aria-label={t("canvas.stageProgressLabel")}
    >
      <div className="px-4 py-3 border-b border-slate-100">
        <p className="text-xs font-bold text-slate-400 uppercase tracking-wide">{t("canvas.discoveryProgress")}</p>
        <div className="mt-2 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-slate-400 mt-1">
          {completed} / {total} {t("canvas.stageUnit")}{state.project_type ? ` · ${state.project_type}` : ""}
        </p>
      </div>
      <nav className="flex-1 overflow-y-auto p-3 text-sm space-y-0.5">
        {state.stages.map((stage, i) => (
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
