"use client";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ModelTable } from "@/components/admin/ModelTable";
import { AddModelModal } from "@/components/admin/AddModelModal";
import { ApiError } from "@/lib/api/client";
import { listAdminModels, type AdminModel } from "@/lib/api/models";
import { useT } from "@/lib/i18n/provider";

export default function AdminModelsPage() {
  const [models, setModels] = useState<AdminModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const t = useT();
  const reload = useCallback(async () => {
    setError(null);
    try {
      setModels(await listAdminModels());
    } catch (err) {
      // A 403 is the case where a pm typed the URL directly — the middleware is only a UX gate, and
      // the actual block shows up here (in the backend response).
      setError(err instanceof ApiError && err.status === 403
        ? t("admin.needAdmin")
        : t("admin.modelsLoadFailed"));
      setModels([]);
    }
  }, [t]);

  useEffect(() => { void reload(); }, [reload]);

  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold">{t("admin.modelsTitle")}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {t("admin.modelsSubtitle")}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700"
          >
            {t("admin.addModelTitle")}
          </button>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </p>
        )}
        {models === null && <p className="text-sm text-slate-400">{t("admin.commonLoading")}</p>}
        {models !== null && models.length > 0 && (
          <ModelTable models={models} onChanged={reload} />
        )}
        {models !== null && models.length === 0 && !error && (
          <p className="text-sm text-slate-500">{t("admin.noModels")}</p>
        )}

        {adding && (
          <AddModelModal onAdded={reload} onClose={() => setAdding(false)} />
        )}
      </main>
    </>
  );
}
