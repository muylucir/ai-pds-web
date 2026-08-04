"use client";
import type { AuditEntry } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

const DOT = ["bg-violet-500", "bg-emerald-500", "bg-sky-500", "bg-rose-400", "bg-amber-500"];

export function ActivityFeed({ entries, limit = 6 }: { entries: AuditEntry[]; limit?: number }) {
  const t = useT();
  const recent = [...entries].sort((a, b) => b.index - a.index).slice(0, limit);
  return (
    <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="audit-heading">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <h2 id="audit-heading" className="font-bold">
          {t("dash.recentActivity")} <span className="text-xs font-normal text-slate-400">(audit.md)</span>
        </h2>
      </div>
      {recent.length === 0 ? (
        <p className="p-5 text-sm text-slate-400">{t("dash.noActivity")}</p>
      ) : (
        <ul className="p-5 space-y-4 text-sm">
          {recent.map((e, i) => {
            const summary = e.ai_response && e.ai_response !== "N/A" ? e.ai_response : e.user_input;
            return (
              <li key={e.index} className="flex gap-3">
                <span className={`shrink-0 w-2 h-2 mt-1.5 rounded-full ${DOT[i % DOT.length]}`} aria-hidden="true" />
                <div className="min-w-0">
                  <p className="line-clamp-2">{summary}</p>
                  <p className="text-xs text-slate-400">Entry {e.index}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
