"use client";
import { use, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { QuestionForm } from "@/components/questions/QuestionForm";
import { RawMarkdownFallback } from "@/components/questions/RawMarkdownFallback";
import { ClarificationBanner } from "@/components/questions/ClarificationBanner";
import {
  listQuestionFiles,
  getQuestionFile,
  putAnswers,
  postMessage,
  ApiError,
} from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

const isClarification = (p: string) => p.endsWith("-clarification-questions.md");

export default function QuestionsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const search = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const files = useAsync(() => listQuestionFiles(projectId), [projectId]);
  const list = files.data ?? [];
  const clarification = list.find(isClarification);
  const requested = search.get("file");
  const active =
    (requested && list.includes(requested) ? requested : undefined) ??
    list.find((p) => !isClarification(p)) ??
    list[0];

  const file = useAsync(
    () => (active ? getQuestionFile(projectId, active) : Promise.resolve(null)),
    [projectId, active],
  );

  async function submitAnswers(answers: Record<string, string>) {
    if (!active) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await putAnswers(projectId, active, answers);
      file.reload();
      files.reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setSubmitError("답변 형식이 올바르지 않습니다.");
      else if (err instanceof ApiError && err.status === 404) setSubmitError("질문 파일을 찾을 수 없습니다.");
      else setSubmitError("답변 제출에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitFreeText(text: string) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await postMessage(projectId, text);
      file.reload();
      files.reload();
    } catch {
      setSubmitError("답변 제출에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  const notFound = file.error instanceof ApiError && file.error.status === 404;

  return (
    <>
      <AppHeader activeTab="questions" projectId={projectId} />
      <main className="max-w-4xl mx-auto px-6 py-8">
        {clarification && (
          <ClarificationBanner projectId={projectId} path={clarification} preamble={null} />
        )}

        {list.length > 1 && (
          <div className="flex flex-wrap gap-2 mb-6">
            {list.map((p) => {
              const activeBtn = p === active;
              return (
                <a
                  key={p}
                  href={`/projects/${projectId}/questions?file=${encodeURIComponent(p)}`}
                  className={`text-xs px-3 py-1.5 rounded-lg border ${
                    activeBtn ? "bg-violet-600 text-white border-violet-600" : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {p.split("/").pop()}
                </a>
              );
            })}
          </div>
        )}

        {files.loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {notFound && <p className="text-sm text-rose-600">질문 파일을 찾을 수 없습니다.</p>}
        {submitError && <p className="text-sm text-rose-600 mb-4">{submitError}</p>}
        {!files.loading && list.length === 0 && (
          <p className="text-sm text-slate-400">아직 답변할 질문이 없습니다.</p>
        )}

        {file.data && file.data.parse_ok && (
          <QuestionForm file={file.data} onSubmit={submitAnswers} submitting={submitting} />
        )}
        {file.data && !file.data.parse_ok && (
          <RawMarkdownFallback file={file.data} onSubmit={submitFreeText} submitting={submitting} />
        )}
      </main>
    </>
  );
}
