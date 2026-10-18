"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  DESIGN_TEMPLATE_PATH, previewDesignProfile, uploadDesignProfile,
  type DesignPreview,
} from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";
import { COLOUR_RE } from "./DesignProfileCard";

//: `as const` — useT() accepts only defined keys, so it must be narrowed to literals (typos
//  are caught by type checking, not at runtime).
const ORIGIN_KEY = {
  fence: "admin.designOriginFence",
  extracted: "admin.designOriginExtracted",
  none: "admin.designOriginNone",
} as const satisfies Record<DesignPreview["origin"], string>;

/**
 * Upload has two steps: read tokens from the document (1), and save after the admin confirms (2).
 *
 * Why there is a confirmation step (observed 2026-08-19): the ```tokens fence is a convention only our
 * format has, so DESIGN.md files made elsewhere come up without the fence. Extracting values from such
 * a document involves slots the document does not answer — in a document that gives different greens to
 * the brand heading and the CTA, only a person knows which one is `primary`. So the values are shown
 * before saving and can be corrected.
 */
export function UploadDesignModal(
  { onUploaded, onClose, replacing }:
  { onUploaded: () => void; onClose: () => void; replacing: boolean },
) {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<DesignPreview | null>(null);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The 400's detail is the line number the backend pointed out — shown as is. It is not re-parsed
  // here (two parsers would drift).
  function show(err: unknown) {
    setError(err instanceof ApiError && err.detail
      ? err.detail : t("admin.designUploadFailed"));
  }

  async function readTokens() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await previewDesignProfile(file);
      setPreview(result);
      setTokens(result.tokens);
    } catch (err) {
      show(err);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!file || !preview) return;
    setBusy(true);
    setError(null);
    try {
      // If the document already has a fence, no values are sent — the server ignores them anyway, but not
      // sending them says "that file is the authority" in code.
      await uploadDesignProfile(file,
        preview.origin === "fence" ? undefined : tokens);
      onUploaded();
      onClose();
    } catch (err) {
      show(err);
    } finally {
      setBusy(false);
    }
  }

  const entries = Object.entries(tokens);

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
               onChange={(e) => {
                 setFile(e.target.files?.[0] ?? null);
                 // Changing the file means the earlier confirmation result no longer belongs to that file.
                 setPreview(null);
                 setTokens({});
               }} />

        {busy && !preview && (
          <p className="mt-3 text-sm text-slate-500">{t("admin.designPreviewBusy")}</p>
        )}

        {preview && (
          <section className="mt-4 rounded-lg bg-slate-50 p-3">
            <p className={`text-sm ${preview.origin === "none"
              ? "text-amber-700" : "text-slate-600"}`}>
              {t(ORIGIN_KEY[preview.origin])}
            </p>
            {entries.length > 0 && (
              <ul className="mt-3 space-y-2">
                {entries.map(([key, value]) => (
                  <li key={key} className="flex items-center gap-2">
                    {COLOUR_RE.test(value) && (
                      <span aria-hidden className="size-5 shrink-0 rounded border border-slate-200"
                            style={{ backgroundColor: value }} />
                    )}
                    <span className="w-40 shrink-0 text-xs text-slate-500">{key}</span>
                    <input aria-label={key} value={value}
                           readOnly={preview.origin === "fence"}
                           onChange={(e) => setTokens(
                             { ...tokens, [key]: e.target.value })}
                           className="w-full rounded border border-slate-200 px-2 py-1 font-mono text-xs" />
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

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
            {preview
              ? (
                <button type="button" onClick={save} disabled={busy}
                        className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                  {t("admin.designUpload")}
                </button>
              )
              : (
                <button type="button" onClick={readTokens} disabled={!file || busy}
                        className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                  {t("admin.designNext")}
                </button>
              )}
          </div>
        </div>
      </div>
    </div>
  );
}
