"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  closeSurvey, createSurvey, getSurvey, surveyCsvUrl, synthesizeSurvey,
  type SurveyView, type SynthesisResult,
} from "@/lib/api/surveys";
import { SurveyDashboard } from "./SurveyDashboard";
import { useT } from "@/lib/i18n/provider";

export function SurveyPanel({ projectId, slug, readOnly = false }: {
  projectId: string;
  slug: string;
  /** Viewer member — sees questions, link, rollup, and CSV, but no create, close, or synthesize. */
  readOnly?: boolean;
}) {
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

      {!loading && !view && readOnly && (
        <p className="text-sm text-slate-400">{t("survey.none")}</p>
      )}

      {!loading && !view && !readOnly && (
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
                  {!readOnly && (
                    <button type="button" onClick={() => void handleClose()} disabled={busy}
                            className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                      {t("survey.close")}
                    </button>
                  )}
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
                {!readOnly && (
                  <button type="button" onClick={() => void handleCreate()} disabled={busy}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                    {t("survey.createNew")}
                  </button>
                )}
              </div>
            )}

            {/* Synthesis is possible even on an open survey: checking the interim rollup as a document
                and then collecting more responses is a common flow in practice. Rerunning overwrites only
                the rollup file with the latest figures — the agent's Step 6 synthesis is a different file
                (validation-results.md), so it is not touched. */}
            {!readOnly && (
              <div className="flex flex-wrap gap-2 items-center pt-1">
                <button type="button" onClick={() => void handleSynthesize()} disabled={busy}
                        className="px-3 py-1.5 rounded-lg bg-violet-600 text-white text-xs font-medium disabled:opacity-50">
                  {busy ? t("survey.synthesizing") : t("survey.synthesize")}
                </button>
              </div>
            )}
            {synthesized && <SynthesisNotice projectId={projectId} result={synthesized} />}
          </div>
          <SurveyDashboard questions={qn.questions} rollup={view!.rollup} />
        </>
      )}
    </section>
  );
}

/** A notice that synthesis is done, and a button that takes that rollup to the Discovery conversation.
 *
 *  Same approach as "수정 요청" (request changes) on the review screen: it does not send right away but only fills
 *  a draft into the workspace input via ?draft=. In Step 6 the agent reads, along with the rollup,
 *  whatever the user reported separately (discovery-config/CLAUDE.md), so this leaves room to add things
 *  like interview notes. The draft includes the rollup path because each prototype has its own
 *  rollup file — without the path, the agent asks back which prototype's survey it is, or
 *  reads the wrong one.
 *
 *  The sentence is translated as one chunk with {n} and {path} slots. Keeping the prefix and suffix separate
 *  and slotting them in English word order turns the Korean into "건을 5 에 저장했습니다. …". */
function SynthesisNotice({ projectId, result }: { projectId: string; result: SynthesisResult }) {
  const t = useT();
  const [before, after] = t("survey.saved")
    .replace("{n}", String(result.response_count))
    .split("{path}");
  const draft = t("survey.reflectDraft").replace("{path}", result.path);
  const href = `/projects/${projectId}/workspace?draft=${encodeURIComponent(draft)}`;
  return (
    <div className="rounded-lg bg-violet-50 p-3 space-y-2">
      <p className="text-xs text-slate-600 break-all">
        {before}<code>{result.path}</code>{after}
      </p>
      <p className="text-xs text-slate-500">{t("survey.savedNext")}</p>
      <Link href={href}
            className="inline-block px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-xs font-medium">
        {t("survey.reflect")}
      </Link>
    </div>
  );
}
