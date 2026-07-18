"use client";
import { getQuestionFile } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { answeredCount } from "@/lib/stageProgress";
import { QuestionSummaryCard } from "./QuestionSummaryCard";
import { ClarificationCard } from "./ClarificationCard";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

// Thin data container: fetches the QuestionFile the file_changed path pointed
// to, then picks a PRESENTATIONAL card by data shape (not by filename alone —
// the filename already routed us here via useTurnStream's card:"questions").
export function QuestionCardSlot({
  projectId,
  path,
  onChoose,
  busy,
}: {
  projectId: string;
  path: string;
  onChoose: (text: string) => void;
  busy: boolean;
}) {
  const { data: file, loading, error } = useAsync(() => getQuestionFile(projectId, path), [projectId, path]);

  if (loading && !file) return <p className="text-xs text-slate-400 ml-1">불러오는 중…</p>;
  if (error) return <p className="text-xs text-rose-600 ml-1">질문을 불러오지 못했습니다.</p>;
  if (!file) return null;

  const { answered, total } = answeredCount(file);
  const allAnswered = total > 0 && answered === total;

  if (allAnswered) return <QuestionSummaryCard file={file} />;

  if (path.endsWith("-clarification-questions.md")) {
    return <ClarificationCard file={file} onChoose={onChoose} busy={busy} />;
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm flex items-center justify-between gap-3">
      <p className="text-slate-600">{basename(path)}에 답변이 필요합니다</p>
      <a
        href={`/projects/${projectId}/questions?file=${encodeURIComponent(path)}`}
        className="text-xs text-violet-600 font-medium shrink-0 hover:text-violet-700"
      >
        질문 답변하러 가기 →
      </a>
    </div>
  );
}
