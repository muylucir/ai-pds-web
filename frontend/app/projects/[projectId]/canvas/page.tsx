"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CanvasSidebar } from "@/components/canvas/CanvasSidebar";
import { ChatTimeline } from "@/components/canvas/ChatTimeline";
import { ChatInput } from "@/components/canvas/ChatInput";
import { PreviewPanel } from "@/components/canvas/PreviewPanel";
import { getState, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useTurnStream } from "@/lib/useTurnStream";

export default function CanvasPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const { items, streaming, send } = useTurnStream(projectId);

  const notFound = state.error instanceof ApiError && state.error.status === 404;
  const loadError = state.error && !notFound;

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <AppHeader activeTab="canvas" projectId={projectId} />
      <div className="flex-1 flex min-h-0">
        {state.data ? (
          <CanvasSidebar state={state.data} />
        ) : (
          <aside
            className="hidden lg:flex w-60 shrink-0 bg-white border-r border-slate-200 flex-col p-4 text-sm"
            aria-label="스테이지 진행 상황"
          >
            {state.loading && <p className="text-slate-400">불러오는 중…</p>}
            {notFound && <p className="text-rose-600">프로젝트를 찾을 수 없습니다.</p>}
            {loadError && (
              <p className="text-rose-600">진행 상황을 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
            )}
          </aside>
        )}

        <main className="flex-1 flex flex-col min-w-0 bg-slate-50">
          <ChatTimeline items={items} />
          <ChatInput onSend={send} disabled={streaming} />
        </main>

        <PreviewPanel projectId={projectId} />
      </div>
    </div>
  );
}
