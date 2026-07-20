"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ProjectSummary } from "@/lib/api/types";
import { deleteProject } from "@/lib/api/client";

export function ProjectList({
  projects,
  onDeleted,
}: {
  projects: ProjectSummary[];
  onDeleted: () => void;
}) {
  // 삭제 확인 다이얼로그 대상 (null = 닫힘)
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape로 닫기 — 워크스페이스 bottom-sheet와 동일한 최소 접근성 패턴
  useEffect(() => {
    if (!target) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) setTarget(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [target, busy]);

  async function confirmDelete() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await deleteProject(target.project_id);
      setTarget(null);
      onDeleted();
    } catch {
      setError("삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  if (projects.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }
  return (
    <>
      <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map((p) => (
          <li key={p.project_id} className="relative">
            <Link
              href={`/projects/${p.project_id}/dashboard`}
              className="block bg-white rounded-xl border border-slate-200 p-5 hover:border-violet-300 hover:shadow-sm transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="w-8 h-8 rounded-lg bg-violet-100 text-violet-700 flex items-center justify-center text-sm font-bold">
                  🟣
                </span>
                <p className="font-bold truncate pr-8">{p.name ?? p.project_id}</p>
              </div>
              <p className="text-xs text-slate-400 mt-2">ID: {p.project_id}</p>
            </Link>
            {/* Link 밖(li 안) absolute 배치 — 카드 내비게이션과 클릭 충돌 방지 */}
            <button
              type="button"
              aria-label={`${p.name ?? p.project_id} 프로젝트 삭제`}
              onClick={() => {
                setError(null);
                setTarget(p);
              }}
              className="absolute top-3 right-3 w-8 h-8 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 flex items-center justify-center"
            >
              🗑
            </button>
          </li>
        ))}
      </ul>

      {target && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => !busy && setTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="프로젝트 삭제 확인"
            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">
              &apos;{target.name ?? target.project_id}&apos; 프로젝트 삭제
            </h2>
            <p className="text-sm text-slate-600 mt-2">
              채팅 기록과 모든 문서가 영구 삭제되며 되돌릴 수 없습니다.
            </p>
            {error && <p className="text-sm text-rose-600 mt-3">{error}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setTarget(null)}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50"
              >
                삭제
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
