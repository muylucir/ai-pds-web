"use client";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errorMessage";
import { useT } from "@/lib/i18n/provider";
import { deleteModel, patchModel, type AdminModel } from "@/lib/api/models";

export function ModelTable({
  models, onChanged,
}: {
  models: AdminModel[];
  onChanged: () => void;
}) {
  const t = useT();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AdminModel | null>(null);

  // 서버가 정책 위반(표시 5개 상한 등)을 알려주면 그 문장을 그대로 보여준다 —
  // 프론트가 규칙을 복제하면 두 곳이 어긋난다(UserTable과 같은 규율).
  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? errorMessage(t, err.detail) : t("err.generic"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      {error && (
        <p role="alert" className="mb-4 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-500">
          <tr className="border-b border-slate-200">
            <th className="py-2 font-medium">이름</th>
            <th className="py-2 font-medium">모델 ID</th>
            <th className="py-2 font-medium">표시</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model_id} className="border-b border-slate-100">
              <td className="py-3">{m.name}</td>
              {/* 관리자는 무엇을 등록했는지 확인해야 하므로 id를 보여준다 —
                  콤보박스가 이름만 보여주는 것과 다른 이유다. */}
              <td className="py-3 font-mono text-xs text-slate-500">{m.model_id}</td>
              <td className="py-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={m.display}
                  aria-label={`${m.name} 표시`}
                  disabled={busy === m.model_id}
                  onClick={() => run(m.model_id,
                    () => patchModel(m.model_id, { display: !m.display }).then(() => undefined))}
                  className={`h-6 w-11 rounded-full transition-colors disabled:opacity-50 ${
                    m.display ? "bg-violet-600" : "bg-slate-300"}`}
                >
                  <span className={`block h-5 w-5 rounded-full bg-white transition-transform ${
                    m.display ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </td>
              <td className="py-3 text-right">
                <button
                  type="button"
                  aria-label={`${m.name} 삭제`}
                  onClick={() => setConfirmDelete(m)}
                  className="text-xs text-rose-600 hover:underline"
                >
                  삭제
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {confirmDelete && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2 className="text-lg font-bold">모델 삭제</h2>
            <p className="mt-2 text-sm text-slate-600">
              {confirmDelete.name}을(를) 목록에서 제거합니다. 이미 이 모델로 만든
              프로젝트는 계속 같은 모델로 돕니다.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = confirmDelete;
                  setConfirmDelete(null);
                  void run(target.model_id, () => deleteModel(target.model_id));
                }}
                className="rounded-lg bg-rose-600 px-4 py-2 text-sm text-white hover:bg-rose-700"
              >
                삭제 확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
