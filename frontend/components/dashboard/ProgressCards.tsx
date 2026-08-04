"use client";
import Link from "next/link";
import type { ProjectState } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";

export function ProgressCards({
  state,
  pendingQuestions,
  artifactCount,
  projectId,
}: {
  state: ProjectState;
  pendingQuestions: number;
  artifactCount: number;
  projectId: string;
}) {
  const t = useT();
  const pct = progressPercent(state);
  const { completed, total } = stageCounts(state);
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.overallProgress")}</p>
        <p className="text-2xl font-bold text-violet-700">{pct}%</p>
        <div className="mt-2 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.completedStages")}</p>
        <p className="text-2xl font-bold">
          {completed} <span className="text-sm font-normal text-slate-400">/ {total}</span>
        </p>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.pendingQuestions")}</p>
        <p className="text-2xl font-bold text-amber-600">{pendingQuestions}</p>
        <Link href={`/projects/${projectId}/questions`} className="text-xs text-violet-600 hover:underline mt-2 inline-block">
          {t("dash.answerQuestions")}
        </Link>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.generatedArtifacts")}</p>
        <p className="text-2xl font-bold">{artifactCount}</p>
      </div>
    </div>
  );
}
