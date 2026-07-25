"use client";
import { useCallback, useEffect, useState } from "react";
import {
  closeSurvey, createSurvey, getSurvey, surveyCsvUrl,
  type SurveyView,
} from "@/lib/api/surveys";
import { SurveyDashboard } from "./SurveyDashboard";

export function SurveyPanel({ projectId, slug }: { projectId: string; slug: string }) {
  const [view, setView] = useState<SurveyView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setView(await getSurvey(projectId, slug));
    } catch {
      setError("설문 정보를 불러오지 못했습니다.");
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
      setError("질문 생성에 실패했습니다. 다시 시도해 주세요.");
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
      setError("설문 마감에 실패했습니다.");
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
          검증 설문
        </h2>
        {view && (
          <button type="button" onClick={() => void reload()} disabled={busy}
                  className="text-xs text-slate-500 hover:text-slate-700">
            새로고침
          </button>
        )}
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}
      {loading && !view && <p className="text-sm text-slate-400">불러오는 중…</p>}

      {!loading && !view && (
        <div className="rounded-xl border border-slate-200 p-4">
          <p className="text-sm text-slate-600 mb-3">
            프로토타입 명세의 검증 가설에서 설문 문항을 생성합니다.
          </p>
          <button type="button" onClick={() => void handleCreate()} disabled={busy}
                  className="px-3.5 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50">
            {busy ? "생성 중…" : "질문 생성"}
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
                    {copied ? "복사됨" : "링크 복사"}
                  </button>
                  <button type="button" onClick={() => void handleClose()} disabled={busy}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                    설문 마감
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-wrap gap-2 items-center">
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">
                  마감됨
                </span>
                <a href={surveyCsvUrl(projectId, slug)}
                   className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50">
                  CSV 내보내기
                </a>
                <button type="button" onClick={() => void handleCreate()} disabled={busy}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                  새 설문 생성
                </button>
              </div>
            )}
          </div>
          <SurveyDashboard questions={qn.questions} rollup={view!.rollup} />
        </>
      )}
    </section>
  );
}
