"use client";
import { use, useEffect, useState } from "react";
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
  getState,
  postMessage,
  downloadArtifactsArchive,
  ApiError,
} from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";

// 클라이언트 Blob 다운로드 — 백엔드 왕복 없이 현재 로드된 마크다운을 저장한다.
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

// 산출물 전체(aiplc-docs/**) zip 다운로드 — 백엔드 왕복 필요(개별 .md와 달리
// 서버가 아카이브를 구성한다).
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

  const tree = useAsync(() => listArtifacts(projectId), [projectId]);
  const audit = useAsync(() => getAudit(projectId), [projectId]);
  // Discovery Document 스테이지 상태 → 게이트 배지(초안 검토 중 / 승인 완료).
  // state 로드 실패는 배지 미표시로 강등(게이트 동작 자체는 영향 없음).
  const state = useAsync(() => getState(projectId).catch(() => null), [projectId]);
  const docStage = state.data?.stages.find((s) => s.name === "Discovery Document");

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
  const isDiscoveryDocument = selected?.endsWith("discovery-document.md") ?? false;

  async function sendTurn(text: string) {
    setBusy(true);
    setActionError(null);
    try {
      await postMessage(projectId, text);
      // tree too — a revision turn may CREATE a new document (e.g. a fresh
      // FAQ/PR file), which must appear in the tree, not just refresh the
      // currently-open file's content.
      tree.reload();
      content.reload();
      audit.reload();
    } catch {
      setActionError("요청 처리에 실패했습니다. 다시 시도해 주세요.");
    } finally {
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
  useEffect(() => {
    if (busy) return;
    const id = setInterval(() => tree.reload(), 5000);
    return () => clearInterval(id);
  }, [busy, tree.reload]);

  return (
    <>
      <AppHeader activeTab="review" projectId={projectId} />
      <main className="max-w-[1720px] mx-auto px-6 py-8">
        {isDiscoveryDocument && !contentLoadError && (
          <>
            <ApprovalGate
              onApprove={() => sendTurn("승인")}
              onRevise={(t) => sendTurn(t)}
              busy={busy}
              stageStatus={docStage?.status ?? null}
            />
            {actionError && <p className="text-sm text-rose-600 mb-4">{actionError}</p>}
            {busy && <p className="text-sm text-slate-400 mb-4">AI가 요청을 처리하고 있습니다…</p>}
          </>
        )}

        <div className="grid lg:grid-cols-[240px_1fr] gap-6">
          <aside className="bg-white rounded-xl border border-slate-200 p-4">
            <DocTree paths={tree.data ?? []} selected={selected} onSelect={setSelected} />
          </aside>

          {contentLoadError ? (
            <div className="bg-white rounded-xl border border-slate-200 p-6">
              <p className="text-sm text-rose-600">문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
            </div>
          ) : selected === null ? (
            <div className="bg-white rounded-xl border border-slate-200 p-6">
              <p className="text-sm text-slate-400">좌측에서 문서를 선택하세요.</p>
            </div>
          ) : (
            <div>
              <div className="flex justify-end mb-3">
                <button
                  type="button"
                  onClick={() => downloadZip(projectId).catch(() => setActionError("압축 다운로드에 실패했습니다."))}
                  className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 mr-2"
                >
                  ⬇ 전체 다운로드 (.zip)
                </button>
                <button
                  type="button"
                  onClick={() => downloadMarkdown(selected, content.data ?? "")}
                  disabled={content.loading}
                  className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                >
                  ⬇ .md 다운로드
                </button>
              </div>
              {isDiscoveryDocument ? (
                <div className="grid lg:grid-cols-3 gap-6">
                  <DocumentPanel markdown={content.data ?? ""} />
                  <VerificationSummary entries={audit.data ?? []} />
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
