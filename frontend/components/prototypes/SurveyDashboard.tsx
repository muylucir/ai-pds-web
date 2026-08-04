"use client";
import type { Rollup, SurveyQuestion } from "@/lib/api/surveys";
import { useT } from "@/lib/i18n/provider";

function ScaleBar({ label, n, max }: { label: string; n: number; max: number }) {
  const pct = max > 0 ? Math.round((n / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-4 text-slate-400">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className="h-full bg-violet-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right text-slate-500">{n}</span>
    </div>
  );
}

export function SurveyDashboard({ questions, rollup }: {
  questions: SurveyQuestion[];
  rollup: Rollup;
}) {
  const t = useT();
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">
        {t("survey.responseCount").replace("{n}", String(rollup.count))}
      </p>
      {rollup.count === 0 && (
        <p className="text-sm text-slate-400">
          {t("survey.noResponsesYet")}
        </p>
      )}
      {questions.map((q) => {
        const stat = rollup.per_question[q.id];
        if (!stat) return null;
        return (
          <div key={q.id} className="rounded-xl border border-slate-200 p-4">
            <p className="text-sm font-medium text-slate-700 mb-3">{q.text}</p>
            {stat.type === "scale" && (
              <div className="space-y-1.5">
                <p className="text-xs text-slate-500 mb-2">
                  {t("survey.mean")}{" "}
                  <span className="font-bold text-violet-600">{stat.mean}</span>{" "}
                  {t("survey.scaleSummary").replace("{n}", String(stat.n))}
                </p>
                {["5", "4", "3", "2", "1"].map((k) => (
                  <ScaleBar key={k} label={k} n={stat.distribution[k] ?? 0}
                            max={Math.max(...Object.values(stat.distribution), 1)} />
                ))}
              </div>
            )}
            {stat.type === "choice" && (
              <ul className="space-y-1.5">
                {Object.entries(stat.counts).map(([opt, n]) => (
                  <li key={opt} className="flex items-center gap-2 text-xs">
                    <span className="flex-1 text-slate-600">{opt}</span>
                    <span className="text-slate-500">{t("survey.choiceCount").replace("{n}", String(n))}</span>
                  </li>
                ))}
              </ul>
            )}
            {stat.type === "text" && (
              <div>
                <p className="text-xs text-slate-500 mb-2">{t("survey.freeTextResponses").replace("{n}", String(stat.n))}</p>
                <ul className="space-y-2">
                  {stat.samples.map((s, i) => (
                    <li key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      {s}
                    </li>
                  ))}
                </ul>
                {stat.n > stat.samples.length && (
                  <p className="text-xs text-slate-400 mt-2">
                    {t("survey.exportForAll")}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
