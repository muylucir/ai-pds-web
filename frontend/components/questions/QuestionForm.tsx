"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { answeredCount } from "@/lib/stageProgress";
import { Markdown } from "@/components/Markdown";
import { QuestionCard } from "./QuestionCard";
import { useT } from "@/lib/i18n/provider";

export function QuestionForm({
  file,
  onSubmit,
  submitting,
  readOnly = false,
}: {
  file: QuestionFile;
  onSubmit: (answers: Record<string, string>) => void;
  submitting: boolean;
  /** Read-only member. The questions and answers so far are visible; no input or submit. */
  readOnly?: boolean;
}) {
  const t = useT();
  const [answers, setAnswers] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const q of file.questions) seed[String(q.number)] = q.answer ?? "";
    return seed;
  });

  const answered = Object.values(answers).filter((v) => v.trim() !== "").length;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const filled: Record<string, string> = {};
    for (const [k, v] of Object.entries(answers)) if (v.trim() !== "") filled[k] = v;
    onSubmit(filled);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{file.name}</h1>
        </div>
        <p className="text-sm text-slate-500">
          <b className="text-violet-700">{answered}</b> / {file.questions.length} {t("q.answeredCount")}
        </p>
      </div>
      <div className="h-2 rounded-full bg-slate-200 overflow-hidden" role="progressbar" aria-valuenow={answered} aria-valuemin={0} aria-valuemax={file.questions.length}>
        <div className="h-full bg-violet-500 rounded-full transition-all" style={{ width: `${file.questions.length ? (answered / file.questions.length) * 100 : 0}%` }} />
      </div>

      {file.preamble && (
        <div className="flex gap-3 bg-sky-50 border border-sky-200 rounded-xl p-4 text-sm">
          <span className="text-lg" aria-hidden="true">💡</span>
          <div className="text-sky-800">
            <Markdown text={file.preamble} className="prose-p:text-sky-800 prose-li:text-sky-800 prose-strong:text-sky-900" />
          </div>
        </div>
      )}

      {/* fieldset disabled: locks all choices and input fields in the cards at once. */}
      <fieldset disabled={readOnly} className="space-y-6">
        {file.questions.map((q) => (
          <QuestionCard
            key={q.number}
            question={q}
            value={answers[String(q.number)] ?? ""}
            onChange={(next) => setAnswers((prev) => ({ ...prev, [String(q.number)]: next }))}
          />
        ))}
      </fieldset>

      {/* Submit bar: translucency + blur lets the question text behind show through on scroll,
          making it look "broken" (especially in narrow scroll containers like the right panel)
          — opaque white. */}
      {readOnly ? (
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          {t("readOnly.answers")}
        </p>
      ) : (
        <div className="sticky bottom-0 bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between gap-3 shadow-lg shadow-slate-200/50">
          <div className="text-xs text-slate-500 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500" aria-hidden="true" />
            {t("q.auditNotice")}
          </div>
          <button type="submit" disabled={submitting} className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold">
            {t("q.submitAnswers")}
          </button>
        </div>
      )}
    </form>
  );
}
