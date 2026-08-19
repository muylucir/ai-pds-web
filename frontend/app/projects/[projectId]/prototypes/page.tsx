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
import type { HostState } from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useT } from "@/lib/i18n/provider";

export default function PrototypesPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const list = useAsync(() => listPrototypes(projectId), [projectId]);
  const t = useT();
  const { modelLabel, language } = useProjectMeta(projectId);

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
  // 호스팅 시작 중의 진행 단계. `POST /host`가 `npm install` → `npm run build` →
  // 포트 대기(최대 60초)를 **전부 await한 뒤** 응답하므로, 그동안 화면에는 비활성
  // 버튼밖에 없었다 — 사용자가 "아무 반응이 없다"고 읽는 구간이다.
  //
  // 서버를 고칠 필요가 없다: `ProtoHost.start`가 진행하며 `_registry`의 state를
  // installing → building → running으로 바꾸고 `GET /host`가 그것을 그대로
  // 돌려준다. 즉 **이미 관측 가능한 상태**를 폴링해서 보여주기만 하면 된다.
  const [hostPhase, setHostPhase] =
    useState<{ slug: string; state: HostState } | null>(null);

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
    // 서버가 `installing`을 기록하기 전(start가 먼저 stop을 호출한다)에는 404가
    // 정상이다 — getHost가 그것을 null로 접어 주므로 그대로 두고 다음 폴링을
    // 기다린다. 폴링 자체의 실패로 호스팅을 중단시키지 않는다: 이 값은 표시용이다.
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
    } finally {
      clearInterval(poll);
      setHostPhase(null);
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
      setResetError(t("page.resetIncomplete"));
    } finally {
      // Runs even on failure: a partial reset still deleted things, and the
      // card must reflect that — if it still reads "빌드 완료" afterwards,
      // that is the honest signal that the reset didn't finish.
      setBusySlug(null);
      list.reload();
    }
  }

  // PM도 참가자와 **같은 토큰 링크**로 들어간다.
  //
  // 백엔드에 "로그인했으면 통과" 분기를 두지 않기 위해서다 — 경로가 둘이면
  // proto_public.py가 인증 경계로 분리되어 있다는 사실이 흐려지고, 프론트
  // 프록시도 세션 쿠키까지 forward해야 해서 방금 좁힌 허용목록이 다시 넓어진다.
  // 덤으로 PM이 보는 화면이 참가자가 보는 화면과 정확히 같아지므로, 링크가
  // 실제로 동작하는지를 PM이 자기 클릭으로 검증하게 된다.
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
    setOpenSlug(null);
    list.reload();
  }

  return (
    <>
      <AppHeader activeTab="prototypes" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} />
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
                onBuild={() => handleBuild(info.slug)}
                onStartHost={() => handleStartHost(info.slug)}
                startingPhase={hostPhase?.slug === info.slug ? hostPhase.state : null}
                onStopHost={() => handleStopHost(info.slug)}
                // 판정 기준이 state에서 **access_url의 존재**로 바뀌었다. 서버가
                // running일 때만 이 값을 실어 보내므로 조건은 사실상 같지만, 이
                // 방향이면 "링크가 있다"와 "링크가 동작한다"가 한 값에서 나온다 —
                // 프론트가 state를 보고 없는 URL을 만들어 낼 수 없다.
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
