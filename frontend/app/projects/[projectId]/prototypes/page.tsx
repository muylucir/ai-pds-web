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
  absoluteShareUrl,
  resetPrototype,
} from "@/lib/api/prototypes";
import type { HostState, PrototypeState } from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import { knownErrorMessage } from "@/lib/api/errorMessage";
import type { Dict } from "@/lib/i18n";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useT } from "@/lib/i18n/provider";

export default function PrototypesPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const list = useAsync(() => listPrototypes(projectId), [projectId]);
  const t = useT();
  const { modelLabel, language, readOnly } = useProjectMeta(projectId);

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
  // Progress stage while hosting starts. `POST /host` responds only **after awaiting all of**
  // `npm install` → `npm run build` → the port wait (up to 60 seconds), so during that time the screen
  // had nothing but a disabled button — the stretch users read as "nothing is happening".
  //
  // The server does not need fixing: `ProtoHost.start` moves `_registry`'s state through
  // installing → building → running as it goes, and `GET /host` returns it as is. That is, we only
  // need to poll and display **state that is already observable**.
  const [hostPhase, setHostPhase] =
    useState<{ slug: string; state: HostState } | null>(null);
  // Why starting/stopping hosting was rejected. Attached to that card — a rejection usually means
  // there is something the user must do (409 `build_session_active`: the open build session must be closed),
  // and if it is not visible, it reads as the button coming back with no response.
  const [hostError, setHostError] =
    useState<{ slug: string; message: string } | null>(null);

  useEffect(() => {
    if (!resetTarget) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && busySlug !== resetTarget.slug) setResetTarget(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [resetTarget, busySlug]);

  async function handleBuild(slug: string, state: PrototypeState) {
    setBusySlug(slug);
    try {
      // **Do not auto-fire when fixing something already built.** Someone who pressed "수정하기"
      // already knows what to fix (they saw it in the preview), but auto-start first runs a round
      // trip where the agent asks back "what to improve", and while that question is up the input
      // box is locked — the user could not even type their own request.
      //
      // Instead, the user speaks first in an empty panel, and that first message becomes the opening
      // turn (the `session.opened` branch in backend routes/prototypes). The first build (`none`) and
      // failed builds still auto-fire: at that point the user has nothing to say yet, and the agent
      // must first read the spec or look at what went wrong.
      let autoStart = !(state === "built" || state === "running");
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

  function hostErrorText(err: unknown, fallback: keyof Dict): string {
    return err instanceof ApiError
      ? knownErrorMessage(t, err.detail, t(fallback)) : t(fallback);
  }

  async function handleStartHost(slug: string) {
    setBusySlug(slug);
    setHostError(null);
    // Before the server records `installing` (start calls stop first), a 404 is normal —
    // getHost folds it to null, so we leave it and wait for the next poll. A failure of the
    // polling itself does not abort hosting: this value is for display only.
    setHostPhase({ slug, state: "installing" });
    const poll = setInterval(() => {
      void getHost(projectId, slug)
        .then((st) => {
          if (st) setHostPhase({ slug, state: st.state });
        })
        .catch(() => {});
    }, 1500);
    try {
      await startHost(projectId, slug);
    } catch (err) {
      setHostError({ slug, message: hostErrorText(err, "proto.hostStartFailed") });
    } finally {
      clearInterval(poll);
      setHostPhase(null);
      setBusySlug(null);
      list.reload();
    }
  }

  async function handleStopHost(slug: string) {
    setBusySlug(slug);
    setHostError(null);
    try {
      await stopHost(projectId, slug);
    } catch (err) {
      setHostError({ slug, message: hostErrorText(err, "proto.hostStopFailed") });
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
      setResetError(t("page.resetIncomplete"));
    } finally {
      // Runs even on failure: a partial reset still deleted things, and the
      // card must reflect that — if it still reads "빌드 완료" afterwards,
      // that is the honest signal that the reset didn't finish.
      setBusySlug(null);
      list.reload();
    }
  }

  // The PM also goes in through **the same token link** as participants.
  //
  // This avoids having a "pass if logged in" branch in the backend — with two paths, the fact that
  // proto_public.py is separated by the auth boundary would blur, and the frontend proxy would have
  // to forward session cookies too, widening the allowlist we just narrowed.
  // As a bonus, the screen the PM sees is exactly the same as what participants see, so the PM
  // verifies with their own click that the link actually works.
  function handleOpenPreview(accessUrl: string) {
    window.open(accessUrl, "_blank", "noopener,noreferrer");
  }

  async function handleShowLogs(slug: string) {
    setLogsSlug(slug);
    setLogsText(null);
    setLogsError(null);
    try {
      const status = await getHost(projectId, slug);
      setLogsText(status?.log_tail ?? "");
    } catch {
      setLogsError(t("page.logsLoadFailed"));
    }
  }

  function closeBuildPanel() {
    // Returning from the panel may have changed the session state — do not leave a stale rejection reason.
    setHostError(null);
    setOpenSlug(null);
    list.reload();
  }

  return (
    <>
      <AppHeader activeTab="prototypes" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} readOnly={readOnly} />
      <main className="max-w-5xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold mb-6">{t("page.prototypesTitle")}</h1>

        {list.loading && <p className="text-sm text-slate-400">{t("page.loading")}</p>}
        {list.error && <p className="text-sm text-rose-600">{t("page.prototypeListFailed")}</p>}

        {list.data && list.data.active_builds >= list.data.max_builds && (
          <p className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {t("page.buildCapReached").replace("{max}", String(list.data.max_builds))}
          </p>
        )}

        {list.data && list.data.prototypes.length === 0 && (
          <p className="text-sm text-slate-400">{t("page.noPrototypeSpecs")}</p>
        )}

        {list.data && list.data.prototypes.length > 0 && (
          <div className="grid gap-3">
            {list.data.prototypes.map((info) => (
              <PrototypeCard
                key={info.slug}
                info={info}
                busy={busySlug === info.slug}
                readOnly={readOnly}
                onBuild={() => handleBuild(info.slug, info.state)}
                onStartHost={() => handleStartHost(info.slug)}
                startingPhase={hostPhase?.slug === info.slug ? hostPhase.state : null}
                hostError={hostError?.slug === info.slug ? hostError.message : null}
                onStopHost={() => handleStopHost(info.slug)}
                // The criterion changed from state to **the presence of access_url**. The server sends
                // this value only when running, so the condition is effectively the same, but in this
                // direction "there is a link" and "the link works" come from a single value —
                // the frontend cannot make up a nonexistent URL by looking at state.
                onOpenPreview={info.access_url
                  ? () => handleOpenPreview(info.access_url as string) : undefined}
                onShowLogs={() => handleShowLogs(info.slug)}
                onOpenSurvey={() =>
                  setSurveySlug((cur) => (cur === info.slug ? null : info.slug))
                }
                onReset={handleReset}
                archiveUrl={prototypeArchiveUrl(projectId, info.slug)}
                shareUrl={info.access_url
                  ? absoluteShareUrl(info.access_url) : undefined}
              />
            ))}
          </div>
        )}

        {surveySlug && (
          <div className="mt-8 border-t border-slate-200 pt-6">
            <SurveyPanel projectId={projectId} slug={surveySlug} readOnly={readOnly} />
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
            aria-label={`${logsSlug} ${t("page.logsSuffix")}`}
            className="bg-white rounded-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-bold">{logsSlug} {t("page.logsSuffix")}</h2>
              <button type="button" aria-label={t("page.close")} className="text-slate-400" onClick={() => setLogsSlug(null)}>
                ✕
              </button>
            </div>
            {logsError && <p className="text-sm text-rose-600">{logsError}</p>}
            {logsText !== null && (
              <pre className="text-xs bg-slate-50 border border-slate-200 rounded-lg p-3 whitespace-pre-wrap break-all">
                {logsText || t("page.noLogs")}
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
            aria-label={t("page.resetConfirmLabel")}
            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-bold text-lg">
              {t("page.resetConfirmTitle").replace("{slug}", resetTarget.slug)}
            </h2>
            {/* Reassurance sits right under the title, not at the end — it
                sets context ("this isn't total") but must not be the last
                thing read, or it blunts the irreversibility warning below. */}
            <p className="text-xs text-slate-400 mt-1">
              {t("page.resetKeepsSpec")}
            </p>
            <p className="text-sm text-slate-600 mt-3">{t("page.resetDeletesIntro")}</p>
            <ul className="text-sm text-slate-600 mt-1 list-disc list-inside space-y-0.5">
              <li>{t("page.resetItemBuild")}</li>
              <li>{t("page.resetItemChat")}</li>
              <li>{t("page.resetItemSurvey")}</li>
            </ul>
            {/* Irreversibility gets its OWN sentence, placed last, with
                nothing after it to soften it — matching ProjectList.tsx's
                weighting. It only appears when there is something
                irreversible to lose, so the 0-response case stays quiet. */}
            {resetTarget.answers !== null && resetTarget.answers > 0 && (
              <p className="text-sm font-semibold text-rose-600 mt-3">
                {t("page.resetResponsesWarn").replace("{n}", String(resetTarget.answers))}
              </p>
            )}
            {resetTarget.answers === null && (
              <p className="text-sm font-semibold text-amber-700 mt-3">
                {t("page.resetResponsesUnknown")}
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
                {t("page.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmReset}
                disabled={busySlug === resetTarget.slug}
                className="px-4 py-2 text-sm rounded-lg bg-rose-600 hover:bg-rose-700 text-white font-bold disabled:opacity-50"
              >
                {t("page.reset")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
