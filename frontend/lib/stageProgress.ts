import type { ProjectState, QuestionFile } from "@/lib/api/types";

// Presentational math ONLY — progress percentages and counts for the dashboard
// cards / wizard progress bar. No methodology: it does not know stage order or
// meaning, only how many are marked completed in the backend payload.
export function stageCounts(state: ProjectState): { completed: number; total: number } {
  const total = state.stages.length;
  const completed = state.stages.filter((s) => s.status === "completed").length;
  return { completed, total };
}

export function progressPercent(state: ProjectState): number {
  const { completed, total } = stageCounts(state);
  if (total === 0) return 0;
  return Math.round((completed / total) * 100);
}

export function answeredCount(qf: QuestionFile): { answered: number; total: number } {
  const total = qf.questions.length;
  const answered = qf.questions.filter((q) => (q.answer ?? "").trim() !== "").length;
  return { answered, total };
}
