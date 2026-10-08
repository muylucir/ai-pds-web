import type { ProjectState, QuestionFile } from "@/lib/api/types";

// Presentational math ONLY — progress percentages and counts for the dashboard
// cards / wizard progress bar. No methodology: it does not know which stage
// belongs to which capability (the backend rolls that up), only how many
// capabilities are marked completed. A capability this project's path skips is
// not counted — it can never complete.
export function stageCounts(state: Pick<ProjectState, "capabilities">): { completed: number; total: number } {
  const onPath = state.capabilities.filter((c) => c.status !== "not_applicable");
  const completed = onPath.filter((c) => c.status === "completed").length;
  return { completed, total: onPath.length };
}

export function progressPercent(state: Pick<ProjectState, "capabilities">): number {
  const { completed, total } = stageCounts(state);
  if (total === 0) return 0;
  return Math.round((completed / total) * 100);
}

export function answeredCount(qf: QuestionFile): { answered: number; total: number } {
  const total = qf.questions.length;
  const answered = qf.questions.filter((q) => (q.answer ?? "").trim() !== "").length;
  return { answered, total };
}
