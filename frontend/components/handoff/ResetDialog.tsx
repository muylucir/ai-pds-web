// 인계 탭 초기화 확인. "다시 생성"은 직전 판의 열린 결정과 보완 답을 이어받는다 — 이것은 그 둘까지
// 지운다. 프로토타입 초기화 확인과 같은 무게로 쓴다: 남는 것을 제목 바로 아래, 되돌릴 수 없는 것을
// 맨 끝에 둔다(마지막에 읽힌 문장이 경고를 누그러뜨리지 않게).
"use client";
import { useEffect } from "react";
import type { SupplementView } from "@/lib/api/handoff";
import { useT } from "@/lib/i18n/provider";

/** 초기화로 사라질, PM이 인계 탭에서 남긴 것의 수 — 보완 답(대체된 것 포함)과 AI 제안 확인. */
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
  /** 지워질 보완 답과 AI 제안 확인의 수. 0이면 되돌릴 수 없다는 경고를 내지 않는다. */
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
