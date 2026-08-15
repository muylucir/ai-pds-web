"use client";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { DesignProfileCard } from "@/components/admin/DesignProfileCard";
import { UploadDesignModal } from "@/components/admin/UploadDesignModal";
import { ApiError } from "@/lib/api/client";
import {
  DESIGN_TEMPLATE_PATH, deleteDesignProfile, getDesignProfile,
  type DesignProfile,
} from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";

export default function AdminDesignPage() {
  const [profile, setProfile] = useState<DesignProfile | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const t = useT();
  const reload = useCallback(async () => {
    setError(null);
    try {
      setProfile(await getDesignProfile());
    } catch (err) {
      // 403은 pm이 URL을 직접 친 경우다 — 미들웨어는 UX 게이트일 뿐이고 실제
      // 차단은 백엔드 응답에서 드러난다(admin/models 페이지와 같은 규율).
      setError(err instanceof ApiError && err.status === 403
        ? t("admin.needAdmin") : t("admin.designLoadFailed"));
      setProfile(null);
    }
  }, [t]);

  useEffect(() => { void reload(); }, [reload]);

  async function remove() {
    if (!window.confirm(t("admin.designRemoveWarning"))) return;
    try {
      await deleteDesignProfile();
      await reload();
    } catch {
      // designLoadFailed("불러오지 못했습니다")가 아니라 이 키를 쓴다 -- 실패한
      // 것은 로딩이 아니라 삭제이고, 잘못된 메시지는 다음에 뭘 시도해야 할지
      // 헷갈리게 한다.
      setError(t("admin.designDeleteFailed"));
    }
  }

  return (
    <>
      <AppHeader activeTab="projects" />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">{t("admin.designTitle")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("admin.designSubtitle")}</p>
        </div>

        {error && (
          <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </p>
        )}

        {profile === undefined && (
          <p className="text-sm text-slate-400">{t("admin.commonLoading")}</p>
        )}

        {profile === null && !error && (
          <section className="rounded-xl border border-dashed border-slate-300 p-8 text-center">
            <p className="text-sm text-slate-600">{t("admin.designNone")}</p>
            <div className="mt-4 flex justify-center gap-2">
              <a href={DESIGN_TEMPLATE_PATH}
                 className="rounded-lg border border-slate-200 px-4 py-2 text-sm hover:bg-slate-50">
                {t("admin.designDownloadTemplate")}
              </a>
              <button type="button" onClick={() => setUploading(true)}
                      className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700">
                {t("admin.designUpload")}
              </button>
            </div>
          </section>
        )}

        {profile && (
          <DesignProfileCard profile={profile}
                             onReplace={() => setUploading(true)}
                             onRemove={remove} />
        )}

        {uploading && (
          <UploadDesignModal replacing={Boolean(profile)}
                             onUploaded={reload}
                             onClose={() => setUploading(false)} />
        )}
      </main>
    </>
  );
}
