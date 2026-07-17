import Link from "next/link";

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1];
}

export function ArtifactsPanel({ artifacts, projectId }: { artifacts: string[]; projectId: string }) {
  return (
    <section className="bg-white rounded-xl border border-slate-200" aria-labelledby="artifact-heading">
      <div className="px-5 py-4 border-b border-slate-100">
        <h2 id="artifact-heading" className="font-bold">산출물</h2>
      </div>
      {artifacts.length === 0 ? (
        <p className="p-5 text-sm text-slate-400">아직 생성된 산출물이 없습니다.</p>
      ) : (
        <ul className="p-3 text-sm">
          {artifacts.map((path) => {
            const base = basename(path);
            const isDoc = base === "discovery-document.md";
            const inner = (
              <>
                <span className="text-lg" aria-hidden="true">{isDoc ? "📕" : "📄"}</span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{base}</p>
                  <p className="text-xs text-slate-400 truncate">{path}</p>
                </div>
                {isDoc && <span className="text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">Living</span>}
              </>
            );
            return (
              <li key={path}>
                {isDoc ? (
                  <Link href={`/projects/${projectId}/review`} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50">
                    {inner}
                  </Link>
                ) : (
                  <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg">{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
