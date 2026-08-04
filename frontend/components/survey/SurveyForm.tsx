"use client";
import { useState } from "react";
import type { AnswerValue, SurveyQuestion } from "@/lib/api/surveys";
import { useT } from "@/lib/i18n/provider";

const SCALE_VALUES = [1, 2, 3, 4, 5];

export function SurveyForm({ questions, onSubmit, submitting }: {
  questions: SurveyQuestion[];
  onSubmit: (answers: Record<string, AnswerValue>) => void;
  submitting: boolean;
}) {
  const t = useT();
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({});
  const [showError, setShowError] = useState(false);

  function set(id: string, value: AnswerValue) {
    setAnswers((prev) => ({ ...prev, [id]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const filled: Record<string, AnswerValue> = {};
    for (const [k, v] of Object.entries(answers)) {
      // Drop untouched optional text so the backend doesn't store empty strings.
      if (typeof v === "string" && v.trim() === "") continue;
      filled[k] = v;
    }
    const missing = questions.filter((q) => q.required && filled[q.id] === undefined);
    if (missing.length > 0) {
      setShowError(true);
      return;
    }
    setShowError(false);
    onSubmit(filled);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {questions.map((q, idx) => (
        <fieldset key={q.id} className="rounded-xl border border-slate-200 p-4">
          <legend className="px-1 text-sm font-medium text-slate-700">
            {idx + 1}. <span>{q.text}</span>
            {q.required && <span className="text-rose-500 ml-1">*</span>}
          </legend>

          {q.type === "scale" && (
            <div className="flex gap-4 mt-3" role="radiogroup" aria-label={q.text}>
              {SCALE_VALUES.map((v) => (
                <label key={v} className="flex flex-col items-center gap-1 text-xs text-slate-500">
                  <input type="radio" name={q.id} value={v} aria-label={String(v)}
                         checked={answers[q.id] === v}
                         onChange={() => set(q.id, v)} />
                  {v}
                </label>
              ))}
            </div>
          )}

          {q.type === "choice" && (
            <div className="space-y-2 mt-3">
              {q.options.map((opt) => (
                <label key={opt} className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="radio" name={q.id} value={opt} aria-label={opt}
                         checked={answers[q.id] === opt}
                         onChange={() => set(q.id, opt)} />
                  {opt}
                </label>
              ))}
            </div>
          )}

          {q.type === "text" && (
            <textarea id={q.id} aria-label={q.text} rows={3} maxLength={2000}
                      value={String(answers[q.id] ?? "")}
                      onChange={(e) => set(q.id, e.target.value)}
                      className="mt-3 w-full rounded-lg border border-slate-200 p-2 text-sm" />
          )}
        </fieldset>
      ))}

      {showError && (
        <p className="text-sm text-rose-600">{t("surveyForm.requiredMissing")}</p>
      )}

      <button type="submit" disabled={submitting}
              className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50">
        {submitting ? t("surveyForm.submitting") : t("surveyForm.submit")}
      </button>
    </form>
  );
}
