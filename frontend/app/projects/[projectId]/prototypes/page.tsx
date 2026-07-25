// frontend/app/projects/[projectId]/prototypes/page.tsx — the prototype tab:
// a grid of PrototypeCard (Task 8) driven by GET /prototypes, opening
// BuildPanel (Task 9) for the build chat + hosting controls per card.
"use client";
import { use, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PrototypeCard } from "@/components/prototypes/PrototypeCard";
import { BuildPanel } from "@/components/prototypes/BuildPanel";
import { SurveyPanel } from "@/components/prototypes/SurveyPanel";
import {
  listPrototypes,
  startSession,
  startHost,
  stopHost,
  getHost,
  prototypePreviewUrl,
} from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function PrototypesPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const list = useAsync(() => listPrototypes(projectId), [projectId]);

  // Which card's build session is open, and whether THIS open should fire
  // the auto first-build turn — true only when startSession just created a
  // brand-new session (202); a 409 reopen of an already-live session must
  // not re-fire it.
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const [openAutoStart, setOpenAutoStart] = useState(false);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [logsSlug, setLogsSlug] = useState<string | null>(null);
  const [logsText, setLogsText] = useState<string | null>(null);
  const [logsError, setLogsError] = useState<string | null>(null);

  async function handleBuild(slug: string) {
    setBusySlug(slug);
    try {
      let autoStart = true;
      try {
        await startSession(projectId, slug);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Already-open session — proceed straight to the panel without
          // re-triggering the auto first-build turn.
          autoStart = false;
        } else {
          throw err;
        }
      }
      setOpenAutoStart(autoStart);
      setOpenSlug(slug);
    } finally {
      setBusySlug(null);
    }
  }

  async function handleStartHost(slug: string) {
    setBusySlug(slug);
    try {
      await startHost(projectId, slug);
    } finally {
      setBusySlug(null);
      list.reload();
    }
  }

  async function handleStopHost(slug: string) {
    setBusySlug(slug);
    try {
      await stopHost(projectId, slug);
    } finally {
      setBusySlug(null);
      list.reload();
    }
  }

  function handleOpenPreview(slug: string) {
    window.open(prototypePreviewUrl(projectId, slug), "_blank", "noopener,noreferrer");
  }

  async function handleShowLogs(slug: string) {
    setLogsSlug(slug);
    setLogsText(null);
    setLogsError(null);
    try {
      const status = await getHost(projectId, slug);
      setLogsText(status?.log_tail ?? "");
    } catch {
      setLogsError("로그를 불러오지 못했습니다.");
    }
  }

  function closeBuildPanel() {
    setOpenSlug(null);
    list.reload();
  }

  return (
    <>
      <AppHeader activeTab="prototypes" projectId={projectId} />
      <main className="max-w-5xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold mb-6">프로토타입</h1>

        {list.loading && <p className="text-sm text-slate-400">불러오는 중…</p>}
        {list.error && <p className="text-sm text-rose-600">목록을 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>}
        {list.data && list.data.length === 0 && (
          <p className="text-sm text-slate-400">아직 프로토타입 스펙이 없습니다.</p>
        )}

        {list.data && list.data.length > 0 && (
          <div className="grid gap-3">
            {list.data.map((info) => (
              <PrototypeCard
                key={info.slug}
                info={info}
                busy={busySlug === info.slug}
                onBuild={() => handleBuild(info.slug)}
                onStartHost={() => handleStartHost(info.slug)}
                onStopHost={() => handleStopHost(info.slug)}
                onOpenPreview={info.state === "running" ? () => handleOpenPreview(info.slug) : undefined}
                onShowLogs={() => handleShowLogs(info.slug)}
              />
            ))}
          </div>
        )}

        {openSlug && <SurveyPanel projectId={projectId} slug={openSlug} />}
      </main>

      {openSlug && (
        <BuildPanel projectId={projectId} slug={openSlug} autoStart={openAutoStart} onClose={closeBuildPanel} />
      )}

      {logsSlug && (
        <div
          className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => setLogsSlug(null)}
        >
          <div
            role="dialog"
            aria-label={`${logsSlug} 로그`}
            className="bg-white rounded-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-bold">{logsSlug} 로그</h2>
              <button type="button" aria-label="닫기" className="text-slate-400" onClick={() => setLogsSlug(null)}>
                ✕
              </button>
            </div>
            {logsError && <p className="text-sm text-rose-600">{logsError}</p>}
            {logsText !== null && (
              <pre className="text-xs bg-slate-50 border border-slate-200 rounded-lg p-3 whitespace-pre-wrap break-all">
                {logsText || "(로그 없음)"}
              </pre>
            )}
          </div>
        </div>
      )}
    </>
  );
}
