"use client";
// frontend/components/workspace/WelcomeCard.tsx
//
// 두 언어 채널이 한 컴포넌트에서 만나는 자리다:
//   - 카드의 라벨·설명 = UI 언어(useT)
//   - onStart로 보내는 개시 문장 = 프로젝트 언어(startMessage)
// 후자는 에이전트에게 가고 트랜스크립트에 남으므로 UI 언어를 따르면 안 된다 —
// 영어 UI로 한국어 프로젝트를 시작하면 대화는 한국어로 진행되어야 한다
// (lib/approvalMarker.ts의 승인 단어와 같은 판단).
import { DEFAULT_LOCALE, type Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { startMessage } from "@/lib/startMessage";

export function WelcomeCard({
  onStart,
  language,
}: {
  onStart: (text: string) => void;
  // 이 프로젝트의 생성물 언어. null/undefined면 ko로 떨어진다 — 구 백엔드나
  // 조회 실패이고, 그것이 이 기능 이전 모든 프로젝트의 언어다.
  language?: Locale | null;
}) {
  const t = useT();
  const lang = language ?? DEFAULT_LOCALE;
  return (
    <div className="max-w-xl mx-auto mt-12 rounded-2xl border border-slate-200 bg-white p-6 text-center space-y-4">
      <p className="text-lg font-bold">{t("welcome.title")}</p>
      <p className="text-sm text-slate-500">{t("welcome.subtitle")}</p>
      <div className="grid gap-3 sm:grid-cols-2 text-left">
        <button type="button" onClick={() => onStart(startMessage("A", lang))}
          className="rounded-xl border border-violet-200 bg-violet-50 hover:bg-violet-100 p-4">
          <p className="font-bold text-violet-700 text-sm">{t("welcome.pathATitle")}</p>
          <p className="mt-1 text-xs text-slate-600">{t("welcome.pathABody")}</p>
        </button>
        <button type="button" onClick={() => onStart(startMessage("B", lang))}
          className="rounded-xl border border-sky-200 bg-sky-50 hover:bg-sky-100 p-4">
          <p className="font-bold text-sky-700 text-sm">{t("welcome.pathBTitle")}</p>
          <p className="mt-1 text-xs text-slate-600">{t("welcome.pathBBody")}</p>
        </button>
      </div>
      <p className="text-xs text-slate-400">{t("welcome.freeform")}</p>
    </div>
  );
}
