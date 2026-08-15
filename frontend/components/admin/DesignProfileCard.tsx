"use client";
import { DESIGN_RAW_PATH, type DesignProfile } from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";

const COLOUR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function DesignProfileCard(
  { profile, onReplace, onRemove }:
  { profile: DesignProfile; onReplace: () => void; onRemove: () => void },
) {
  const t = useT();
  const entries = Object.entries(profile.tokens);
  return (
    <section className="rounded-xl border border-slate-200 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium">{profile.filename}</p>
          <p className="mt-1 text-xs text-slate-500">
            {t("admin.designUploadedBy")}: {profile.uploaded_by} ·{" "}
            {profile.uploaded_at.slice(0, 10)}
          </p>
        </div>
        <div className="flex gap-2">
          <a href={DESIGN_RAW_PATH}
             className="rounded-lg border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50">
            {t("admin.designDownloadRaw")}
          </a>
          <button type="button" onClick={onReplace}
                  className="rounded-lg bg-violet-600 px-3 py-2 text-sm text-white hover:bg-violet-700">
            {t("admin.designReplace")}
          </button>
          <button type="button" onClick={onRemove}
                  className="rounded-lg border border-rose-200 px-3 py-2 text-sm text-rose-700 hover:bg-rose-50">
            {t("admin.designRemove")}
          </button>
        </div>
      </div>

      <h2 className="mt-5 text-sm font-medium">{t("admin.designTokens")}</h2>
      {entries.length === 0 && (
        <p className="mt-2 text-sm text-slate-500">—</p>
      )}
      <ul className="mt-2 flex flex-wrap gap-3">
        {entries.map(([key, value]) => (
          <li key={key} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
            {COLOUR_RE.test(value) && (
              <span aria-hidden className="size-5 rounded border border-slate-200"
                    style={{ backgroundColor: value }} />
            )}
            <span className="text-xs text-slate-500">{key}</span>
            <span className="text-xs font-mono">{value}</span>
          </li>
        ))}
      </ul>

      {profile.prose.trim() && (
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-medium">
            {t("admin.designProse")}
          </summary>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">
            {profile.prose}
          </pre>
        </details>
      )}
    </section>
  );
}
