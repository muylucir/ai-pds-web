"use client";
import { useState } from "react";
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

  // Value contract stays a plain string (QuestionForm's answers dict/submit
  // path is unchanged): a single letter or (multi) comma-joined letters like
  // "A,C" for option picks, or raw free text for the Other option.
  const nonOtherLetters = question.options.filter((o) => !o.is_other).map((o) => o.letter);
  const isLetterList = (v: string) => v !== "" && v.split(",").filter(Boolean).every((t) => nonOtherLetters.includes(t));

  // Free-text ("Other") mode is tracked EXPLICITLY, not inferred by comparing
  // `value` against option letters. Inferring it broke free text whose first
  // character happened to equal an option letter: typing "A" made value==="A",
  // which read as "option A selected", flipped out of Other mode, and blanked
  // the textarea — the first char was lost and option A rendered as checked.
  // Seeded from the incoming value's shape (a restored answer that isn't a
  // known letter/letter-list is free text); thereafter only explicit user
  // actions (picking an option vs. using Other) flip it.
  const [otherActive, setOtherActive] = useState(() => value !== "" && !isLetterList(value));

  // With Other mode explicit, letter selection is only meaningful when NOT in
  // Other mode. Single-select: the picked letter, or "" in Other mode.
  const selectedLetter = !otherActive && !multi && nonOtherLetters.includes(value) ? value : "";
  // Multi-select: the checked letters, or empty while Other free text is in use.
  const multiSelected = new Set(!otherActive && multi && isLetterList(value) ? value.split(",").filter(Boolean) : []);

  function selectLetter(letter: string) {
    setOtherActive(false);
    onChange(letter);
  }

  function toggleLetter(letter: string) {
    setOtherActive(false);
    const next = new Set(multiSelected);
    if (next.has(letter)) next.delete(letter);
    else next.add(letter);
    onChange([...next].sort().join(","));
  }

  function activateOther() {
    setOtherActive(true);
    onChange("");
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
            // 중요: textarea를 라디오/체크박스의 <label> 안에 중첩하지 않는다.
            // 중첩하면 Other 영역 클릭 시 포커스가 textarea가 아니라 sr-only
            // 라디오로 가고, textarea 첫 키 입력이 label→control 활성화로
            // 가로채진다. label에는 선택 트리거(라디오 + 배지/제목)만 두고,
            // textarea는 label 밖 형제로 배치한다. textarea 포커스 시 Other
            // 모드를 활성화해 "클릭 → 바로 타이핑"이 그대로 동작하게 한다.
            return (
              <div
                key={opt.letter}
                className="rounded-xl border-2 border-dashed border-slate-200 p-4 hover:border-violet-200"
              >
                {/* relative 필수: sr-only 인풋은 absolute라, 부모가 static이면
                    문서 루트 기준 좌표로 배치돼 <html>에 유령 오버플로를 만든다
                    → 라벨 클릭(=input.focus())마다 문서가 스크롤되며 헤더가 말려
                    올라감(ui-bug.png 회귀). */}
                <label className="relative flex gap-3 cursor-pointer">
                  <input
                    type={multi ? "checkbox" : "radio"}
                    name={name}
                    className="sr-only peer"
                    checked={otherActive}
                    onChange={activateOther}
                  />
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <p className="font-medium">Other — 직접 입력</p>
                </label>
                <textarea
                  aria-label="기타 답변 직접 입력"
                  rows={2}
                  value={otherActive ? value : ""}
                  onFocus={() => setOtherActive(true)}
                  onChange={(e) => { setOtherActive(true); onChange(e.target.value); }}
                  placeholder="위 선택지에 없다면 직접 설명해 주세요…"
                  className="mt-2 ml-10 w-[calc(100%-2.5rem)] text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                />
              </div>
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
                onChange={() => (multi ? toggleLetter(opt.letter) : selectLetter(opt.letter))}
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
