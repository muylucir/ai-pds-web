// Per-stage progress of package generation — shown while generating, or when it failed or was interrupted.
//
// Each stage shows waiting / in progress (waiting for first response, thinking, characters received) / done / failed. Large
// projects take more than 2 minutes to the first output (observed ws-g) — during that time "waiting for the first
// response" and the elapsed time must be visible so it can be told apart from a hang.
//
// On failure there are two paths: keep the finished stages and resume from the failed one (the server reports this as `resumable` —
// it is false if the source or the supplementary answers changed in the meantime), or start over.
"use client";
import { useEffect, useState } from "react";
import type { Manifest, PackageView, Step } from "@/lib/api/handoff";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

function seconds(from: string | null, to: string | null, now: number): number | null {
  if (!from) return null;
  const start = Date.parse(from);
  const end = to ? Date.parse(to) : now;
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function GenerationProgress({ pkg, busy, onResume, onRestart, readOnly = false }: {
  pkg: PackageView;
  busy: boolean;
  onResume: () => void;
  onRestart: () => void;
  /** Read-only member — sees progress but has no resume or restart. */
  readOnly?: boolean;
}) {
  const t = useT();
  const m = pkg.manifest as Manifest;
  const generating = m.status === "generating";
  const [now, setNow] = useState(() => Date.now());

  // Redraw every second only while generating — with polling (3 seconds) alone, the elapsed time jumps in steps.
  useEffect(() => {
    if (!generating) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [generating]);

  const duration = (s: number | null) => {
    if (s === null) return "";
    const min = Math.floor(s / 60);
    return min > 0
      ? t("handoff.progress.minSec").replace("{m}", String(min)).replace("{s}", String(s % 60))
      : t("handoff.progress.sec").replace("{s}", String(s));
  };
  const reason = (code: string | null) =>
    t(`handoff.pkg.error.${code ?? "generation_failed"}` as keyof Dict) ?? t("handoff.pkg.error.generation_failed");

  const detail = (step: Step): string => {
    switch (step.status) {
      case "pending":
        return t("handoff.progress.pending");
      case "running": {
        const what = step.chars > 0
          ? t("handoff.progress.receiving").replace("{n}", step.chars.toLocaleString())
          : step.thinking ? t("handoff.progress.thinking") : t("handoff.progress.waiting");
        return `${what} · ${duration(seconds(step.started_at, null, now))}`;
      }
      case "done":
        return `${t("handoff.progress.done").replace("{n}", step.chars.toLocaleString())} · ${
          duration(seconds(step.started_at, step.finished_at, now))}`;
      case "failed":
        return t("handoff.progress.failed").replace("{reason}", reason(step.error));
    }
    return "";
  };

  const tone = (step: Step) => ({
    pending: { mark: "", cls: "border border-slate-300 bg-white" },
    running: { mark: "", cls: "border-2 border-violet-500 border-t-transparent animate-spin" },
    done: { mark: "✓", cls: "bg-emerald-500 text-white" },
    failed: { mark: "✕", cls: "bg-rose-600 text-white" },
  })[step.status];

  const failedTitle = m.status === "interrupted"
    ? t("handoff.pkg.interrupted")
    : m.status === "failed" ? t("handoff.pkg.failed").replace("{reason}", reason(m.error)) : null;

  return (
    <section className="max-w-[920px] mb-5 bg-white border border-slate-200 rounded-xl">
      <h3 className="px-5 pt-3.5 pb-2 text-[15px] font-semibold text-slate-900">{t("handoff.progress.title")}</h3>
      {failedTitle && (
        <p role="alert" className="mx-5 mb-2 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] text-rose-900">
          {failedTitle}
        </p>
      )}
      <ol>
        {m.steps.map((step) => (
          <li key={step.name} className="flex items-center gap-3 px-5 py-2.5 border-t border-slate-100 text-sm">
            <span aria-hidden className={`inline-flex w-5 h-5 rounded-full items-center justify-center text-[11px] font-bold ${tone(step).cls}`}>
              {tone(step).mark}
            </span>
            <span className="w-40 font-semibold text-slate-900">{t(`handoff.step.${step.name}` as keyof Dict)}</span>
            <span className={`text-[13px] ${step.status === "failed" ? "text-rose-700" : "text-slate-600"}`}>
              {detail(step)}
            </span>
          </li>
        ))}
      </ol>
      {!generating && !readOnly && (
        <div className="flex items-center gap-2.5 px-5 py-3 border-t border-slate-100">
          {pkg.resumable && (
            <button type="button" onClick={onResume} disabled={busy}
                    className="px-4 py-2 rounded-lg bg-violet-600 text-white text-[13px] font-bold hover:bg-violet-700 disabled:opacity-45">
              {t("handoff.progress.resume")}
            </button>
          )}
          <button type="button" onClick={onRestart} disabled={busy}
                  className={`px-3 py-2 rounded-lg text-[13px] disabled:opacity-45 ${pkg.resumable
                    ? "border border-slate-300 text-slate-600"
                    : "bg-violet-600 text-white font-bold hover:bg-violet-700"}`}>
            {t("handoff.progress.restart")}
          </button>
          {!pkg.resumable && m.steps.some((s) => s.status === "done") && (
            <span className="text-xs text-slate-500">{t("handoff.progress.staleNote")}</span>
          )}
        </div>
      )}
    </section>
  );
}
