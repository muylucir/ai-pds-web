"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import {
  finishImport, stageBundle, type ImportResult, type ImportWarning,
} from "@/lib/api/transfer";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// Character rule for project ids. It **must be the same rule** as CreateProjectForm --
// if only one side is looser, import can produce ids that creation cannot.
const ID_DISALLOWED = /[^A-Za-z0-9_-]/g;

/** Warning code → message key. The backend does not know the UI language, so it sends codes
 *  (WARN_* in backend/aipds/project_import.py). Same discipline as errorMessage. */
const WARNING_KEY: Record<string, keyof Dict> = {
  model_unavailable: "transfer.warnModelUnavailable",
  survey_tokens_reissued: "transfer.warnSurveyTokensReissued",
  unknown_entries: "transfer.warnUnknownEntries",
};

type Phase = "idle" | "uploading" | "importing";

/**
 * Import trigger + a modal holding that flow.
 *
 * **Why a modal.** Import is used only when moving a project over from another
 * instance, while the common tasks on the list screen are creating or opening a
 * project. Keeping the file input always expanded on screen would give that rare
 * operation the same weight as the common ones.
 *
 * The modal's shape follows `ProjectList`'s delete confirmation dialog -- same
 * backdrop treatment, same `role="dialog" aria-modal`, same Escape and backdrop-click
 * rules. This keeps the screen from having two kinds of dialogs.
 */
export function ImportProjectButton({ onImported }: {
  onImported: (result: ImportResult) => void;
}) {
  const t = useT();
  const fileInput = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // The uploaded bundle. It **survives** a 409 (id conflict) -- when the user changes
  // only the id and calls again, the backend reads the same staging object. Without
  // this value, that retry would become a re-upload of hundreds of MB.
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [idRejected, setIdRejected] = useState(false);
  // Navigate after showing the warnings -- passing silently would leave the user using
  // a project that runs on a model they never chose, without even knowing it.
  const [warnings, setWarnings] = useState<ImportWarning[] | null>(null);
  const [imported, setImported] = useState<ImportResult | null>(null);

  const busy = phase !== "idle";

  function close() {
    // Closing resets everything. Holding on to the uploaded bundle would, on reopen,
    // leave "what is uploaded" invisible on screen, and an import pressed in that
    // state would plant the previous file rather than the one just chosen. The
    // staging object is swept by the bucket's lifecycle a day later.
    setOpen(false);
    setFile(null);
    setUploadId(null);
    setNewId("");
    setIdRejected(false);
    setPercent(0);
    setError(null);
    setWarnings(null);
    setImported(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, busy]);

  async function run() {
    setError(null);
    let staged = uploadId;
    try {
      if (staged === null) {
        if (!file) return;
        setPhase("uploading");
        setPercent(0);
        staged = await stageBundle(file, (f) => setPercent(Math.round(f * 100)));
        setUploadId(staged);
      }
      setPhase("importing");
      const result = await finishImport(staged, newId.trim() || undefined);
      if (result.warnings.length > 0) {
        // With warnings, add one confirmation step. Without, navigate immediately --
        // no extra click on the normal path (same flow as creation).
        setWarnings(result.warnings);
        setImported(result);
      } else {
        close();
        onImported(result);
      }
    } catch (err) {
      if (err instanceof ApiError && err.detail === "upload_failed") {
        // The PUT to S3 failed -- the staging cannot be trusted, so discard it.
        setUploadId(null);
        setError(t("transfer.uploadFailed"));
      } else if (err instanceof ApiError && err.status === 409) {
        // The id field opens. uploadId is kept as-is.
        setError(errorMessage(t, err.detail));
      } else if (err instanceof ApiError) {
        if (err.status === 400 || err.status === 413) setUploadId(null);
        setError(errorMessage(t, err.detail));
      } else {
        setError(t("transfer.importFailed"));
      }
    } finally {
      setPhase("idle");
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="px-4 py-2 text-sm rounded-lg border border-violet-300 text-violet-700 hover:bg-violet-50 font-bold whitespace-nowrap"
      >
        {t("transfer.import")}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => !busy && close()}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("transfer.importTitle")}
            className="bg-white rounded-2xl p-6 max-w-lg w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">{t("transfer.importTitle")}</h2>

            {warnings && imported ? (
              <>
                <ul className="mt-3 space-y-1.5 text-sm text-amber-800 list-disc pl-5">
                  {warnings.map((w) => (
                    <li key={w.code}>
                      {WARNING_KEY[w.code]
                        ? t(WARNING_KEY[w.code]).replace("{n}", String(w.count))
                        : w.code}
                    </li>
                  ))}
                </ul>
                <div className="mt-5 flex justify-end">
                  <button
                    type="button"
                    onClick={() => {
                      const result = imported;
                      close();
                      onImported(result);
                    }}
                    className="px-4 py-2 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 text-white font-bold"
                  >
                    {t("transfer.import")}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="text-sm text-slate-600 mt-2">
                  {t("transfer.importHint")}
                </p>
                <div className="mt-4">
                  <label htmlFor="bundle" className="block text-xs text-slate-500 mb-1">
                    {t("transfer.pickFile")}
                  </label>
                  <input
                    id="bundle"
                    ref={fileInput}
                    type="file"
                    accept=".zip,application/zip"
                    disabled={busy}
                    onChange={(e) => {
                      // Choosing a new file makes the previous staging meaningless.
                      setUploadId(null);
                      setError(null);
                      setFile(e.target.files?.[0] ?? null);
                    }}
                    className="w-full text-sm rounded-lg border border-slate-200 p-2 disabled:opacity-50"
                  />
                </div>
                {uploadId !== null && (
                  <div className="mt-3">
                    <label htmlFor="newpid" className="block text-xs text-slate-500 mb-1">
                      {t("transfer.newIdLabel")}
                    </label>
                    <input
                      id="newpid"
                      value={newId}
                      disabled={busy}
                      title={t("project.idCharsHint")}
                      onChange={(e) => {
                        const raw = e.target.value;
                        const clean = raw.replace(ID_DISALLOWED, "");
                        setNewId(clean);
                        if (clean !== raw) setIdRejected(true);
                        else if (clean === "") setIdRejected(false);
                      }}
                      placeholder={t("project.idPlaceholder")}
                      className="w-full text-sm rounded-lg border border-slate-200 p-2.5 focus:outline-none focus:ring-2 focus:ring-violet-400"
                    />
                  </div>
                )}
                {error && (
                  <p className="text-sm text-rose-600 mt-3">
                    {error}
                    {uploadId !== null && ` ${t("transfer.newIdHint")}`}
                  </p>
                )}
                {!error && idRejected && (
                  <p className="text-sm text-rose-600 mt-3">{t("project.idCharsHint")}</p>
                )}
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={close}
                    disabled={busy}
                    className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {t("project.cancel")}
                  </button>
                  <button
                    type="button"
                    onClick={() => void run()}
                    disabled={busy || (file === null && uploadId === null)}
                    className="px-4 py-2 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold whitespace-nowrap"
                  >
                    {phase === "uploading"
                      ? t("transfer.uploading").replace("{n}", String(percent))
                      : phase === "importing"
                        ? t("transfer.importing")
                        : t("transfer.import")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
