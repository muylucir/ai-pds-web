"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  DESIGN_TEMPLATE_PATH, previewDesignProfile, uploadDesignProfile,
  type DesignPreview,
} from "@/lib/api/design";
import { useT } from "@/lib/i18n/provider";
import { COLOUR_RE } from "./DesignProfileCard";

//: `as const` — useT()는 정의된 키만 받으므로 리터럴로 좁혀야 한다(오타가
//  런타임이 아니라 타입 검사에서 걸린다).
const ORIGIN_KEY = {
  fence: "admin.designOriginFence",
  extracted: "admin.designOriginExtracted",
  none: "admin.designOriginNone",
} as const satisfies Record<DesignPreview["origin"], string>;

/**
 * 업로드는 두 단계다: 문서에서 토큰을 읽고(1) 관리자가 확인한 뒤 저장한다(2).
 *
 * 왜 확인 단계가 있는가(2026-08-19 실측): ```tokens 펜스는 우리 서식에만 있는
 * 관례라 밖에서 만들어진 DESIGN.md는 펜스 없이 올라온다. 그 문서에서 값을 뽑는
 * 일에는 문서가 답하지 않는 자리가 있다 — 브랜드 헤딩과 CTA에 서로 다른 초록을
 * 주는 문서에서 어느 것이 `primary`인지는 사람만 안다. 그래서 저장 전에 값을
 * 보여주고 고칠 수 있게 한다.
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

  // 400의 detail은 백엔드가 짚어준 줄 번호다 — 그대로 보여준다. 여기서 다시
  // 파싱하지 않는다(파서가 두 벌이 되면 어긋난다).
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
      // 문서에 이미 펜스가 있으면 값을 보내지 않는다 — 서버도 무시하지만, 보내지
      // 않는 것이 "그 파일이 권위다"를 코드로 말한다.
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
                 // 파일을 바꾸면 앞선 확인 결과는 그 파일의 것이 아니다.
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
