"use client";
import { use } from "react";
import { AppHeader } from "@/components/AppHeader";
import { GetStartedBanner } from "@/components/dashboard/GetStartedBanner";
import { HandoffReadyBanner } from "@/components/handoff/HandoffReadyBanner";
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
  const { modelLabel, language, readOnly } = useProjectMeta(projectId);

  const notFound = state.error instanceof ApiError && state.error.status === 404;

  // Is this a project where nothing has started yet? **`stages` alone cannot tell** —
  // if the state file's `## Stage Progress` is off, even an in-progress project arrives as an empty
  // array (backend parsers/state.py records that observation), and showing "시작하세요" (get started) then
  // would be a lie to a user who is already working. No audit entries means
  // no turn has ever run, which prevents that misjudgment.
  //
  // Why wait for loading to finish: values read with `?? []` are empty arrays during loading too,
  // so without waiting, the banner flashes once even on an in-progress project.
  const settled = !state.loading && !audit.loading;
  const notStarted =
    settled && state.data?.stages.length === 0 && (audit.data ?? []).length === 0;

  return (
    <>
      <AppHeader activeTab="dashboard" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} readOnly={readOnly} />
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

            {notStarted && !readOnly && <GetStartedBanner projectId={projectId} />}
            <HandoffReadyBanner projectId={projectId} state={state.data} className="mb-6" />

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
