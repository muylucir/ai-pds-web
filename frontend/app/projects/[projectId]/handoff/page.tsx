// frontend/app/projects/[projectId]/handoff/page.tsx — 인계 탭.
//
// 준비 상태 → 보완 질문 → 생성 → 미리보기·내려받기. 판정과 생성은 서버가 한다
// (backend aipds/handoff/). 생성은 서버의 백그라운드 작업이라 POST가 곧바로 돌아오고,
// 이 화면이 manifest를 폴링한다 — 세 문서를 쓰는 호출은 수 분이고 CloudFront 읽기
// 제한은 60초다.
"use client";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { GenerationProgress } from "@/components/handoff/GenerationProgress";
import { PackagePanel } from "@/components/handoff/PackagePanel";
import { ReadinessPanel } from "@/components/handoff/ReadinessPanel";
import { SupplementForm } from "@/components/handoff/SupplementForm";
import {
  downloadPackage, getPackage, getReadiness, getSupplement, putSupplement, startPackage,
} from "@/lib/api/handoff";
import type { PackageView, SupplementUpdate } from "@/lib/api/handoff";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useT } from "@/lib/i18n/provider";

type Mode = "status" | "supplement" | "package";

/** 생성 중 폴링 간격. 생성은 분 단위라 촘촘할 필요가 없다. */
const POLL_MS = 3000;

export default function HandoffPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const t = useT();
  const { modelLabel, language } = useProjectMeta(projectId);
  const readiness = useAsync(() => getReadiness(projectId), [projectId]);
  const supplement = useAsync(() => getSupplement(projectId), [projectId]);
  const [pkg, setPkg] = useState<PackageView | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const status = pkg?.manifest?.status;
  const generating = status === "generating";

  const refreshPackage = useCallback(async () => {
    try {
      setPkg(await getPackage(projectId));
    } catch {
      // 조회 실패는 다음 폴링이나 새로고침이 다시 묻는다 — 화면을 막지 않는다.
    }
  }, [projectId]);

  useEffect(() => { void refreshPackage(); }, [refreshPackage]);

  useEffect(() => {
    if (!generating) return;
    const id = setInterval(() => { void refreshPackage(); }, POLL_MS);
    return () => clearInterval(id);
  }, [generating, refreshPackage]);

  // 생성이 끝나면(성공이든 실패든) 결과 화면으로 넘어간다. 사용자가 그 사이 보완 질문을
  // 열어 두었다면 그대로 둔다 — 쓰던 답을 빼앗지 않는다.
  const previous = useRef(status);
  useEffect(() => {
    if (previous.current === "generating" && status && status !== "generating") {
      setMode((m) => (m === "supplement" ? m : null));
    }
    previous.current = status;
  }, [status]);

  // 첫 화면: 패키지가 있으면 그것을, 없으면 준비 상태를. 사용자가 고른 뒤에는 따라간다.
  const shown: Mode = mode ?? (status === "ready" ? "package" : "status");

  const generate = async (resume = false) => {
    setActionError(null);
    try {
      const manifest = await startPackage(projectId, resume);
      setPkg({ manifest, files: {}, stale: [], supplement_changed: false, resumable: false });
      setMode("status");
    } catch {
      setActionError(t("handoff.generateFailed"));
    }
  };

  const save = async (update: SupplementUpdate, thenGenerate: boolean) => {
    setBusy(true);
    setMessage(null);
    try {
      await putSupplement(projectId, update);
      supplement.reload();
      setMessage(t("handoff.supp.saved"));
      if (thenGenerate) await generate();
    } catch {
      setMessage(t("handoff.supp.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    setActionError(null);
    try {
      const blob = await downloadPackage(projectId);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${projectId}-handoff.zip`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      setActionError(t("handoff.pkg.downloadFailed"));
    }
  };

  const supplementView = supplement.data;
  const questionCount = (supplementView?.questions.length ?? 0) + (supplementView?.confirmations.length ?? 0);

  return (
    <>
      <AppHeader activeTab="handoff" projectId={projectId} modelLabel={modelLabel} projectLanguage={language} />
      <main className="max-w-[1720px] mx-auto px-6 py-8">
        <div className="mb-[18px]">
          <h2 className="text-[22px] font-bold text-slate-900">
            {shown === "supplement" ? `${t("handoff.title")} · ${t("handoff.supp.title")}` : t("handoff.title")}
          </h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            {shown === "supplement" ? t("handoff.supp.lead") : t("handoff.lead")}
          </p>
        </div>

        {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}

        {/* 생성 중이거나 실패·중단됐으면 단계별 진행을 먼저 보인다. 보완 질문을 쓰는 중에는 가리지 않는다. */}
        {pkg?.manifest && status !== "ready" && shown !== "supplement" && (
          <GenerationProgress pkg={pkg} busy={busy || generating}
                              onResume={() => void generate(true)} onRestart={() => void generate(false)} />
        )}

        {readiness.error ? (
          <p className="text-sm text-rose-600">{t("handoff.loadFailed")}</p>
        ) : !readiness.data ? null : shown === "supplement" && supplementView ? (
          <SupplementForm
            key={JSON.stringify(supplementView)}
            view={supplementView}
            workspaceHref={`/projects/${projectId}/workspace`}
            busy={busy}
            message={message}
            onSave={(u) => void save(u, false)}
            onSaveAndGenerate={(u) => void save(u, true)}
            onBack={() => setMode("status")}
          />
        ) : shown === "package" && pkg?.manifest ? (
          <PackagePanel
            pkg={pkg}
            generating={generating}
            onRegenerate={() => void generate()}
            onDownload={() => void download()}
            onSupplement={() => setMode("supplement")}
            onStatus={() => setMode("status")}
          />
        ) : (
          <ReadinessPanel
            readiness={readiness.data}
            questionCount={questionCount}
            generating={generating}
            hasPackage={status === "ready"}
            onSupplement={() => setMode("supplement")}
            onGenerate={() => void generate()}
            onViewPackage={() => setMode("package")}
          />
        )}
      </main>
    </>
  );
}
