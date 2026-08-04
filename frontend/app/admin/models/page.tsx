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
      // 403은 pm이 URL을 직접 친 경우다 — 미들웨어는 UX 게이트일 뿐이고
      // 실제 차단은 여기(백엔드 응답)에서 드러난다.
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
              프로젝트 생성 화면의 모델 목록입니다. 여러 모델을 등록해 두고 그중
              최대 5개만 표시할 수 있습니다. 이미 만든 프로젝트는 여기서 모델을
              지워도 계속 같은 모델로 돕니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700"
          >
            모델 추가
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
