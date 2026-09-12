"use client";
import { useEffect, useRef, useState } from "react";
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

/**
 * 가져오기 트리거 + 그 흐름을 담은 모달.
 *
 * **왜 모달인가.** 가져오기는 다른 인스턴스에서 프로젝트를 옮겨 올 때만 쓰는
 * 조작이고, 목록 화면의 흔한 일은 프로젝트를 만들거나 여는 것이다. 파일 입력을
 * 화면에 늘 펼쳐 두면 그 드문 조작이 흔한 조작과 같은 무게를 갖는다.
 *
 * 모달의 모양은 `ProjectList`의 삭제 확인 대화상자를 따른다 — 같은 배경 처리,
 * 같은 `role="dialog" aria-modal`, 같은 Escape·배경 클릭 규칙. 화면에 대화상자가
 * 두 종류 생기지 않게 한다.
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

  function close() {
    // 닫기는 전부 되돌린다. 업로드해 둔 번들을 붙들고 있으면 다시 열었을 때
    // "무엇이 올라가 있는지"가 화면에 보이지 않는 상태가 되고, 그 상태에서
    // 누른 가져오기가 방금 고른 파일이 아니라 지난번 파일을 심는다. 스테이징
    // 객체는 버킷의 lifecycle이 하루 뒤에 걷는다.
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
        // 경고가 있으면 확인 단계를 하나 둔다. 없으면 곧바로 이동한다 —
        // 정상 경로에 클릭을 더하지 않는다(생성과 같은 흐름).
        setWarnings(result.warnings);
        setImported(result);
      } else {
        close();
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
                      // 파일을 새로 고르면 이전 스테이징은 의미가 없다.
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
