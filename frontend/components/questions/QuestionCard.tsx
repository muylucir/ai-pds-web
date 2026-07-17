"use client";
import type { Question } from "@/lib/api/types";

export function QuestionCard({
  question,
  value,
  onChange,
}: {
  question: Question;
  value: string;
  onChange: (next: string) => void;
}) {
  const name = `q${question.number}`;
  // The selected non-Other letter, or "" when the Other free-text is in use.
  const selectedLetter = question.options.some((o) => o.letter === value && !o.is_other) ? value : "";

  return (
    <fieldset className="bg-white rounded-xl border-2 border-violet-300 shadow-sm shadow-violet-100 overflow-hidden">
      <legend className="sr-only">질문 {question.number}</legend>
      <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
        <span className="w-7 h-7 rounded-full bg-violet-600 text-white flex items-center justify-center text-xs font-bold" aria-hidden="true">
          {question.number}
        </span>
        <div>
          <h2 className="font-bold">Q{question.number}. {question.text}</h2>
          {question.category && <p className="text-xs text-slate-400 mt-0.5">카테고리: {question.category}</p>}
        </div>
      </div>
      <div className="p-6 space-y-3">
        {question.options.map((opt) => {
          if (opt.is_other) {
            const otherActive = selectedLetter === "";
            return (
              <label key={opt.letter} className="block cursor-pointer">
                <input
                  type="radio"
                  name={name}
                  className="sr-only peer"
                  checked={otherActive && value !== ""}
                  onChange={() => onChange("")}
                />
                <div className="flex gap-3 rounded-xl border-2 border-dashed border-slate-200 p-4 hover:border-violet-200">
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <div className="flex-1">
                    <p className="font-medium">Other — 직접 입력</p>
                    <textarea
                      aria-label="기타 답변 직접 입력"
                      rows={2}
                      value={otherActive ? value : ""}
                      onChange={(e) => onChange(e.target.value)}
                      placeholder="위 선택지에 없다면 직접 설명해 주세요…"
                      className="mt-2 w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                </div>
              </label>
            );
          }
          const checked = selectedLetter === opt.letter;
          return (
            <label key={opt.letter} className="block cursor-pointer">
              <input
                type="radio"
                name={name}
                value={opt.letter}
                className="sr-only peer"
                checked={checked}
                onChange={() => onChange(opt.letter)}
              />
              <div
                className={`flex gap-3 rounded-xl border-2 p-4 hover:border-violet-200 ${
                  checked ? "border-violet-600 bg-violet-50" : "border-slate-200"
                }`}
              >
                <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                  {opt.letter}
                </span>
                <div>
                  <p className="font-medium">
                    {opt.text}
                    {opt.recommended && (
                      <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 ml-1">★ AI 추천</span>
                    )}
                  </p>
                </div>
              </div>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
