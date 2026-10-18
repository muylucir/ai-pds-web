// Handoff tab reset confirmation. "Regenerate" carries over the previous version's open decisions and supplementary answers — this
// erases those two as well. Written with the same weight as the prototype reset confirmation: what remains goes right under the
// title, what cannot be undone goes at the very end (so the last sentence read does not soften the warning).
"use client";
import { useEffect } from "react";
import type { SupplementView } from "@/lib/api/handoff";
import { useT } from "@/lib/i18n/provider";

/** The count of what the PM left in the handoff tab that a reset will erase — supplementary answers (including superseded ones) and AI suggestion confirmations. */
export function savedAnswers(view: SupplementView | null | undefined): number {
  if (!view) return 0;
  const open = [...view.open_questions, ...view.open_deferred, ...view.open_answered];
  return [...view.questions, ...view.superseded].filter((q) => q.answer).length
    + open.filter((q) => q.answer).length
    + view.confirmations.filter((c) => c.confirmed_at).length;
}

export function ResetDialog({
  answers, busy, error, onCancel, onConfirm,
}: {
  /** The number of supplementary answers and AI suggestion confirmations to be erased. At 0, no cannot-be-undone warning is shown. */
  answers: number;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-6"
         onClick={() => !busy && onCancel()}>
      <div role="dialog" aria-modal="true" aria-label={t("handoff.reset.label")}
           className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <h2 className="font-bold text-lg">{t("handoff.reset.title")}</h2>
        <p className="text-xs text-slate-400 mt-1">{t("handoff.reset.keeps")}</p>
        <p className="text-sm text-slate-600 mt-3">{t("handoff.reset.deletesIntro")}</p>
        <ul className="text-sm text-slate-600 mt-1 list-disc list-inside space-y-0.5">
          <li>{t("handoff.reset.itemPackage")}</li>
          <li>{t("handoff.reset.itemPrevious")}</li>
          <li>{t("handoff.reset.itemAnswers")}</li>
        </ul>
        {answers > 0 && (
          <p className="text-sm font-semibold text-rose-600 mt-3">
            {t("handoff.reset.answersWarn").replace("{n}", String(answers))}
          </p>
        )}
        {error && <p className="text-sm text-rose-600 mt-3">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={busy}
                  className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50">
            {t("page.cancel")}
          </button>
          <button type="button" onClick={onConfirm} disabled={busy}
                  className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50">
            {t("handoff.reset.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
