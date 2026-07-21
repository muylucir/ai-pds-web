"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ProjectPage, ProjectProgress, ProjectSummary } from "@/lib/api/types";
import { deleteProject } from "@/lib/api/client";

function progressLabel(p: ProjectProgress | null | undefined): string {
  if (!p) return "—";
  const count = `(${p.completed}/${p.total})`;
  return p.current_stage ? `${p.current_stage} ${count}` : count;
}

export function ProjectList({
  data,
  onDeleted,
  onPageChange,
}: {
  data: ProjectPage;
  onDeleted: () => void;
  onPageChange: (page: number) => void;
}) {
  const [target, setTarget] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  if (data.total === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.size));

  return (
    <>
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
              <th scope="col" className="px-4 py-3 font-medium">프로젝트 ID</th>
              <th scope="col" className="px-4 py-3 font-medium">프로젝트명</th>
              <th scope="col" className="px-4 py-3 font-medium">진행상황</th>
              <th scope="col" className="px-4 py-3 w-12">
                <span className="sr-only">삭제</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.projects.map((p) => (
              // relative + 이름 링크의 after:inset-0 스트레치드 링크 — 행 어디를
              // 클릭해도 대시보드로 이동하되, 삭제 버튼은 z-10으로 위에 띄운다.
              <tr key={p.project_id} className="relative border-b border-slate-100 last:border-0 hover:bg-violet-50/40">
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{p.project_id}</td>
                <td className="px-4 py-3 font-medium">
                  <Link
                    href={`/projects/${p.project_id}/dashboard`}
                    className="text-slate-900 hover:text-violet-700 after:absolute after:inset-0 after:content-['']"
                  >
                    {p.name ?? p.project_id}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-600">{progressLabel(p.progress)}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    type="button"
                    aria-label={`${p.name ?? p.project_id} 프로젝트 삭제`}
                    onClick={() => {
                      setError(null);
                      setTarget(p);
                    }}
                    className="relative z-10 w-8 h-8 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 inline-flex items-center justify-center"
                  >
                    🗑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-4 text-sm text-slate-500">
        <span>총 {data.total}건</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label="이전 페이지"
            disabled={data.page <= 1}
            onClick={() => onPageChange(data.page - 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            ‹ 이전
          </button>
          <span>{data.page} / {totalPages}</span>
          <button
            type="button"
            aria-label="다음 페이지"
            disabled={data.page >= totalPages}
            onClick={() => onPageChange(data.page + 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-40 disabled:pointer-events-none"
          >
            다음 ›
          </button>
        </div>
      </div>

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
