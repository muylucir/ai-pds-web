"use client";
import { useState } from "react";
import { getDocument, ApiError } from "@/lib/api/client";
import { useAsync } from "@/lib/useAsync";
import { MarkdownView } from "@/components/review/MarkdownView";

// Living-Document view for the right panel's "문서" tab. No part tabs (the
// mockup's Part 1/2/3/4 row is NOT ported — GET /document returns one
// markdown blob with no part segmentation; see Global Constraints). Approval
// UX (re-deferred from a structured gate card — see plan header) lives here:
// both buttons simply relay natural-language turns through the caller's
// onApprove/onRevise, which the canvas page wires to the SAME useTurnStream
// `send` the chat input uses.
export function DocumentView({
  projectId,
  onApprove,
  onRevise,
  busy,
}: {
  projectId: string;
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  const [revising, setRevising] = useState(false);
  const [text, setText] = useState("");
  const { data: markdown, loading, error } = useAsync(() => getDocument(projectId), [projectId]);

  const notFound = error instanceof ApiError && error.status === 404;
  const loadError = error !== null && !notFound;
  // The real backend's Workspace.get_document() swallows a missing-file
  // FileNotFoundError and returns {"markdown": ""} with a 200 — it never
  // 404s (see backend/pathfinder/workspace.py). So an empty string after a
  // successful load is itself the "no document yet" state, distinct from
  // the 404 branch above (kept for defensiveness / other backends). Mirrors
  // components/review/DocumentPanel.tsx's markdown.trim() === "" check.
  const empty = !loading && error === null && (markdown ?? "").trim() === "";

  function submitRevision() {
    const trimmed = text.trim();
    if (trimmed === "") return;
    onRevise(trimmed);
    setText("");
    setRevising(false);
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span aria-hidden="true">📕</span>
          <p className="font-bold text-sm">discovery-document.md</p>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-600">Living</span>
        </div>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-slate-500"
        >
          .md
        </button>
      </div>

      <div className="flex-1 overflow-y-auto chat-scroll p-5 text-sm text-slate-700">
        {loading && !markdown && <p className="text-slate-400">불러오는 중…</p>}
        {notFound && <p className="text-slate-400">문서가 아직 없습니다.</p>}
        {loadError && (
          <p className="text-rose-600">문서를 불러오지 못했습니다. 백엔드 연결을 확인하세요.</p>
        )}
        {empty && <p className="text-slate-400">아직 작성된 문서가 없습니다.</p>}
        {markdown && <MarkdownView markdown={markdown} />}
      </div>

      {/* No document yet (empty markdown) -> nothing to approve or revise;
          the action row is hidden rather than rendered-but-disabled. */}
      {!empty && (
        <div className="p-3 border-t border-slate-100 shrink-0 space-y-2">
          {revising && (
            <div className="space-y-2">
              <textarea
                aria-label="수정 요청 사항"
                rows={3}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="예: FAQ에 다국어 지원 계획 항목을 추가해줘."
                className="w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    // Abandoned draft should not resurface next time the
                    // textarea is opened.
                    setText("");
                    setRevising(false);
                  }}
                  className="px-3 py-2 text-sm rounded-lg border border-slate-300 hover:bg-slate-50"
                >
                  취소
                </button>
                <button
                  type="button"
                  disabled={busy || text.trim() === ""}
                  onClick={submitRevision}
                  className="px-3 py-2 text-sm rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50"
                >
                  수정 요청 제출
                </button>
              </div>
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => setRevising((v) => !v)}
              className="flex-1 py-2.5 rounded-lg border border-slate-300 hover:bg-slate-50 text-sm font-medium disabled:opacity-50"
            >
              ✏️ 수정 요청
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onApprove}
              className="flex-1 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-bold disabled:opacity-50"
            >
              ✓ 이 문서 승인
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
