// frontend/app/projects/[projectId]/prototypes/page.tsx — the prototype tab:
// a grid of PrototypeCard (Task 8) driven by GET /prototypes, opening
// BuildPanel (Task 9) for the build chat + hosting controls per card.
"use client";
import { use, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PrototypeCard } from "@/components/prototypes/PrototypeCard";
import { BuildPanel } from "@/components/prototypes/BuildPanel";
import { SurveyPanel } from "@/components/prototypes/SurveyPanel";
import {
  listPrototypes,
  prototypeArchiveUrl,
  startSession,
  startHost,
  stopHost,
  getHost,
  prototypePreviewUrl,
  resetPrototype,
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
  // Which card's survey panel is open. Deliberately SEPARATE from openSlug:
  // the build drawer is a full-screen `fixed inset-0` overlay, so sharing the
  // condition meant the survey panel only ever rendered underneath it — i.e.
  // it was unreachable.
  const [surveySlug, setSurveySlug] = useState<string | null>(null);
  const [logsSlug, setLogsSlug] = useState<string | null>(null);
  const [logsText, setLogsText] = useState<string | null>(null);
  const [logsError, setLogsError] = useState<string | null>(null);
  // The reset confirmation dialog. `answers` is captured at the moment the
  // dialog opens (see handleReset) rather than read off `list.data`, which
  // can be stale — a workshop's survey responses arrive live while this page
  // sits open, so the count shown here must be re-fetched at click time or a
  // 0→N transition between page-load and click silently drops the
  // irreversibility warning. `null` means the refetch itself failed — treated
  // as "unknown, assume the worst" rather than as zero.
  const [resetTarget, setResetTarget] = useState<{ slug: string; answers: number | null } | null>(
    null,
  );
  const [resetError, setResetError] = useState<string | null>(null);

  useEffect(() => {
    if (!resetTarget) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && busySlug !== resetTarget.slug) setResetTarget(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [resetTarget, busySlug]);

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

  // Opens the confirmation dialog. `list.data`'s response_count can be
  // stale — a workshop's survey answers arrive live while this page sits
  // open — so the count the dialog warns about is re-fetched right here,
  // at click time, the same point-of-use-refresh SurveyPanel.reload uses.
  // A failed refetch must not block the reset (the destructive action still
  // has to work), but it also must not silently fall back to a stale/zero
  // count and understate the risk — so an unknown count renders as its own
  // explicit warning rather than as "0 응답".
  async function handleReset(slug: string) {
    setResetError(null);
    let answers: number | null;
    try {
      const fresh = await listPrototypes(projectId);
      answers = fresh.prototypes.find((p) => p.slug === slug)?.response_count ?? 0;
    } catch {
      answers = null;
    }
    setResetTarget({ slug, answers });
  }

  async function confirmReset() {
    if (!resetTarget) return;
    const { slug } = resetTarget;
    setBusySlug(slug);
    setResetError(null);
    try {
      await resetPrototype(projectId, slug);
      setResetTarget(null);
    } catch {
      // A 502 means the purge was only partial — every step is idempotent,
      // so this is "press it again," not "give up." Shown INSIDE the
      // dialog (not a disconnected alert) so it sits next to what failed,
      // and the dialog stays open so retrying is one click away.
      setResetError("초기화가 완료되지 않았습니다. 다시 시도해 주세요.");
    } finally {
      // Runs even on failure: a partial reset still deleted things, and the
      // card must reflect that — if it still reads "빌드 완료" afterwards,
      // that is the honest signal that the reset didn't finish.
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

        {list.data && list.data.active_builds >= list.data.max_builds && (
          <p className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            동시 빌드 상한({list.data.max_builds}건)에 도달했습니다 — 진행 중인 빌드가
            끝나면 새 빌드를 시작할 수 있습니다.
          </p>
        )}

        {list.data && list.data.prototypes.length === 0 && (
          <p className="text-sm text-slate-400">아직 프로토타입 스펙이 없습니다.</p>
        )}

        {list.data && list.data.prototypes.length > 0 && (
          <div className="grid gap-3">
            {list.data.prototypes.map((info) => (
              <PrototypeCard
                key={info.slug}
                info={info}
                busy={busySlug === info.slug}
                onBuild={() => handleBuild(info.slug)}
                onStartHost={() => handleStartHost(info.slug)}
                onStopHost={() => handleStopHost(info.slug)}
                onOpenPreview={info.state === "running" ? () => handleOpenPreview(info.slug) : undefined}
                onShowLogs={() => handleShowLogs(info.slug)}
                onOpenSurvey={() =>
                  setSurveySlug((cur) => (cur === info.slug ? null : info.slug))
                }
                onReset={handleReset}
                archiveUrl={prototypeArchiveUrl(projectId, info.slug)}
              />
            ))}
          </div>
        )}

        {surveySlug && (
          <div className="mt-8 border-t border-slate-200 pt-6">
            <SurveyPanel projectId={projectId} slug={surveySlug} />
          </div>
        )}
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

      {resetTarget && (
        <div
          className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-6"
          onClick={() => busySlug !== resetTarget.slug && setResetTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="프로토타입 초기화 확인"
            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">
              &apos;{resetTarget.slug}&apos; 프로토타입 초기화
            </h2>
            {/* Reassurance sits right under the title, not at the end — it
                sets context ("this isn't total") but must not be the last
                thing read, or it blunts the irreversibility warning below. */}
            <p className="text-xs text-slate-400 mt-1">
              설계 문서(PROTOTYPE-*.md)는 남으므로 다시 빌드할 수 있습니다.
            </p>
            <p className="text-sm text-slate-600 mt-3">다음 항목이 삭제됩니다:</p>
            <ul className="text-sm text-slate-600 mt-1 list-disc list-inside space-y-0.5">
              <li>빌드 결과와 실행 중인 서버</li>
              <li>빌드 대화 기록</li>
              <li>검증 설문</li>
            </ul>
            {/* Irreversibility gets its OWN sentence, placed last, with
                nothing after it to soften it — matching ProjectList.tsx's
                weighting. It only appears when there is something
                irreversible to lose, so the 0-response case stays quiet. */}
            {resetTarget.answers !== null && resetTarget.answers > 0 && (
              <p className="text-sm font-semibold text-rose-600 mt-3">
                응답 {resetTarget.answers}건은 되돌릴 수 없습니다.
              </p>
            )}
            {resetTarget.answers === null && (
              <p className="text-sm font-semibold text-amber-700 mt-3">
                현재 응답 수를 확인하지 못했습니다 — 응답이 있다면 되돌릴 수 없이 삭제됩니다.
              </p>
            )}
            {resetError && <p className="text-sm text-rose-600 mt-3">{resetError}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setResetTarget(null)}
                disabled={busySlug === resetTarget.slug}
                className="px-4 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={confirmReset}
                disabled={busySlug === resetTarget.slug}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50"
              >
                초기화
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
