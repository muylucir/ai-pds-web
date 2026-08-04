"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { useT } from "@/lib/i18n/provider";
import { addModel } from "@/lib/api/models";

export function AddModelModal({
  onAdded, onClose,
}: {
  onAdded: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [display, setDisplay] = useState(true);
  const [busy, setBusy] = useState(false);
  const t = useT();
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !modelId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addModel(name.trim(), modelId.trim(), display);
      onAdded();
      onClose();
    } catch (err) {
      // 실패하면 모달을 닫지 않는다 — 입력을 다시 치게 만들지 않기 위해서다.
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
        <h2 className="text-lg font-bold">모델 추가</h2>
        <form onSubmit={submit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="model-name" className="block text-sm font-medium">
              표시 이름
            </label>
            <input
              id="model-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: Opus 4.8"
              className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 text-sm"
            />
          </div>
          <div>
            <label htmlFor="model-id" className="block text-sm font-medium">
              모델 ID
            </label>
            <input
              id="model-id"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="예: global.anthropic.claude-opus-4-8"
              className="mt-1 w-full rounded-lg border border-slate-200 p-2.5 font-mono text-xs"
            />
            <p className="mt-1 text-xs text-slate-500">
              Bedrock 추론 프로파일 id입니다. 배포 리전에서 모델 액세스가 켜져
              있어야 실제로 호출됩니다.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={display}
              onChange={(e) => setDisplay(e.target.checked)}
              aria-label="콤보박스에 표시"
            />
            콤보박스에 표시 (최대 5개)
          </label>
          {error && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={busy || !name.trim() || !modelId.trim()}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm text-white hover:bg-violet-700 disabled:opacity-50"
            >
              추가
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
