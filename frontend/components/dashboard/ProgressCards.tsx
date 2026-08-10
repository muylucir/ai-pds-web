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
  // 작성된 질문 **파일** 수. 미답변 수가 아니다 — 질문은 AskUserQuestion으로
  // 전달되고 답변도 그 왕복으로 돌아오므로 파일의 `[Answer]:`는 영구히 비어
  // 있다(discovery-config/CLAUDE.md의 override 섹션). 이 값을 "대기 중인 질문"
  // 으로 세면 사용자가 전부 답한 뒤에도 숫자가 그대로 남는다.
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
      {/* 기록 카드다 — 앰버(주의를 요구하는 색)도, 답변 링크도 쓰지 않는다.
          `/projects/{id}/questions`는 은퇴해 /workspace로 리다이렉트되고, 질문
          파일 자체는 UI에서 편집할 수 없다. */}
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
