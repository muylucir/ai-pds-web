"use client";
import { use, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { DocumentPanel } from "@/components/review/DocumentPanel";
import { ApprovalGate } from "@/components/review/ApprovalGate";
import { VerificationSummary } from "@/components/review/VerificationSummary";
import { getDocument, getAudit, postMessage, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

export default function ReviewPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // A 404 document is treated as an empty document, not an error.
  const doc = useAsync(
    () => getDocument(projectId).catch((e) => (e instanceof ApiError && e.status === 404 ? "" : Promise.reject(e))),
    [projectId],
  );
  const audit = useAsync(() => getAudit(projectId), [projectId]);

  async function sendTurn(text: string) {
    setBusy(true);
    setActionError(null);
    try {
      await postMessage(projectId, text);
      doc.reload();
      audit.reload();
    } catch {
      setActionError("요청 처리에 실패했습니다. 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <AppHeader activeTab="review" projectId={projectId} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        <ApprovalGate onApprove={() => sendTurn("승인")} onRevise={(t) => sendTurn(t)} busy={busy} />
        {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}
        {busy && <p className="text-sm text-slate-400 mb-4">AI가 요청을 처리하고 있습니다…</p>}

        <div className="grid lg:grid-cols-3 gap-6">
          <DocumentPanel markdown={doc.data ?? ""} />
          <VerificationSummary entries={audit.data ?? []} />
        </div>
      </main>
    </>
  );
}
