"use client";
// frontend/components/workspace/WelcomeCard.tsx
//
// This is where the two language channels meet in one component:
//   - the card's labels and descriptions = UI language (useT)
//   - the opening sentence sent via onStart = project language (startMessage)
// The latter goes to the agent and stays in the transcript, so it must not follow the UI language —
// starting a Korean project from an English UI must still run the conversation in Korean
// (same judgment as the approval words in lib/approvalMarker.ts).
import { DEFAULT_LOCALE, type Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { startMessage } from "@/lib/startMessage";

export function WelcomeCard({
  onStart,
  language,
}: {
  onStart: (text: string) => void;
  // This project's output language. null/undefined falls back to ko — that means an old backend or
  // a failed lookup, and ko is the language of every project predating this feature.
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
