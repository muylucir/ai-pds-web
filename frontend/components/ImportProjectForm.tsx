"use client";
import { useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import {
  finishImport, stageBundle, type ImportResult, type ImportWarning,
} from "@/lib/api/transfer";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// 프로젝트 id의 문자 규칙. CreateProjectForm과 **같은 규칙이어야 한다** — 한쪽만
// 느슨하면 생성으로는 만들 수 없는 id가 가져오기로 생긴다.
const ID_DISALLOWED = /[^A-Za-z0-9_-]/g;

/** 경고 코드 → 문구 키. 백엔드는 UI 언어를 모르므로 코드를 보낸다
 *  (backend/aipds/project_import.py의 WARN_*). errorMessage와 같은 규율이다. */
const WARNING_KEY: Record<string, keyof Dict> = {
  model_unavailable: "transfer.warnModelUnavailable",
  survey_tokens_reissued: "transfer.warnSurveyTokensReissued",
  unknown_entries: "transfer.warnUnknownEntries",
};

type Phase = "idle" | "uploading" | "importing";

export function ImportProjectForm({ onImported }: {
  onImported: (result: ImportResult) => void;
}) {
  const t = useT();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // 올려 둔 번들. 409(id 충돌) 뒤에도 **살아 있다** — 사용자가 id만 바꿔 다시
  // 부르면 백엔드가 같은 스테이징 객체를 읽는다. 이 값이 없으면 그 재시도가
  // 수백 MB 재업로드가 된다.
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [idRejected, setIdRejected] = useState(false);
  // 경고를 보여 준 뒤 이동한다 — 조용히 넘기면 사용자는 고른 적 없는 모델로
  // 도는 프로젝트를 그것도 모른 채 쓴다.
  const [warnings, setWarnings] = useState<ImportWarning[] | null>(null);
  const [imported, setImported] = useState<ImportResult | null>(null);

  const busy = phase !== "idle";

  function reset() {
    setFile(null);
    setUploadId(null);
    setNewId("");
    setIdRejected(false);
    setPercent(0);
    if (fileInput.current) fileInput.current.value = "";
  }

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
        // 경고가 있으면 확인 단계를 하나 둔다. 없으면 곧바로 이동한다 —
        // 정상 경로에 클릭을 더하지 않는다(생성과 같은 흐름).
        setWarnings(result.warnings);
        setImported(result);
      } else {
        reset();
        onImported(result);
      }
    } catch (err) {
      if (err instanceof ApiError && err.detail === "upload_failed") {
        // S3로 가는 PUT이 실패했다 — 스테이징을 신뢰할 수 없으므로 버린다.
        setUploadId(null);
        setError(t("transfer.uploadFailed"));
      } else if (err instanceof ApiError && err.status === 409) {
        // id 칸이 열린다. uploadId는 그대로 둔다.
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

  if (warnings && imported) {
    return (
      <div className="bg-white rounded-xl border border-amber-300 p-5 mb-8">
        <h2 className="font-bold text-sm">{t("transfer.importTitle")}</h2>
        <ul className="mt-3 space-y-1.5 text-sm text-amber-800 list-disc pl-5">
          {warnings.map((w) => (
            <li key={w.code}>
              {WARNING_KEY[w.code]
                ? t(WARNING_KEY[w.code]).replace("{n}", String(w.count))
                : w.code}
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => {
              const result = imported;
              setWarnings(null);
              setImported(null);
              reset();
              onImported(result);
            }}
            className="px-4 py-2 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 text-white font-bold"
          >
            {t("transfer.import")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 mb-8">
      <h2 className="font-bold text-sm">{t("transfer.importTitle")}</h2>
      <p className="text-xs text-slate-500 mt-1">{t("transfer.importHint")}</p>
      <div className="mt-3 flex flex-col sm:flex-row sm:items-end gap-3">
        <div className="flex-1">
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
              // 파일을 새로 고르면 이전 스테이징은 의미가 없다.
              setUploadId(null);
              setError(null);
              setFile(e.target.files?.[0] ?? null);
            }}
            className="w-full text-sm rounded-lg border border-slate-200 p-2 disabled:opacity-50"
          />
        </div>
        {uploadId !== null && (
          <div className="sm:w-56">
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
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || (file === null && uploadId === null)}
          className="px-5 py-2.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-bold whitespace-nowrap"
        >
          {phase === "uploading"
            ? t("transfer.uploading").replace("{n}", String(percent))
            : phase === "importing" ? t("transfer.importing") : t("transfer.import")}
        </button>
      </div>
      {error && (
        <p className="text-sm text-rose-600 mt-3">
          {error}
          {uploadId !== null && ` ${t("transfer.newIdHint")}`}
        </p>
      )}
      {!error && idRejected && (
        <p className="text-sm text-rose-600 mt-3">{t("project.idCharsHint")}</p>
      )}
    </div>
  );
}
