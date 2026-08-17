"use client";
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { answeredCount } from "@/lib/stageProgress";
import { useT } from "@/lib/i18n/provider";
import { InlineMarkdown } from "@/components/Markdown";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

// Green collapsed "제출됨" summary (mockup 04's submitted-question-set idiom).
// Rendered when the caller (QuestionCardSlot, Task 3) has determined every
// question in `file` is answered — this component just renders the data.
export function QuestionSummaryCard({ file }: { file: QuestionFile }) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const { answered } = answeredCount(file);

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-4 py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="text-emerald-600" aria-hidden="true">
            ✓
          </span>
          <div>
            <p className="font-medium">
              {basename(file.name)} · {answered}{t("chat.answeredSuffix")}
            </p>
            <p className="text-[11px] text-slate-400">
              {t("chat.submittedNote")}
            </p>
          </div>
        </div>
        <button
          type="button"
          aria-expanded={expanded}
          className="text-[11px] text-slate-400 hover:text-violet-600 shrink-0"
          onClick={() => setExpanded((v) => !v)}
        >
          {/* Mockup 04 only shows this widget collapsed ("펼치기"); the 접기
              label for the expanded state is our own a11y extension, not
              ported copy — it keeps the toggle's accessible name in sync
              with aria-expanded instead of leaving a stale "펼치기" label. */}
          {expanded ? t("canvas.collapse") : t("canvas.expand")}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
        {file.questions.map((q) => (
          <span
            key={q.number}
            className="px-2 py-0.5 rounded bg-white border border-emerald-200 text-slate-500"
          >
            Q{q.number}:{q.answer ?? ""}
          </span>
        ))}
      </div>
      {expanded && (
        <ul className="mt-3 space-y-2 border-t border-emerald-200 pt-3">
          {file.questions.map((q) => (
            <li key={q.number} className="text-xs text-slate-600">
              <p className="font-medium">
                Q{q.number}. <InlineMarkdown text={q.text} />
              </p>
              <p className="text-slate-400 mt-0.5">{t("canvas.answerLabel")}: {q.answer ?? "-"}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
