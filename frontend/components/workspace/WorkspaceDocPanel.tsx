// frontend/components/workspace/WorkspaceDocPanel.tsx
"use client";
import Link from "next/link";
import { readArtifact, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { Markdown } from "@/components/Markdown";

// The workspace's 4th column. Renders the document the CONVERSATION is
// currently about (activeDoc — submit_document 이벤트뿐 아니라 doc성
// file_changed도 추적, ui-bug2 싱크 수정) inline so the user reads it
// without leaving the workspace.
//
// turnSeq: 턴이 끝날 때마다 증가하는 시퀀스. 문서 이벤트가 도착한 시점에는
// VM→S3 동기화 전이라 읽기가 비거나 404일 수 있으므로, 턴 종료 시점에
// 다시 읽는다 (fetch 키에 포함).
//
// Hidden below `lg` — the same responsive posture as StageSidebar and
// WorkspaceRightPanel; on narrow screens the review route is the fallback.
export function WorkspaceDocPanel({
  projectId,
  activeDoc,
  turnSeq,
}: {
  projectId: string;
  activeDoc: { path: string; version: string | null } | null;
  turnSeq: number;
}) {
  const path = activeDoc?.path ?? null;
  const version = activeDoc?.version ?? null;
  // 404 is treated as an empty doc (the file may lag the event by a beat),
  // mirroring the review page; any other error surfaces as a load-error note.
  const content = useAsync(
    () =>
      path === null
        ? Promise.resolve("")
        : readArtifact(projectId, path).catch((e) =>
            e instanceof ApiError && e.status === 404 ? "" : Promise.reject(e),
          ),
    // turnSeq in the key: 턴 종료마다 재읽기 (턴 중 동기화 지연 보정).
    [projectId, path, turnSeq],
  );

  const name = path ? path.slice(path.lastIndexOf("/") + 1) : null;
  const loadError = path !== null && content.error !== null;
  // Version strings from the backend may already carry a "v" prefix (e.g.
  // "v2") or not (e.g. "2"); normalize to a single leading "v".
  const versionLabel = version ? (/^v/i.test(version) ? version : `v${version}`) : null;

  return (
    <aside
      aria-label="생성된 문서"
      className="hidden lg:flex flex-col min-w-0 min-h-0 bg-white border-l border-slate-200"
    >
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-2">
        <p className="text-xs font-bold text-slate-400 uppercase tracking-wide truncate">
          {name ? `📄 ${name}` : "생성된 문서"}
        </p>
        {versionLabel && (
          <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600">
            {versionLabel}
          </span>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 text-sm text-slate-700">
        {path === null ? (
          <p className="text-slate-400">
            아직 생성된 문서가 없습니다. 문서가 만들어지면 여기에서 바로 확인할 수 있습니다.
          </p>
        ) : loadError ? (
          <p className="text-rose-600">문서를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>
        ) : content.loading ? (
          <p className="text-slate-400">문서를 불러오는 중…</p>
        ) : (content.data ?? "").trim() === "" ? (
          <p className="text-slate-400">문서 내용이 아직 비어 있습니다.</p>
        ) : (
          <Markdown text={content.data ?? ""} />
        )}
      </div>
      {path !== null && (
        <div className="p-3 border-t border-slate-100">
          <Link
            href={`/projects/${projectId}/review`}
            className="text-xs font-medium text-violet-700 underline hover:text-violet-900"
          >
            전체 문서 리뷰 화면으로 →
          </Link>
        </div>
      )}
    </aside>
  );
}
