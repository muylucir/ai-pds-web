"use client";
// frontend/components/workspace/AttachFilesModal.tsx
import { useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import {
  MAX_UPLOAD_BYTES, UPLOAD_ACCEPT, UPLOAD_GROUPS, uploadProblemText, type UploadProblem,
} from "@/lib/uploads";

type Row = { id: number; name: string; problem: UploadProblem | null; done: boolean };

/**
 * 📎이 여는 첨부 모달 — 무엇을 붙일 수 있고 AI가 그것을 어떻게 받는지를 고르기
 * **전에** 보이고, 파일마다 결과와 실패 이유를 보인다.
 *
 * 열 때마다 마운트된다(부모가 `open`일 때만 그린다) — 지난번 목록이 남지 않는다.
 * 닫아도 진행 중인 업로드는 끝까지 간다: 칩에 넣는 것은 `onUpload`(부모)다.
 *
 * 모양과 닫기 규칙(배경 클릭·Escape)은 ImportProjectButton의 모달을 따른다.
 */
export function AttachFilesModal({ onClose, onUpload }: {
  onClose: () => void;
  /** 파일 하나를 올려 첨부 칩에 넣는다. 실패하면 이유를, 성공하면 null을 돌려준다. */
  onUpload: (file: File) => Promise<UploadProblem | null>;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextId = useRef(0);
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);

  // 열릴 때 한 번만 — 아래 효과와 합치면 부모가 다시 그릴 때마다(스트리밍 중 매
  // 프레임) 포커스를 대화상자로 빼앗는다.
  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function add(files: File[]) {
    const added = files.map((f) => ({ id: nextId.current++, file: f }));
    setRows((prev) => [...prev, ...added.map(({ id, file }) =>
      ({ id, name: file.name, problem: null, done: false }))]);
    // 한 번에 하나씩 — 칩이 고른 순서대로 쌓인다.
    for (const { id, file } of added) {
      const problem = await onUpload(file);
      setRows((prev) => prev.map((r) => (r.id === id ? { ...r, problem, done: true } : r)));
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t("upload.title")}
        tabIndex={-1}
        className="bg-white rounded-2xl p-6 max-w-lg w-full max-h-[90vh] overflow-y-auto shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-bold text-lg">{t("upload.title")}</h2>
        <p className="text-sm text-slate-600 mt-2">{t("upload.intro")}</p>

        <dl className="mt-3 space-y-1 text-sm">
          {UPLOAD_GROUPS.map((g) => (
            <div key={g.label} className="flex gap-2">
              <dt className="shrink-0 w-32 text-slate-500">{t(g.label)}</dt>
              <dd className="text-slate-700 font-mono text-xs leading-5">{g.extensions.join(" ")}</dd>
            </div>
          ))}
        </dl>
        <ul className="mt-3 space-y-1 text-xs text-slate-500 list-disc pl-4">
          <li>{t("upload.howRead")}</li>
          <li>{t("upload.limit").replace("{mb}", String(MAX_UPLOAD_BYTES / (1024 * 1024)))}</li>
        </ul>

        <div
          data-testid="attach-drop-zone"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setDragging(false);
            void add(Array.from(e.dataTransfer.files));
          }}
          className={`mt-4 rounded-xl border-2 border-dashed px-4 py-6 text-center text-sm ${
            dragging ? "border-violet-500 bg-violet-50" : "border-slate-300"}`}
        >
          <p className="text-slate-500">{t("upload.dropHere")}</p>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="mt-2 px-4 py-2 text-sm rounded-lg border border-violet-300 text-violet-700 hover:bg-violet-50 font-bold"
          >
            {t("upload.pick")}
          </button>
          <input
            ref={inputRef}
            type="file"
            hidden
            multiple
            accept={UPLOAD_ACCEPT}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = ""; // allow re-selecting the same file
              if (files.length) void add(files);
            }}
          />
        </div>

        {rows.length > 0 && (
          <ul className="mt-4 space-y-1.5 text-sm" aria-label={t("upload.listLabel")}>
            {rows.map((r) => (
              <li key={r.id} className="flex items-start gap-2">
                <span aria-hidden="true">{!r.done ? "⏳" : r.problem ? "⚠️" : "✅"}</span>
                <span className="min-w-0">
                  <span className="break-all">{r.name}</span>
                  <span className={`block text-xs ${r.problem ? "text-rose-600" : "text-slate-500"}`}>
                    {!r.done ? t("upload.uploading")
                      : r.problem ? uploadProblemText(t, r.problem) : t("upload.attached")}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg bg-violet-600 hover:bg-violet-700 text-white font-bold"
          >
            {t("upload.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
