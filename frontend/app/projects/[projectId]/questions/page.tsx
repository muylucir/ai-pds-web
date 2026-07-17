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

  // useAsync only resets `loading`/`error` when deps change — it keeps the
  // previous `data` around until the new fetch resolves. Tag each result with
  // the `active` path it was fetched for so we can tell a fresh (current
  // file's) result apart from a stale one still in flight for a PREVIOUS
  // `active` (e.g. right after switching files via `?file=`). Rendering the
  // stale data would key-remount QuestionForm against the WRONG file's
  // answers, seeding it with the previous file's answer map.
  const file = useAsync(
    () =>
      active
        ? getQuestionFile(projectId, active).then((data) => ({ path: active, data }))
        : Promise.resolve(null),
    [projectId, active],
  );
  const loadedFile = file.data && file.data.path === active ? file.data.data : null;

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
  const fileLoadError = file.error && !notFound;

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
        {files.error && (
          <p className="text-sm text-rose-600">질문 목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {notFound && <p className="text-sm text-rose-600">질문 파일을 찾을 수 없습니다.</p>}
        {fileLoadError && (
          <p className="text-sm text-rose-600">질문을 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {submitError && <p className="text-sm text-rose-600 mb-4">{submitError}</p>}
        {!files.loading && !files.error && list.length === 0 && (
          <p className="text-sm text-slate-400">아직 답변할 질문이 없습니다.</p>
        )}

        {loadedFile && loadedFile.parse_ok && (
          <QuestionForm key={active} file={loadedFile} onSubmit={submitAnswers} submitting={submitting} />
        )}
        {loadedFile && !loadedFile.parse_ok && (
          <RawMarkdownFallback key={active} file={loadedFile} onSubmit={submitFreeText} submitting={submitting} />
        )}
      </main>
    </>
  );
}
