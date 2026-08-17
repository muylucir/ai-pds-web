"use client";
import { useState } from "react";
import type { Question } from "@/lib/api/types";
import { InlineMarkdown, Markdown } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";

// "B: 부연 설명" 값을 letter와 note로 분해한다. 첫 ": " 앞 토큰이 알려진
// non-Other letter일 때만 분해 — "Broker: ..." 같은 값은 null(전체가 Other
// 자유텍스트). 값 계약: 부연 있는 일반 보기 답변은 "letter: note" 단일 문자열
// (스펙 2026-07-21-option-annotation-design.md).
function splitLetterNote(value: string, letters: string[]): { letter: string; note: string } | null {
  const idx = value.indexOf(": ");
  if (idx === -1) return null;
  const head = value.slice(0, idx);
  return letters.includes(head) ? { letter: head, note: value.slice(idx + 2) } : null;
}

// 선택 컨트롤의 **보이는** 표시. 실제 input은 sr-only(위 label 주석의 포커스
// 이유)이므로 checkbox/radio 글리프가 화면에 나오지 않는다 — 그래서 복수선택
// 질문이 단일선택과 완전히 같은 모양으로 보였고(카드 텍스트가 바이트 단위로
// 동일), 사용자는 두 번째 보기를 눌러 보기 전까지 여러 개를 고를 수 있다는
// 사실을 알 수 없었다. 모양으로 모드를 말한다: 네모=복수, 동그라미=단일.
// aria-hidden — 진짜 상태는 sr-only input이 이미 스크린 리더에 알린다.
function SelectIndicator({ checked, multi }: { checked: boolean; multi: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`shrink-0 mt-0.5 w-5 h-5 border-2 flex items-center justify-center text-[11px] font-bold leading-none ${
        multi ? "rounded-md" : "rounded-full"
      } ${checked ? "bg-violet-600 border-violet-600 text-white" : "bg-white border-slate-300 text-transparent"}`}
    >
      {multi ? "✓" : "●"}
    </span>
  );
}

export function QuestionCard({
  question,
  value,
  onChange,
}: {
  question: Question;
  value: string;
  onChange: (next: string) => void;
}) {
  const t = useT();
  const name = `q${question.number}`;
  const multi = question.multi_select === true;

  // Defence in depth: at most ONE Other option, the last one. The backend
  // normalizes this at the ask_questions boundary, but this component also
  // renders interrupts restored from earlier sessions (GET /pending after a
  // refresh), which predate that normalization. Two is_other options share
  // this card's single `otherActive` state, so both render as "Other — 직접
  // 입력" (the real option's text vanishing) and selecting one silently
  // overwrites the other (ui-bug: question.png). Demote all but the last and
  // give the demoted ones a usable label.
  const options = (() => {
    const lastOther = question.options.map((o) => o.is_other).lastIndexOf(true);
    if (lastOther === -1) return question.options;
    return question.options.map((o, i) =>
      o.is_other && i !== lastOther
        ? { ...o, is_other: false, text: o.text.trim() || `${t("q.optionFallback")} ${o.letter}` }
        : o,
    );
  })();

  // Value contract stays a plain string (QuestionForm's answers dict/submit
  // path is unchanged): a single letter or (multi) comma-joined letters like
  // "A,C" for option picks, or raw free text for the Other option.
  const nonOtherLetters = options.filter((o) => !o.is_other).map((o) => o.letter);
  const isLetterList = (v: string) => v !== "" && v.split(",").filter(Boolean).every((t) => nonOtherLetters.includes(t));

  // single-select에서 "B: 부연" 형태를 분해 (multi에는 부연 없음 — 스펙의
  // YAGNI 결정: "A,C: 설명"은 파싱 모호성을 만들고, 승인/리뷰형 질문은
  // single-select로 온다).
  const letterNote = !multi ? splitLetterNote(value, nonOtherLetters) : null;

  // Free-text ("Other") mode is tracked EXPLICITLY, not inferred by comparing
  // `value` against option letters. Inferring it broke free text whose first
  // character happened to equal an option letter: typing "A" made value==="A",
  // which read as "option A selected", flipped out of Other mode, and blanked
  // the textarea — the first char was lost and option A rendered as checked.
  // Seeded from the incoming value's shape: a restored answer that is neither
  // a letter/letter-list NOR a "letter: note" form is free text; thereafter
  // only explicit user actions (picking an option vs. using Other) flip it.
  const [otherActive, setOtherActive] = useState(
    () => value !== "" && !isLetterList(value) && letterNote === null,
  );

  // With Other mode explicit, letter selection is only meaningful when NOT in
  // Other mode. Single-select: the picked letter (plain or the head of a
  // "letter: note" value), or "" in Other mode.
  const selectedLetter =
    !otherActive && !multi ? (nonOtherLetters.includes(value) ? value : (letterNote?.letter ?? "")) : "";
  // 선택된 보기의 부연(없으면 "").
  const note = !otherActive ? (letterNote?.note ?? "") : "";
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
      <legend className="sr-only">{t("q.legend")} {question.number}</legend>
      <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
        <span className="w-7 h-7 rounded-full bg-violet-600 text-white flex items-center justify-center text-xs font-bold" aria-hidden="true">
          {question.number}
        </span>
        <div>
          <h2 className="font-bold">
            Q{question.number}. <InlineMarkdown text={question.text} />
          </h2>
          <div className="flex items-center gap-2 mt-1">
            {/* 두 모드 모두 배지를 단다. 복수선택에만 달면 배지가 **없는**
                상태를 해석해야 하고, 단일선택 질문만 본 사용자는 그 규약을
                배울 기회가 없다. */}
            <span
              className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
                multi ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-500"
              }`}
            >
              {multi ? t("q.multiSelectBadge") : t("q.singleSelectBadge")}
            </span>
            {question.category && <p className="text-xs text-slate-400">{t("q.category")}: {question.category}</p>}
          </div>
        </div>
      </div>
      {/* 문항 앞의 설명 산문 — "왜 이걸 묻는가". 질문 파일에서 온 라운드에만
          있다(AskUserQuestion 페이로드에는 이 필드가 없다).

          마크다운으로 렌더하는 이유: 표가 들어온다. 실측한 확인 게이트 질문은
          "**위에 정리한** 페인 포인트 5건이 정확합니까?"이고 그 전제가 5행 표다 —
          평문으로 뿌리면 답할 수 없는 질문이 된다. */}
      {question.context?.trim() && (
        <div className="px-6 pt-4 text-sm text-slate-600 border-b border-slate-100 pb-4">
          <Markdown text={question.context} />
        </div>
      )}
      <div className="p-6 space-y-3">
        {options.map((opt) => {
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
                <label className="relative flex items-center gap-3 cursor-pointer">
                  <input
                    type={multi ? "checkbox" : "radio"}
                    name={name}
                    className="sr-only peer"
                    checked={otherActive}
                    onChange={activateOther}
                  />
                  <SelectIndicator checked={otherActive} multi={multi} />
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <p className="font-medium">{t("q.otherOption")}</p>
                </label>
                <textarea
                  aria-label={t("q.otherAria")}
                  rows={2}
                  value={otherActive ? value : ""}
                  onFocus={() => setOtherActive(true)}
                  onChange={(e) => { setOtherActive(true); onChange(e.target.value); }}
                  placeholder={t("q.otherPlaceholder")}
                  className="mt-2 ml-10 w-[calc(100%-2.5rem)] text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                />
              </div>
            );
          }
          const checked = multi ? multiSelected.has(opt.letter) : selectedLetter === opt.letter;
          return (
            <div key={opt.letter}>
              {/* relative: Other 라벨과 동일한 이유 (sr-only absolute 인풋 가둠) */}
              <label className="relative block cursor-pointer">
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
                  <SelectIndicator checked={checked} multi={multi} />
                  <span className="shrink-0 w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center text-sm font-bold text-slate-500">
                    {opt.letter}
                  </span>
                  <div>
                    <p className="font-medium">
                      <InlineMarkdown text={opt.text} />
                      {opt.recommended && (
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 ml-1">{t("q.aiRecommended")}</span>
                      )}
                    </p>
                  </div>
                </div>
              </label>
              {/* 부연 설명(선택): 선택된 보기 아래에만 펼쳐진다. textarea를
                  label 밖 형제로 두는 것 필수 — label 안에 중첩하면 클릭
                  포커스가 sr-only 라디오로 가고 첫 키 입력이 유실된다(동일
                  회귀를 Other에서 이미 수정). 값 계약: 입력 시
                  "letter: note", 비우면 letter만. */}
              {checked && !multi && (
                <textarea
                  aria-label={`${t("q.noteAriaPrefix")} ${opt.letter} ${t("q.noteAriaSuffix")}`}
                  rows={2}
                  value={note}
                  onChange={(e) =>
                    onChange(e.target.value === "" ? opt.letter : `${opt.letter}: ${e.target.value}`)
                  }
                  placeholder={t("q.notePlaceholder")}
                  className="mt-2 ml-10 w-[calc(100%-2.5rem)] text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
                />
              )}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
