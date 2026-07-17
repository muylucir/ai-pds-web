"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ProgressCards } from "@/components/dashboard/ProgressCards";
import { StageTimeline } from "@/components/dashboard/StageTimeline";
import { ArtifactsPanel } from "@/components/dashboard/ArtifactsPanel";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { getState, listArtifacts, getAudit, listQuestionFiles, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function DashboardPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const artifacts = useAsync(() => listArtifacts(projectId), [projectId]);
  const audit = useAsync(() => getAudit(projectId), [projectId]);
  const questionFiles = useAsync(() => listQuestionFiles(projectId), [projectId]);

  const notFound = state.error instanceof ApiError && state.error.status === 404;

  return (
    <>
      <AppHeader activeTab="dashboard" projectId={projectId} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        {notFound && <p className="text-sm text-rose-600">프로젝트를 찾을 수 없습니다.</p>}
        {!notFound && state.error && (
          <p className="text-sm text-rose-600">대시보드를 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {state.loading && <p className="text-sm text-slate-400">불러오는 중…</p>}

        {state.data && (
          <>
            <div className="mb-8">
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold">{projectId}</h1>
                <span className="text-xs px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 font-medium">🟣 DISCOVERY</span>
                {state.data.project_type && (
                  <span className="text-xs px-2.5 py-1 rounded-full bg-slate-100 text-slate-600">{state.data.project_type}</span>
                )}
              </div>
              {state.data.current_stage && (
                <p className="text-sm text-slate-500 mt-1">현재 단계: {state.data.current_stage}</p>
              )}
            </div>

            <ProgressCards
              state={state.data}
              pendingQuestions={questionFiles.data?.length ?? 0}
              artifactCount={artifacts.data?.length ?? 0}
              projectId={projectId}
            />

            <div className="grid lg:grid-cols-3 gap-6">
              <StageTimeline state={state.data} projectId={projectId} />
              <div className="space-y-6">
                <ArtifactsPanel artifacts={artifacts.data ?? []} projectId={projectId} />
                <ActivityFeed entries={audit.data ?? []} />
              </div>
            </div>
          </>
        )}
      </main>
    </>
  );
}
