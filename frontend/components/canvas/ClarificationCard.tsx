"use client";
import type { QuestionFile } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

// Amber contradiction/clarification card (mockup 04's "답변 간 모순 감지"
// idiom). Rendered when the caller (QuestionCardSlot, Task 3) has determined
// `file` has an unanswered question AND its path is a *-clarification-
// questions.md file — this component just renders the data + wires option
// buttons back to the single `onChoose` callback (the page relays the chosen
// text through the SAME useTurnStream `send`, Task 5 — no separate submit path).
export function ClarificationCard({
  file,
  onChoose,
  busy,
}: {
  file: QuestionFile;
  onChoose: (text: string) => void;
  busy: boolean;
}) {
  const t = useT();
  return (
    // role="region" (not "alert"): an assertive live region wrapping
    // interactive option buttons is an anti-pattern — it can interrupt
    // screen-reader users mid-task and doesn't relate the buttons to their
    // heading the way a labelled region does (whole-branch review Minor-4).
    <div
      role="region"
      aria-label={t("canvas.clarificationLabel")}
      className="rounded-xl border-2 border-amber-300 bg-amber-50 px-4 py-3.5"
    >
      <div className="flex items-center gap-2">
        <span aria-hidden="true">⚠️</span>
        <p className="text-sm font-bold text-amber-900">{t("canvas.clarificationTitle")}</p>
      </div>
      {file.preamble && <p className="text-sm text-amber-800 mt-1.5 leading-relaxed">{file.preamble}</p>}
      {file.questions.map((q) => (
        <div key={q.number} className="mt-3">
          {q.category && <p className="text-xs font-medium text-amber-700">{q.category}</p>}
          <p className="text-sm text-amber-800 mt-1 leading-relaxed">{q.text}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {q.options.map((opt) => (
              <button
                key={opt.letter}
                type="button"
                disabled={busy}
                onClick={() => onChoose(`${opt.letter} — ${opt.text}`)}
                className="px-3 py-1.5 rounded-lg bg-white border border-amber-300 text-amber-900 text-xs font-medium hover:bg-amber-100 disabled:opacity-50"
              >
                {opt.letter}. {opt.text}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
