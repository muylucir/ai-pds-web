"use client";
import type { ProjectState } from "@/lib/api/types";
import { progressPercent, stageCounts } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";

export function ProgressCards({
  state,
  questionFileCount,
  artifactCount,
}: {
  state: ProjectState;
  // The number of question **files** written. Not the number unanswered — questions are
  // delivered via AskUserQuestion and answers come back through that round trip, so the
  // file's `[Answer]:` stays empty forever (the override section of discovery-config/CLAUDE.md).
  // Counting this value as "pending questions" would leave the number unchanged even after
  // the user answered everything.
  questionFileCount: number;
  artifactCount: number;
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
      {/* A record card — it uses neither amber (the color that demands attention) nor an
          answer link. `/projects/{id}/questions` is retired and redirects to /workspace, and
          the question files themselves cannot be edited in the UI. */}
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.questionRecords")}</p>
        <p className="text-2xl font-bold">{questionFileCount}</p>
        <p className="text-xs text-slate-400 mt-2">{t("dash.questionRecordsHint")}</p>
      </div>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <p className="text-xs text-slate-500 mb-1">{t("dash.generatedArtifacts")}</p>
        <p className="text-2xl font-bold">{artifactCount}</p>
      </div>
    </div>
  );
}
