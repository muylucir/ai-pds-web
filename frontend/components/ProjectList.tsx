"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { canWrite, type ProjectPage, type ProjectProgress, type ProjectSummary } from "@/lib/api/types";
import { ApiError, deleteProject } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { exportProject } from "@/lib/api/transfer";
import { listModels } from "@/lib/api/models";
import { isLocale, LANGUAGE_LABEL } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";
import { modelLabel } from "@/lib/modelLabel";
import { ProjectMembersModal } from "./ProjectMembersModal";

function progressLabel(p: ProjectProgress | null | undefined): string {
  if (!p) return "—";
  const count = `(${p.completed}/${p.total})`;
  return p.current_stage ? `${p.current_stage} ${count}` : count;
}

/** The creation date shows **only the date** -- in the list, hours and minutes inform
 *  no decision and only widen the column. Using the first 10 characters of the ISO
 *  string is the same approach as admin/UserTable (parsing with Date can shift the
 *  date by a day depending on the local time zone). */
function createdLabel(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function ProjectList({
  data,
  onDeleted,
  onChanged,
  onPageChange,
}: {
  data: ProjectPage;
  onDeleted: () => void;
  /** Owner or members changed -- reload the list. */
  onChanged?: () => void;
  onPageChange: (page: number) => void;
}) {
  const t = useT();
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  // The project whose access management dialog is open. Only owners and admins get the button.
  const [accessTarget, setAccessTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The id of the project being exported. One value instead of per-row state -- bundle
  // assembly uses server CPU and temp disk, so there is no reason to start several at once.
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  // model_id → display name. Called once for the whole list (per row it would be up
  // to 50 calls per page). Failure just means no name, and the row shows the id --
  // the same judgment useProjectMeta makes for the header badge.
  const [modelNames, setModelNames] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    void listModels()
      .then((models) => {
        if (!alive) return;
        setModelNames(Object.fromEntries(models.map((m) => [m.model_id, m.name])));
      })
      .catch(() => { /* falls back to the raw id */ });
    return () => { alive = false; };
  }, []);

  async function runExport(p: ProjectSummary) {
    setExportError(null);
    setExporting(p.project_id);
    try {
      await exportProject(p.project_id);
    } catch (err) {
      // An in-progress build session (409) is the common failure -- the message must say so.
      // This screen has its own message: the shared one is written to be read next to the
      // prototype card, so its "카드의 세션 열기" (the card's Open session) does not say which
      // screen it is on. Here it names that tab first.
      setExportError(!(err instanceof ApiError)
        ? t("transfer.exportFailed")
        : err.detail === "build_session_active"
          ? t("transfer.buildSessionActive")
          : errorMessage(t, err.detail));
    } finally {
      setExporting(null);
    }
  }

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
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colOwner")}</th>
              <th scope="col" className="px-4 py-3 font-medium">{t("project.colCreatedAt")}</th>
              <th scope="col" className="px-4 py-3 w-36">
                <span className="sr-only">{t("project.delete")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.projects.map((p) => (
              // relative + an after:inset-0 stretched link on the name link -- clicking
              // anywhere in the row goes to the dashboard, while the delete button floats above with z-10.
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
                {/* A model not in the catalog shows the raw id -- a project running on a
                    model the admin removed is a normal path, and the screen must be honest
                    about it. effort is the value the project copied (not the catalog's current value). */}
                <td className="px-4 py-3 text-slate-600">
                  {p.model_id ? modelLabel(modelNames[p.model_id] ?? p.model_id, p.effort, t) : "—"}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {isLocale(p.language) ? LANGUAGE_LABEL[p.language] : "—"}
                </td>
                <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                  <span className="font-mono text-xs">{p.owner ?? "—"}</span>
                  {/* A project the user belongs to as a view-only member. Clicking opens it, but it cannot be changed. */}
                  {p.access === "member" && (
                    <span className="ml-2 text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                      {t("project.viewerBadge")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-500 whitespace-nowrap">
                  {createdLabel(p.created_at)}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  {canWrite(p.access) && (<>
                  <button
                    type="button"
                    aria-label={`${p.name ?? p.project_id} ${t("members.openAria")}`}
                    title={t("members.open")}
                    onClick={() => setAccessTarget(p)}
                    className="relative z-10 w-8 h-8 rounded-lg text-slate-300 hover:text-violet-700 hover:bg-violet-50 inline-flex items-center justify-center"
                  >
                    👥
                  </button>
                  <button
                    type="button"
                    aria-label={`${p.name ?? p.project_id} ${t("transfer.exportAria")}`}
                    title={t("transfer.export")}
                    disabled={exporting !== null}
                    onClick={() => void runExport(p)}
                    className="relative z-10 w-8 h-8 rounded-lg text-slate-300 hover:text-violet-700 hover:bg-violet-50 inline-flex items-center justify-center disabled:opacity-40 disabled:pointer-events-none"
                  >
                    {exporting === p.project_id ? "⏳" : "⬇"}
                  </button>
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
                  </>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {exportError && <p className="text-sm text-rose-600 mt-3">{exportError}</p>}
      {exporting !== null && !exportError && (
        <p className="text-sm text-slate-500 mt-3">{t("transfer.exporting")}</p>
      )}

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

      {accessTarget && (
        <ProjectMembersModal
          project={accessTarget}
          onClose={() => setAccessTarget(null)}
          onChanged={() => onChanged?.()}
        />
      )}

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
