"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { answeredCount } from "@/lib/stageProgress";
import { Markdown } from "@/components/Markdown";
import { QuestionCard } from "./QuestionCard";

export function QuestionForm({
  file,
  onSubmit,
  submitting,
}: {
  file: QuestionFile;
  onSubmit: (answers: Record<string, string>) => void;
  submitting: boolean;
}) {
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
          <b className="text-violet-700">{answered}</b> / {file.questions.length} 답변 완료
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

      {file.questions.map((q) => (
        <QuestionCard
          key={q.number}
          question={q}
          value={answers[String(q.number)] ?? ""}
          onChange={(next) => setAnswers((prev) => ({ ...prev, [String(q.number)]: next }))}
        />
      ))}

      {/* 제출 바: 반투명+blur는 스크롤 시 뒤 질문 텍스트가 비쳐 "깨진" 것처럼
          보인다(우측 패널처럼 좁은 스크롤 컨테이너에서 특히) — 불투명 흰색. */}
      <div className="sticky bottom-0 bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between gap-3 shadow-lg shadow-slate-200/50">
        <div className="text-xs text-slate-500 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500" aria-hidden="true" />
          모든 답변은 audit.md에 원문 그대로 기록됩니다
        </div>
        <button type="submit" disabled={submitting} className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold">
          답변 제출 → AI 검증
        </button>
      </div>
    </form>
  );
}
