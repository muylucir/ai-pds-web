"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ProjectPage, ProjectProgress, ProjectSummary } from "@/lib/api/types";
import { deleteProject } from "@/lib/api/client";
import { listModels } from "@/lib/api/models";
import { isLocale, LANGUAGE_LABEL } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

function progressLabel(p: ProjectProgress | null | undefined): string {
  if (!p) return "—";
  const count = `(${p.completed}/${p.total})`;
  return p.current_stage ? `${p.current_stage} ${count}` : count;
}

/** 생성일은 **날짜까지만** 보여준다 — 목록에서 시·분은 판단에 쓰이지 않고
 *  열만 넓힌다. ISO 문자열의 앞 10자를 쓰는 것은 admin/UserTable과 같은
 *  방식이다(Date로 파싱하면 로컬 타임존에 따라 날짜가 하루 밀린다). */
function createdLabel(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function ProjectList({
  data,
  onDeleted,
  onPageChange,
}: {
  data: ProjectPage;
  onDeleted: () => void;
  onPageChange: (page: number) => void;
}) {
  const t = useT();
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // model_id → 표시 이름. 목록 전체에 한 번만 부른다(행마다 부르면 페이지당
  // 최대 50회다). 실패는 이름을 못 붙이는 것으로 끝나고 행은 id를 보여준다 —
  // useProjectMeta가 헤더 배지에서 하는 것과 같은 판단이다.
  const [modelNames, setModelNames] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    void listModels()
      .then((models) => {
        if (!alive) return;
        setModelNames(Object.fromEntries(models.map((m) => [m.model_id, m.name])));
      })
      .catch(() => { /* id 원문으로 떨어진다 */ });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!target) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) setTarget(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [target, busy]);

  async function confirmDelete() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await deleteProject(target.project_id);
      setTarget(null);
      onDeleted();
    } catch {
      setError(t("project.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  if (data.total === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        {t("project.emptyList")}
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.size));

  return (
    <>
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
              <th scope="col" className="px-4 py-3 font-medium">{t("project.id")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colName")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colProgress")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colModel")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colLanguage")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colCreatedAt")}</th>
              <th scope="col" className="px-4 py-3 w-12">
                <span className="sr-only">{t("project.delete")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.projects.map((p) => (
              // relative + 이름 링크의 after:inset-0 스트레치드 링크 — 행 어디를
              // 클릭해도 대시보드로 이동하되, 삭제 버튼은 z-10으로 위에 띄운다.
              <tr key={p.project_id} className="relative border-b border-slate-100 last:border-0 hover:bg-violet-50/40">
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{p.project_id}</td>
                <td className="px-4 py-3 font-medium">
                  <Link
                    href={`/projects/${p.project_id}/dashboard`}
                    className="text-slate-900 hover:text-violet-700 after:absolute after:inset-0 after:content-['']"
                  >
                    {p.name ?? p.project_id}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-600">{progressLabel(p.progress)}</td>
                {/* 카탈로그에 없는 모델은 id 원문 — 관리자가 지운 모델로 도는
                    프로젝트는 정상 경로이고, 그 사실이 화면에서 정직해야 한다. */}
                <td className="px-4 py-3 text-slate-600">
                  {p.model_id ? modelNames[p.model_id] ?? p.model_id : "—"}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {isLocale(p.language) ? LANGUAGE_LABEL[p.language] : "—"}
                </td>
                <td className="px-4 py-3 text-slate-500 whitespace-nowrap">
                  {createdLabel(p.created_at)}
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    type="button"
                    aria-label={`${p.name ?? p.project_id} ${t("project.deleteAria")}`}
                    onClick={() => {
                      setError(null);
                      setTarget(p);
                    }}
                    className="relative z-10 w-8 h-8 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 inline-flex items-center justify-center"
                  >
                    🗑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-4 text-sm text-slate-500">
        <span>{t("project.totalCount").replace("{n}", String(data.total))}</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label={t("project.prevPageAria")}
            disabled={data.page <= 1}
            onClick={() => onPageChange(data.page - 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            {t("project.prevPage")}
          </button>
          <span>{data.page} / {totalPages}</span>
          <button
            type="button"
            aria-label={t("project.nextPageAria")}
            disabled={data.page >= totalPages}
            onClick={() => onPageChange(data.page + 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            {t("project.nextPage")}
          </button>
        </div>
      </div>

      {target && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => !busy && setTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("project.deleteConfirmLabel")}
            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">
              {t("project.deleteConfirmTitle").replace("{name}", target.name ?? target.project_id)}
            </h2>
            <p className="text-sm text-slate-600 mt-2">
              {t("project.deleteConfirmBody")}
            </p>
            {error && <p className="text-sm text-rose-600 mt-3">{error}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setTarget(null)}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                {t("project.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50"
              >
                {t("project.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
