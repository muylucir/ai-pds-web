"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppHeader } from "@/components/AppHeader";
import { DocTree } from "@/components/review/DocTree";
import { DocumentPanel } from "@/components/review/DocumentPanel";
import { ApprovalGate } from "@/components/review/ApprovalGate";
import { VerificationSummary } from "@/components/review/VerificationSummary";
import { Markdown } from "@/components/Markdown";
import {
  listArtifacts,
  readArtifact,
  getAudit,
  listApprovals,
  approveDocument,
  downloadArtifactsArchive,
  getMissingReferences,
  ApiError,
} from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { useProjectMeta } from "@/lib/useProjectModel";
import { deriveApprovalState } from "@/lib/approvalState";
import { useT } from "@/lib/i18n/provider";

// Client-side Blob download — saves the currently loaded markdown without a
// backend round trip.
function downloadMarkdown(path: string, content: string) {
  const name = path.slice(path.lastIndexOf("/") + 1) || "document.md";
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// Zip download of all artifacts (aiplc-docs/**) — needs a backend round trip
// (unlike an individual .md, the server assembles the archive).
async function downloadZip(projectId: string) {
  const blob = await downloadArtifactsArchive(projectId);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${projectId}-artifacts.zip`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ReviewPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const t = useT();
  const router = useRouter();
  const { modelLabel, language, readOnly } = useProjectMeta(projectId);

  const tree = useAsync(() => listArtifacts(projectId), [projectId]);
  const audit = useAsync(() => getAudit(projectId), [projectId]);
  // The primary basis for the approval decision is the **approval record** (GET
  // /approvals). Audit log parsing is used only as a fallback for existing
  // projects without records — the reason is in the lib/approvalState.ts header
  // (in short: reconstructing the decision from prose the agent transcribed loses
  // approvals whenever the wording drifts; 3 of 5 measured cases failed).
  //
  // The reason for not looking at aiplc-state.md still stands: it has no
  // "Discovery Document" stage (neither the rules nor the agent use such a
  // stage), so the old lookup was always undefined.
  const approvals = useAsync(() => listApprovals(projectId), [projectId]);
  const approval = deriveApprovalState(audit.data ?? [], approvals.data ?? undefined);

  // Default selection: once the artifact tree loads, select
  // discovery-document.md if present. Guarded by `selected === null` so a
  // later tree reload (e.g. after an approval turn) never clobbers the
  // user's own choice.
  useEffect(() => {
    if (selected !== null || !tree.data) return;
    const doc = tree.data.find((p) => p.endsWith("discovery-document.md"));
    if (doc) setSelected(doc);
  }, [tree.data, selected]);

  // A 404 file is treated as an empty document, not an error; any other
  // error must still surface (not be coalesced into the empty-doc state).
  const content = useAsync(
    () =>
      selected === null
        ? Promise.resolve("")
        : readArtifact(projectId, selected).catch((e) =>
            e instanceof ApiError && e.status === 404 ? "" : Promise.reject(e),
          ),
    [projectId, selected],
  );

  const contentLoadError = selected !== null && content.error !== null;
  const auditPath = tree.data?.find((p) => p.endsWith("aiplc-docs/audit.md")) ?? null;
  const isDiscoveryDocument = selected?.endsWith("discovery-document.md") ?? false;

  // Files that the entry document handed to the development org cites as evidence
  // but that do not exist. Surfaced right before the full download — the upstream
  // template has the reference written whether or not the file exists, so if no
  // one checks, it is handed over with broken links (backend
  // parsers/doc_references). Re-queried only when the list changes: tree.data is
  // a new array on every 5-second poll.
  const artifactsKey = (tree.data ?? []).join("\n");
  const missingRefs = useAsync(
    () => isDiscoveryDocument ? getMissingReferences(projectId) : Promise.resolve(null),
    [projectId, isDiscoveryDocument, artifactsKey, content.data],
  );
  const missing = missingRefs.data?.missing ?? [];

  // Destination of the change-request link — navigates to the workspace chat and
  // passes a draft containing the document name via ?draft=.
  const docName = selected ? selected.slice(selected.lastIndexOf("/") + 1) : "discovery-document.md";
  const reviseHref = `/projects/${projectId}/workspace?draft=${encodeURIComponent(`${docName} ${t("page.reviseDraftSuffix")}`)}`;

  /** Approve button. POST /approve writes the record first and then **starts**
   *  the agent turn.
   *
   *  Not sending "승인" (approve) as a chat turn is the core of this feature: on
   *  that path the only record of the approval was audit.md, written by the
   *  agent, and when the agent transcribed the wording differently, the fact that
   *  the user clicked was lost (lib/approvalState.ts header).
   *
   *  The approval request does not wait for the turn. The turn that moves to the
   *  next stage takes minutes, so we move straight to the workspace, where it is
   *  watched — it attaches to the turn that runs as the workspace opens
   *  (`GET /turn` in useWorkspaceStream). If the AI is still busy with other work, the
   *  server rejects with 409 before writing the record.
   */
  async function approve() {
    setBusy(true);
    setActionError(null);
    try {
      await approveDocument(projectId);
      router.push(`/projects/${encodeURIComponent(projectId)}/workspace`);
    } catch (e) {
      setActionError(t(e instanceof ApiError && e.status === 409
        && e.detail === "turn_in_progress" ? "page.approveBusy" : "page.turnFailed"));
      setBusy(false);
    }
  }

  // Auto-refresh the artifact list (spec: new documents should appear without
  // a manual reload). This route has no SSE stream — documents can be created
  // from the workspace chat or an async turn — so a gentle poll is the only
  // cross-route signal available. `tree.reload()` preserves the user's current
  // selection (the default-selection effect is guarded on `selected === null`),
  // and DocTree doesn't gate on `tree.loading`, so a reload never flashes the
  // pane empty. Paused while a turn is in flight — sendTurn reloads the tree
  // itself on completion, so polling during `busy` would only add races.
  //
  // Also paused while the tab is hidden: a review tab left open behind the
  // workspace would otherwise list the bucket every 5s for nobody. Coming back
  // reloads once immediately, so the list is never a full interval stale.
  useEffect(() => {
    if (busy) return;
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (id === null) id = setInterval(() => tree.reload(), 5000);
    };
    const stop = () => {
      if (id !== null) clearInterval(id);
      id = null;
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        tree.reload();
        start();
      }
    };
    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [busy, tree.reload]);

  return (
    <>
      <AppHeader activeTab="review" projectId={projectId} modelLabel={modelLabel}
                 projectLanguage={language} readOnly={readOnly} />
      <main className="max-w-[1720px] mx-auto px-6 py-8">
        {/* Viewer members do not get the approval gate — approval and change
            requests both start a turn. */}
        {isDiscoveryDocument && !contentLoadError && !approval.approved && !readOnly && (
          <>
            <ApprovalGate
              // Send the approval word in the project's language — this text goes
              // to the agent and remains in the transcript, so it must be in the
              // language of the conversation. When language is null (older
              // backend, lookup failure), it falls back to ko — that is the
              // language of every project before this feature.
              onApprove={approve}
              busy={busy}
              reviseHref={reviseHref}
            />
            {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}
            {busy && <p className="text-sm text-slate-400 mb-4">{t("page.aiWorking")}</p>}
          </>
        )}

        {/* After approval, remove the gate but keep the state and the way back:
            making a change request invalidates the approval and the gate
            reappears. */}
        {isDiscoveryDocument && !contentLoadError && approval.approved && (
          <div
            role="status"
            className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
          >
            <p className="text-sm text-emerald-800">
              <span className="font-bold">{t("page.approvedBanner")}</span>{" "}
              {t("page.approvedBannerBody")}
            </p>
            {!readOnly && (
              <Link
                href={reviseHref}
                className="shrink-0 px-4 py-2 rounded-lg border border-emerald-300 bg-white text-emerald-800 text-sm font-medium hover:bg-emerald-100"
              >
                {t("page.requestRevision")}
              </Link>
            )}
          </div>
        )}

        {/* Width: at max width (1720px), file list ≈330 · body ≈1015 · validation
            summary ≈275px. Minimum widths are set on the ratios so that on narrow
            lg screens they do not drop below 240px and 220px. */}
        <div className="grid lg:grid-cols-[minmax(240px,20%)_1fr] gap-6">
          <aside className="bg-white rounded-xl border border-slate-200 p-4">
            <DocTree paths={tree.data ?? []} selected={selected} onSelect={setSelected} />
          </aside>

          {contentLoadError ? (
            <div className="bg-white rounded-xl border border-slate-200 p-6">
              <p className="text-sm text-rose-600">{t("page.docLoadFailed")}</p>
            </div>
          ) : selected === null ? (
            <div className="bg-white rounded-xl border border-slate-200 p-6">
              <p className="text-sm text-slate-400">{t("page.pickDocument")}</p>
            </div>
          ) : (
            <div>
              {isDiscoveryDocument && missing.length > 0 && (
                <div role="alert"
                     className="rounded-xl border border-amber-200 bg-amber-50 p-4 mb-3 text-sm text-amber-900">
                  <p className="font-bold">{t("page.missingRefsTitle").replace("{n}", String(missing.length))}</p>
                  <ul className="list-disc pl-5 my-2">
                    {missing.map((ref) => <li key={ref}><code>{ref}</code></li>)}
                  </ul>
                  <p>{t("page.missingRefsBody")}</p>
                </div>
              )}
              <div className="flex justify-end mb-3">
                <button
                  type="button"
                  onClick={() => downloadZip(projectId).catch(() => setActionError(t("page.zipDownloadFailed")))}
                  className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 mr-2"
                >
                  {t("page.downloadAllZip")}
                </button>
                <button
                  type="button"
                  onClick={() => downloadMarkdown(selected, content.data ?? "")}
                  disabled={content.loading}
                  className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                >
                  {t("page.downloadMd")}
                </button>
              </div>
              {isDiscoveryDocument ? (
                <div className="grid lg:grid-cols-[1fr_minmax(220px,21%)] gap-6">
                  <DocumentPanel markdown={content.data ?? ""} />
                  <VerificationSummary
                    entries={audit.data ?? []}
                    onViewRaw={auditPath ? () => setSelected(auditPath) : undefined}
                  />
                </div>
              ) : (
                <article className="bg-white rounded-xl border border-slate-200 p-6">
                  <Markdown text={content.data ?? ""} />
                </article>
              )}
            </div>
          )}
        </div>
      </main>
    </>
  );
}
