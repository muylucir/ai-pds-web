// frontend/app/projects/[projectId]/handoff/page.tsx — the handoff tab.
//
// Readiness → supplementary questions → generation → preview/download. The server does the judgment and
// generation (backend aipds/handoff/). Generation is a server background job, so the POST returns right away
// and this screen polls the manifest — the calls that write the three documents take minutes, and CloudFront's
// read limit is 60 seconds.
"use client";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { GenerationProgress } from "@/components/handoff/GenerationProgress";
import { PackagePanel } from "@/components/handoff/PackagePanel";
import { ReadinessPanel } from "@/components/handoff/ReadinessPanel";
import { ResetDialog, savedAnswers } from "@/components/handoff/ResetDialog";
import { SupplementForm } from "@/components/handoff/SupplementForm";
import {
  archiveFolder, downloadPackage, getPackage, getReadiness, getSupplement, putSupplement, resetHandoff,
  reviseAnswer, startPackage,
} from "@/lib/api/handoff";
import type { Confirmation, PackageView, SupplementUpdate } from "@/lib/api/handoff";
import { ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { useT } from "@/lib/i18n/provider";

type Mode = "status" | "supplement" | "package";

/** Polling interval during generation. Generation takes minutes, so it does not need to be tight. */
const POLL_MS = 3000;

export default function HandoffPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const t = useT();
  const router = useRouter();
  const { modelLabel, language, readOnly } = useProjectMeta(projectId);
  const readiness = useAsync(() => getReadiness(projectId), [projectId]);
  const supplement = useAsync(() => getSupplement(projectId), [projectId]);
  const [pkg, setPkg] = useState<PackageView | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const status = pkg?.manifest?.status;
  const generating = status === "generating";

  const refreshPackage = useCallback(async () => {
    try {
      setPkg(await getPackage(projectId));
    } catch {
      // A failed query gets asked again by the next poll or a refresh — it does not block the screen.
    }
  }, [projectId]);

  useEffect(() => { void refreshPackage(); }, [refreshPackage]);

  useEffect(() => {
    if (!generating) return;
    const id = setInterval(() => { void refreshPackage(); }, POLL_MS);
    return () => clearInterval(id);
  }, [generating, refreshPackage]);

  // When generation finishes (success or failure), move to the result screen. If the user opened the
  // supplementary questions in the meantime, leave them be — do not take away the answer they were writing.
  const previous = useRef(status);
  useEffect(() => {
    if (previous.current === "generating" && status && status !== "generating") {
      setMode((m) => (m === "supplement" ? m : null));
    }
    previous.current = status;
  }, [status]);

  // Initial screen: the package if there is one, otherwise readiness. After the user chooses, follow their choice.
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

  // Changing a settled answer: the server writes it and starts the turn that carries the change through the documents. The turn
  // takes minutes, so we move straight to the workspace, which attaches to it (same as approval on the review screen).
  const revise = async (c: Confirmation, answer: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await reviseAnswer(projectId, c.file, c.number, answer);
      router.push(`/projects/${encodeURIComponent(projectId)}/workspace`);
    } catch (e) {
      setMessage(t(e instanceof ApiError && e.status === 409 && e.detail === "turn_in_progress"
        ? "handoff.supp.confirm.reviseBusy" : "handoff.supp.confirm.reviseFailed"));
      setBusy(false);
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
      a.download = `${archiveFolder(projectId)}.zip`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      setActionError(t("handoff.pkg.downloadFailed"));
    }
  };

  // Unlike regenerate, this also clears the previous edition to carry over and the supplementary answers (server handoff/package.reset).
  const reset = async () => {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetHandoff(projectId);
      setResetOpen(false);
      setPkg(null);
      setMode(null);
      setMessage(null);
      supplement.reload();
      await refreshPackage();
    } catch (e) {
      setResetError(e instanceof ApiError && e.status === 409 ? t("handoff.reset.busy") : t("handoff.reset.failed"));
    } finally {
      setResetBusy(false);
    }
  };

  const supplementView = supplement.data;
  const answers = savedAnswers(supplementView);
  const resettable = !!pkg?.manifest || answers > 0;
  const questionCount = (supplementView?.questions.length ?? 0) + (supplementView?.confirmations.length ?? 0)
    + (supplementView?.open_questions.length ?? 0);
  const openDecisions = supplementView?.has_package
    ? supplementView.open_questions.filter((q) => !q.answer).length : null;

  return (
    <>
      <AppHeader activeTab="handoff" projectId={projectId} modelLabel={modelLabel} projectLanguage={language} readOnly={readOnly} />
      <main className="max-w-[1720px] mx-auto px-6 py-8">
        <div className="mb-[18px] flex items-start gap-4">
          <div className="flex-1">
            <h2 className="text-[22px] font-bold text-slate-900">
              {shown === "supplement" ? `${t("handoff.title")} · ${t("handoff.supp.title")}` : t("handoff.title")}
            </h2>
            <p className="mt-0.5 text-[13px] text-slate-500">
              {shown === "supplement" ? t("handoff.supp.lead") : t("handoff.lead")}
            </p>
          </div>
          {resettable && shown !== "supplement" && !readOnly && (
            <button type="button" disabled={generating}
                    title={generating ? t("handoff.reset.busy") : undefined}
                    onClick={() => { setResetError(null); setResetOpen(true); }}
                    className="px-3 py-1.5 rounded-lg border border-rose-200 text-[13px] text-rose-700 hover:bg-rose-50 disabled:opacity-45">
              {t("handoff.reset.open")}
            </button>
          )}
        </div>

        {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}

        {/* If generating, or failed/interrupted, show per-stage progress first. Do not cover it while supplementary questions are being written. */}
        {pkg?.manifest && status !== "ready" && shown !== "supplement" && (
          <GenerationProgress pkg={pkg} busy={busy || generating}
                              onResume={() => void generate(true)} onRestart={() => void generate(false)}
                              readOnly={readOnly} />
        )}

        {readiness.error ? (
          <p className="text-sm text-rose-600">{t("handoff.loadFailed")}</p>
        ) : !readiness.data ? null : shown === "supplement" && supplementView ? (
          <SupplementForm
            key={JSON.stringify(supplementView)}
            view={supplementView}
            busy={busy}
            message={message}
            onSave={(u) => void save(u, false)}
            onSaveAndGenerate={(u) => void save(u, true)}
            onBack={() => setMode("status")}
            onRevise={(c, answer) => void revise(c, answer)}
            readOnly={readOnly}
          />
        ) : shown === "package" && pkg?.manifest ? (
          <PackagePanel
            pkg={pkg}
            generating={generating}
            onRegenerate={() => void generate()}
            onDownload={() => void download()}
            onSupplement={() => setMode("supplement")}
            onStatus={() => setMode("status")}
            readOnly={readOnly}
          />
        ) : (
          <ReadinessPanel
            readiness={readiness.data}
            questionCount={questionCount}
            openDecisions={openDecisions}
            generating={generating}
            hasPackage={status === "ready"}
            onSupplement={() => setMode("supplement")}
            onGenerate={() => void generate()}
            onViewPackage={() => setMode("package")}
            onReconcile={(draft) =>
              router.push(`/projects/${projectId}/workspace?draft=${encodeURIComponent(draft)}`)}
            readOnly={readOnly}
          />
        )}
      </main>
      {resetOpen && (
        <ResetDialog answers={answers} busy={resetBusy} error={resetError}
                     onCancel={() => setResetOpen(false)} onConfirm={() => void reset()} />
      )}
    </>
  );
}
