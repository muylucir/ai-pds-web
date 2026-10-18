"use client";
// frontend/components/canvas/HistoryQuestionsCard.tsx — **read-only** display of a questionnaire
// presented in a past turn.
//
// Previously this spot held only the single line "📋 질문지 제시됨" (questionnaire presented), because the restore path
// discarded the transcript's tool_use.input (where the question payload remains structured) —
// so in the scrollback you could not tell "what was asked", and the answer bubble alone had
// no context. When the payload is present, show the question count and let the user expand it to read the questions and
// options.
//
// This is **not** a live form: you cannot answer again here. That
// round is already over, and the answers are in the bubble right below.
import { memo, useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";
import { InlineMarkdown } from "@/components/Markdown";

// `memo`: a restored card never changes — so when the timeline redraws during streaming, InlineMarkdown
// is not re-parsed for every option.
export const HistoryQuestionsCard = memo(function HistoryQuestionsCard({
  name,
  file,
}: {
  name: string | null;
  file?: QuestionFile | null;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const questions = file?.questions ?? [];

  return (
    <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-xs text-violet-700">
      <div className="flex items-center justify-between gap-3">
        <p>
          📋 {t("chat.questionsPresented")}
          {name ? ` — ${name}` : ""}
          {questions.length > 0 ? ` · ${questions.length}${t("chat.questionCountSuffix")}` : ""}
        </p>
        {questions.length > 0 && (
          <button
            type="button"
            aria-expanded={expanded}
            className="text-[11px] text-violet-400 hover:text-violet-700 shrink-0"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? t("canvas.collapse") : t("canvas.expand")}
          </button>
        )}
      </div>
      {expanded && (
        <ul className="mt-2 space-y-2 border-t border-violet-200 pt-2">
          {questions.map((q) => (
            <li key={q.number}>
              <p className="font-medium text-slate-700">
                Q{q.number}. <InlineMarkdown text={q.text} />
              </p>
              {/* is_other is a free-input placeholder — it is not an option that was actually
                  presented, so drop it from the list (the same judgment as answerSummary's
                  letterText). */}
              <ul className="mt-0.5 text-slate-500">
                {q.options
                  .filter((o) => !o.is_other)
                  .map((o) => (
                    <li key={o.letter}>
                      {o.letter}. <InlineMarkdown text={o.text} />
                    </li>
                  ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
