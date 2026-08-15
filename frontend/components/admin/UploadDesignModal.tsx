"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { DESIGN_TEMPLATE_PATH, uploadDesignProfile } from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";

export function UploadDesignModal(
  { onUploaded, onClose, replacing }:
  { onUploaded: () => void; onClose: () => void; replacing: boolean },
) {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDesignProfile(file);
      onUploaded();
      onClose();
    } catch (err) {
      // 400의 detail은 백엔드가 짚어준 줄 번호다 — 그대로 보여준다. 여기서
      // 다시 파싱하지 않는다(파서가 두 벌이 되면 어긋난다).
      setError(err instanceof ApiError && err.detail
        ? err.detail : t("admin.designUploadFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 grid place-items-center bg-slate-900/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white p-6">
        <h2 className="text-lg font-bold">{t("admin.designUpload")}</h2>
        <p className="mt-2 text-sm text-slate-500">{t("admin.designLanguageNote")}</p>
        {replacing && (
          <p className="mt-2 text-sm text-amber-700">
            {t("admin.designReplaceWarning")}
          </p>
        )}

        <label className="mt-4 block text-sm" htmlFor="design-file">
          DESIGN.md
        </label>
        <input id="design-file" type="file" accept=".md"
               className="mt-1 block w-full text-sm"
               onChange={(e) => setFile(e.target.files?.[0] ?? null)} />

        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </p>
        )}

        <div className="mt-5 flex items-center justify-between">
          <a href={DESIGN_TEMPLATE_PATH} className="text-sm text-violet-700 underline">
            {t("admin.designDownloadTemplate")}
          </a>
          <div className="flex gap-2">
            <button type="button" onClick={onClose}
                    className="rounded-lg border border-slate-200 px-4 py-2 text-sm">
              {t("proto.close")}
            </button>
            <button type="button" onClick={submit} disabled={!file || busy}
                    className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
              {t("admin.designUpload")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
