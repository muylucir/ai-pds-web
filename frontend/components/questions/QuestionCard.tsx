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
  const multi = question.multi_select === true;

  // The selected non-Other letter, or "" when the Other free-text is in use.
  // (Single-select only — see multiSelected/multiOtherActive below for multi.)
  const selectedLetter = question.options.some((o) => o.letter === value && !o.is_other) ? value : "";

  // --- multi-select ---
  // Value contract stays a plain string (QuestionForm's answers dict/submit
  // path is unchanged): letters joined with "," in alphabetical order, e.g.
  // "A,C". The Other(X) option's free-text value is unprefixed raw text —
  // identical to the single-select convention above — which means a
  // comma-joined multi value and an Other free-text value can only be told
  // apart by checking whether every comma-split token is a known non-Other
  // letter. Consequently Other CANNOT be combined with other picks in multi
  // mode (there's no "A,X:<text>" form in scope): checking a letter clears
  // any active Other free text, and using Other clears any checked letters.
  // Other is therefore a sole selection in multi mode, same as single mode.
  const nonOtherLetters = question.options.filter((o) => !o.is_other).map((o) => o.letter);
  const isLetterList = (v: string) => v.split(",").filter(Boolean).every((t) => nonOtherLetters.includes(t));
  const multiSelected = new Set(multi && isLetterList(value) ? value.split(",").filter(Boolean) : []);
  const multiOtherActive = multi && value !== "" && !isLetterList(value);

  function toggleLetter(letter: string) {
    const next = new Set(multiSelected);
    if (next.has(letter)) next.delete(letter);
    else next.add(letter);
    onChange([...next].sort().join(","));
  }

  return (
    // relative 필수: sr-only <legend>(absolute)를 이 fieldset 안에 가둔다.
    // static이면 legend가 문서 루트 기준으로 배치되어 <html>에 유령
    // 오버플로를 만들고, 라벨 클릭(=input.focus())마다 문서가 스크롤되며
    // 헤더가 말려 올라간다(ui-bug.png). 라벨의 relative와 세트.
    <fieldset className="relative bg-white rounded-xl border-2 border-violet-300 shadow-sm shadow-violet-100 overflow-hidden">
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
            const otherActive = multi ? multiOtherActive : selectedLetter === "";
            return (
              // relative 필수: sr-only 인풋은 absolute라, 부모가 static이면
              // 문서 루트 기준 좌표(질문지 전체 높이)로 배치되어 <html>에
              // 유령 오버플로를 만든다 → 라벨 클릭(=input.focus())마다 문서가
              // 그 좌표로 스크롤되며 헤더가 말려 올라감(ui-bug.png 회귀).
              <label key={opt.letter} className="relative block cursor-pointer">
                <input
                  type={multi ? "checkbox" : "radio"}
                  name={name}
                  className="sr-only peer"
                  checked={multi ? otherActive : otherActive && value !== ""}
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
          const checked = multi ? multiSelected.has(opt.letter) : selectedLetter === opt.letter;
          return (
            // relative: 위 Other 라벨과 동일한 이유 (sr-only absolute 인풋 가둠)
            <label key={opt.letter} className="relative block cursor-pointer">
              <input
                type={multi ? "checkbox" : "radio"}
                name={name}
                value={opt.letter}
                className="sr-only peer"
                checked={checked}
                onChange={() => (multi ? toggleLetter(opt.letter) : onChange(opt.letter))}
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
