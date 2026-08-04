"use client";
import { useCallback, useEffect, useState } from "react";
import {
  closeSurvey, createSurvey, getSurvey, surveyCsvUrl, synthesizeSurvey,
  type SurveyView, type SynthesisResult,
} from "@/lib/api/surveys";
import { SurveyDashboard } from "./SurveyDashboard";
import { useT } from "@/lib/i18n/provider";

export function SurveyPanel({ projectId, slug }: { projectId: string; slug: string }) {
  const t = useT();
  const [view, setView] = useState<SurveyView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [synthesized, setSynthesized] = useState<SynthesisResult | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setView(await getSurvey(projectId, slug));
    } catch {
      setError(t("survey.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, slug]);

  useEffect(() => { void reload(); }, [reload]);

  async function handleCreate() {
    setBusy(true);
    setError(null);
    try {
      await createSurvey(projectId, slug);
      await reload();
    } catch {
      setError(t("survey.generateFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleSynthesize() {
    setBusy(true);
    setError(null);
    try {
      setSynthesized(await synthesizeSurvey(projectId, slug));
    } catch {
      setError(t("survey.synthesizeFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleClose() {
    setBusy(true);
    try {
      await closeSurvey(projectId, slug);
      await reload();
    } catch {
      setError(t("survey.closeFailed"));
    } finally {
      setBusy(false);
    }
  }

  const qn = view?.questionnaire;
  const publicUrl = view ? `${window.location.origin}${view.url}` : "";

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-bold text-slate-400 uppercase tracking-wide">
          {t("survey.title")}
        </h2>
        {view && (
          <button type="button" onClick={() => void reload()} disabled={busy}
                  className="text-xs text-slate-500 hover:text-slate-700">
            {t("survey.refresh")}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}
      {loading && !view && <p className="text-sm text-slate-400">{t("survey.loading")}</p>}

      {!loading && !view && (
        <div className="rounded-xl border border-slate-200 p-4">
          <p className="text-sm text-slate-600 mb-3">
            {t("survey.generateHint")}
          </p>
          <button type="button" onClick={() => void handleCreate()} disabled={busy}
                  className="px-3.5 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50">
            {busy ? t("survey.generating") : t("survey.generate")}
          </button>
        </div>
      )}

      {qn && (
        <>
          <div className="rounded-xl border border-slate-200 p-4 space-y-3">
            <p className="text-sm font-medium text-slate-700">{qn.title}</p>
            {qn.status === "open" ? (
              <>
                <p className="text-xs text-slate-500 break-all">{view!.url}</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button"
                          onClick={() => {
                            void navigator.clipboard?.writeText(publicUrl);
                            setCopied(true);
                          }}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50">
                    {copied ? t("survey.copied") : t("survey.copyLink")}
                  </button>
                  <button type="button" onClick={() => void handleClose()} disabled={busy}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                    {t("survey.close")}
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-wrap gap-2 items-center">
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">
                  {t("survey.closed")}
                </span>
                <a href={surveyCsvUrl(projectId, slug)}
                   className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50">
                  {t("survey.exportCsv")}
                </a>
                <button type="button" onClick={() => void handleCreate()} disabled={busy}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                  {t("survey.createNew")}
                </button>
              </div>
            )}

            {/* 취합은 열린 설문에서도 가능하다: 중간 집계를 문서로 확인한 뒤
                응답을 더 받는 흐름이 실제로 흔하다. 재실행하면 최신 수치로
                덮어쓴다. */}
            <div className="flex flex-wrap gap-2 items-center pt-1">
              <button type="button" onClick={() => void handleSynthesize()} disabled={busy}
                      className="px-3 py-1.5 rounded-lg bg-violet-600 text-white text-xs font-medium disabled:opacity-50">
                {busy ? t("survey.synthesizing") : t("survey.synthesize")}
              </button>
              {synthesized && (
                <span className="text-xs text-slate-500 break-all">
                  {t("survey.savedPrefix")} {synthesized.response_count} {t("survey.savedSuffix")} <code>{synthesized.path}</code>
                </span>
              )}
            </div>
          </div>
          <SurveyDashboard questions={qn.questions} rollup={view!.rollup} />
        </>
      )}
    </section>
  );
}
