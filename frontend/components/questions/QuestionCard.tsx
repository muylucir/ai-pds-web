"use client";
import { useState } from "react";
import type { Question } from "@/lib/api/types";
import { InlineMarkdown, Markdown } from "@/components/Markdown";
import { useT } from "@/lib/i18n/provider";

// Splits a "B: 부연 설명" (B: elaboration) value into letter and note. It splits only when the
// token before the first ": " is a known non-Other letter — a value like "Broker: ..." gives null
// (the whole thing is Other free text). Value contract: an answer to an ordinary option with an
// elaboration is the single string "letter: note" (spec 2026-07-21-option-annotation-design.md).
function splitLetterNote(value: string, letters: string[]): { letter: string; note: string } | null {
  const idx = value.indexOf(": ");
  if (idx === -1) return null;
  const head = value.slice(0, idx);
  return letters.includes(head) ? { letter: head, note: value.slice(idx + 2) } : null;
}

// The **visible** indicator of the selection control. The real input is sr-only (for the focus
// reason in the label comment above), so no checkbox/radio glyph appears on screen — so
// multi-select questions looked exactly like single-select ones (the card text was
// byte-for-byte identical), and the user could not tell that several could be chosen until
// they clicked a second option. The shape states the mode: square = multi, circle = single.
// aria-hidden — the sr-only input already announces the real state to screen readers.
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

  // A question with **no options at all** = a free-response question. It is not a parse failure.
  //
  // **Measured (2026-08-18, ws-k).** Mode A (free description) of Envision Step 0.2
  // normally asks without choices, and the agent wrote the file that way:
  // "A/B/C 선택지가 없습니다 — 각 항목은 `[Answer]:` 뒤에 문장으로 답해 주십시오."
  // The parser read that file correctly (5 questions, normal question text, options=0). But
  // this component only iterates over the options array, so the card body was **empty** —
  // nothing to choose and no field to type in, so the user had no way at all to answer. The
  // badge was saying "하나만 선택" (select one) (with 0 things to choose).
  //
  // Free text itself is already supported via the `X) Other` option. What was missing was the
  // case of a "correctly parsed 0-option question".
  const freeform = options.length === 0;

  // Value contract stays a plain string (QuestionForm's answers dict/submit
  // path is unchanged): a single letter or (multi) comma-joined letters like
  // "A,C" for option picks, or raw free text for the Other option.
  const nonOtherLetters = options.filter((o) => !o.is_other).map((o) => o.letter);
  const isLetterList = (v: string) => v !== "" && v.split(",").filter(Boolean).every((t) => nonOtherLetters.includes(t));

  // In single-select, split the "B: 부연" (B: elaboration) form (multi has no elaboration — the
  // spec's YAGNI decision: "A,C: 설명" creates parsing ambiguity, and approval/review questions
  // come as single-select).
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
  // The selected option's elaboration ("" if none).
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
    // relative is required: it confines the sr-only <legend> (absolute) within this fieldset.
    // If static, the legend is positioned relative to the document root, creating a ghost
    // overflow on <html>, and every label click (=input.focus()) scrolls the document and
    // rolls the header up (ui-bug.png). Paired with the label's relative.
    <fieldset className="relative bg-white rounded-xl border-2 border-violet-300 shadow-sm shadow-violet-100 overflow-hidden">
      <legend className="sr-only">{t("q.legend")} {question.number}</legend>
      {/* Explanatory prose before the question — "why this is being asked". Present only in
          rounds that came from a question file (the AskUserQuestion payload has no such field).

          **It comes above the question sentence (2026-08-18).** In the file, this prose is
          **before** the `## Question N` heading (that is the only condition under which this
          field is filled, parsers/questions.py), and reading the background then the question
          is the writer's order. Previously it was rendered below the title, so the card read as
          "question → 1,126 characters of background + table → input field" — in a measurement
          (ws-k) the user misdiagnosed it as a parse error.

          Why it is rendered as markdown: tables come in. The measured confirmation gate question
          is "**위에 정리한** 페인 포인트 5건이 정확합니까?" (are the 5 pain points **summarized
          above** correct?), and its premise is a 5-row table — rendered as plain text, it
          becomes a question that cannot be answered.

          A background color distinguishes it from the question: placed on the same white
          background, the boundary between where the background ends and where the question
          begins disappears. */}
      {question.context?.trim() && (
        <div className="px-6 py-4 bg-slate-50 text-sm text-slate-600 border-b border-slate-100">
          <Markdown text={question.context} />
        </div>
      )}
      <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
        <span className="w-7 h-7 rounded-full bg-violet-600 text-white flex items-center justify-center text-xs font-bold" aria-hidden="true">
          {question.number}
        </span>
        <div>
          <h2 className="font-bold">
            Q{question.number}. <InlineMarkdown text={question.text} />
          </h2>
          <div className="flex items-center gap-2 mt-1">
            {/* Both modes get a badge. If only multi-select had one, the state **without** a
                badge would have to be interpreted, and a user who has only seen single-select
                questions has no chance to learn that convention. */}
            {/* Free-response questions get no selection badge. Saying "하나만 선택" (select one)
                when there are 0 things to choose makes the user read it as a breakage where the
                options failed to show — in the measurement that was in fact the screen. */}
            <span
              className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
                freeform
                  ? "bg-sky-50 text-sky-700"
                  : multi
                    ? "bg-violet-100 text-violet-700"
                    : "bg-slate-100 text-slate-500"
              }`}
            >
              {freeform
                ? t("q.freeformLabel")
                : multi
                  ? t("q.multiSelectBadge")
                  : t("q.singleSelectBadge")}
            </span>
            {question.category && <p className="text-xs text-slate-400">{t("q.category")}: {question.category}</p>}
          </div>
        </div>
      </div>
      <div className="p-6 space-y-3">
        {/* Free response: a single input field with no radios or checkboxes. Why it binds
            directly to `value`: `otherActive` is state that tracks the relationship with the
            options, and there are no options here — inserting that state would create a path
            where a restored answer looks empty on the first render. */}
        {freeform && (
          <textarea
            aria-label={`${t("q.legend")} ${question.number} ${t("q.freeformLabel")}`}
            rows={4}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={t("q.freeformPlaceholder")}
            className="w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
          />
        )}
        {options.map((opt) => {
          if (opt.is_other) {
            // Important: do not nest the textarea inside the radio/checkbox <label>.
            // If nested, clicking the Other area sends focus to the sr-only radio instead of
            // the textarea, and the textarea's first keystroke is intercepted as label→control
            // activation. Put only the selection trigger (radio + badge/title) in the label,
            // and place the textarea as a sibling outside the label. Activating Other mode on
            // textarea focus keeps "click → type right away" working as is.
            return (
              <div
                key={opt.letter}
                className="rounded-xl border-2 border-dashed border-slate-200 p-4 hover:border-violet-200"
              >
                {/* relative is required: the sr-only input is absolute, so if the parent is
                    static it is positioned in document-root coordinates, creating a ghost
                    overflow on <html> → every label click (=input.focus()) scrolls the document
                    and rolls the header up (ui-bug.png regression). */}
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
              {/* relative: same reason as the Other label (confines the sr-only absolute input) */}
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
              {/* Elaboration (optional): expands only under the selected option. Placing the
                  textarea as a sibling outside the label is required — nesting it inside the
                  label sends click focus to the sr-only radio and loses the first keystroke
                  (the same regression already fixed in Other). Value contract: on input,
                  "letter: note"; when emptied, the letter only. */}
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
