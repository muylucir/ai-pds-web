import Link from "next/link";
import type { ProjectSummary } from "@/lib/api/types";

export function ProjectList({ projects }: { projects: ProjectSummary[] }) {
  if (projects.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-sm text-slate-500">
        아직 생성된 프로젝트가 없습니다. 새 프로젝트를 만들어 워크숍 세션을 시작하세요.
      </div>
    );
  }
  return (
    <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {projects.map((p) => (
        <li key={p.project_id}>
          <Link
            href={`/projects/${p.project_id}/dashboard`}
            className="block bg-white rounded-xl border border-slate-200 p-5 hover:border-violet-300 hover:shadow-sm transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="w-8 h-8 rounded-lg bg-violet-100 text-violet-700 flex items-center justify-center text-sm font-bold">
                🟣
              </span>
              <p className="font-bold truncate">{p.name ?? p.project_id}</p>
            </div>
            <p className="text-xs text-slate-400 mt-2">ID: {p.project_id}</p>
          </Link>
        </li>
      ))}
    </ul>
  );
}
