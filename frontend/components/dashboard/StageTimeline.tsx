"use client";
import Link from "next/link";
import type { ProjectState, StageState } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

function StageIcon({ stage, index }: { stage: StageState; index: number }) {
  if (stage.status === "completed") {
    return (
      <span className="shrink-0 w-10 h-10 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center" aria-hidden="true">
        ✓
      </span>
    );
  }
  if (stage.status === "in_progress") {
    return (
      <span className="shrink-0 w-10 h-10 rounded-full bg-violet-600 text-white flex items-center justify-center ring-4 ring-violet-100 font-bold text-sm" aria-hidden="true">
        {index + 1}
      </span>
    );
  }
  return (
    <span className="shrink-0 w-10 h-10 rounded-full bg-slate-100 text-slate-400 flex items-center justify-center text-sm font-bold" aria-hidden="true">
      {index + 1}
    </span>
  );
}

export function StageTimeline({ state, projectId }: { state: ProjectState; projectId: string }) {
  const t = useT();
  return (
    <section className="lg:col-span-2 bg-white rounded-xl border border-slate-200" aria-labelledby="stage-heading">
      <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
        <h2 id="stage-heading" className="font-bold">{t("dash.stageProgressTitle")}</h2>
        <span className="text-xs text-slate-400">{t("dash.adaptiveNote")}</span>
      </div>
      {/* 갓 만든 프로젝트의 `stages`는 빈 배열이고, 그때 빈 <ol>만 남으면 이 큰
          패널이 흰 박스가 되어 고장으로 읽힌다. 자매 패널(ArtifactsPanel·
          ActivityFeed)과 같은 형태의 한 줄로 채운다 — 무엇을 해야 하는지는
          위쪽 GetStartedBanner가 말하므로 여기서는 사실만 적는다. */}
      {state.stages.length === 0 ? (
        <p className="p-5 text-sm text-slate-400">{t("dash.noStages")}</p>
      ) : (
        <ol className="p-6 space-y-2">
          {state.stages.map((stage, i) => {
            const active = stage.status === "in_progress";
            const done = stage.status === "completed";
            return (
              <li key={stage.name} className="stage-line relative flex gap-4 pb-6">
                <StageIcon stage={stage} index={i} />
                <div className="pt-1 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className={active ? "font-bold text-violet-800" : done ? "font-medium" : "font-medium text-slate-400"}>
                      {stage.name}
                    </h3>
                    {done && <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">{t("dash.stageDone")}</span>}
                    {active && (
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-100 text-violet-700 animate-pulse">{t("dash.stageInProgress")}</span>
                    )}
                  </div>
                  {stage.note && (
                    <p className={`text-sm mt-0.5 ${stage.status === "pending" ? "text-slate-400" : "text-slate-500"}`}>
                      {stage.note}
                    </p>
                  )}
                  {active && (
                    <Link
                      href={`/projects/${projectId}/questions`}
                      className="mt-3 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium"
                    >
                      {t("dash.continueAnswering")}
                    </Link>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
