"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { GetStartedBanner } from "@/components/dashboard/GetStartedBanner";
import { ProgressCards } from "@/components/dashboard/ProgressCards";
import { StageTimeline } from "@/components/dashboard/StageTimeline";
import { ArtifactsPanel } from "@/components/dashboard/ArtifactsPanel";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { getState, listArtifacts, getAudit, listQuestionFiles, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useT } from "@/lib/i18n/provider";

export default function DashboardPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const state = useAsync(() => getState(projectId), [projectId]);
  const artifacts = useAsync(() => listArtifacts(projectId), [projectId]);
  const audit = useAsync(() => getAudit(projectId), [projectId]);
  const questionFiles = useAsync(() => listQuestionFiles(projectId), [projectId]);
  const t = useT();
  const { modelLabel, language } = useProjectMeta(projectId);

  const notFound = state.error instanceof ApiError && state.error.status === 404;

  // 아직 아무것도 시작하지 않은 프로젝트인가. **`stages`만으로는 판단할 수 없다** —
  // 상태 파일의 `## Stage Progress`가 어긋나면 진행 중인 프로젝트도 빈 배열로
  // 오고(backend parsers/state.py에 그 실측이 적혀 있다), 그때 "시작하세요"를
  // 띄우면 이미 일하고 있는 사용자에게 거짓말이 된다. audit 항목이 없다는 것은
  // 턴이 한 번도 돌지 않았다는 뜻이라 그 오판을 막는다.
  //
  // 로딩이 끝나기를 기다리는 이유: `?? []`로 읽는 값은 로딩 중에도 빈 배열이므로,
  // 기다리지 않으면 진행 중인 프로젝트에서도 배너가 한 번 번쩍인다.
  const settled = !state.loading && !audit.loading;
  const notStarted =
    settled && state.data?.stages.length === 0 && (audit.data ?? []).length === 0;

  return (
    <>
      <AppHeader activeTab="dashboard" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        {notFound && <p className="text-sm text-rose-600">{t("page.projectNotFound")}</p>}
        {!notFound && state.error && (
          <p className="text-sm text-rose-600">{t("page.dashboardLoadFailed")}</p>
        )}
        {state.loading && <p className="text-sm text-slate-400">{t("page.loading")}</p>}

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
                <p className="text-sm text-slate-500 mt-1">{t("page.currentStage")}: {state.data.current_stage}</p>
              )}
            </div>

            {notStarted && <GetStartedBanner projectId={projectId} />}

            <ProgressCards
              state={state.data}
              questionFileCount={questionFiles.data?.length ?? 0}
              artifactCount={artifacts.data?.length ?? 0}
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
