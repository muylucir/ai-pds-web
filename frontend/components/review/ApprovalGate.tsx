"use client";
import { useState } from "react";

export function ApprovalGate({
  onApprove,
  onRevise,
  busy,
}: {
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");

  return (
    <>
      <div
        role="alert"
        className="rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 text-white p-6 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4 shadow-lg shadow-violet-200"
      >
        <div className="flex gap-4">
          <span className="text-3xl shrink-0" aria-hidden="true">🚦</span>
          <div>
            <h1 className="text-lg font-bold">승인 게이트</h1>
            <p className="text-violet-100 text-sm mt-1">
              AI가 Discovery Document를 작성했습니다. 검토 후 승인해야 다음 단계로 진행됩니다.
              승인·수정요청은 모두 감사 로그에 기록됩니다.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <button
            className="px-4 py-2.5 rounded-lg bg-white/15 hover:bg-white/25 border border-white/30 text-sm font-medium disabled:opacity-50"
            disabled={busy}
            onClick={() => setOpen((v) => !v)}
          >
            ✏️ 수정 요청
          </button>
          <button
            className="px-6 py-2.5 rounded-lg bg-white text-violet-700 text-sm font-bold hover:bg-violet-50 disabled:opacity-50"
            disabled={busy}
            onClick={onApprove}
          >
            ✓ 승인하고 다음 단계로
          </button>
        </div>
      </div>

      {open && (
        <div className="bg-white border border-violet-200 rounded-xl p-5 mb-6">
          <label htmlFor="revision-input" className="font-medium text-sm">
            수정 요청 사항{" "}
            <span className="text-slate-400 font-normal">— 자연어로 설명하면 AI가 문서를 수정한 뒤 다시 게이트로 돌아옵니다</span>
          </label>
          <textarea
            id="revision-input"
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="예: FAQ에 다국어 지원 계획 항목을 추가해줘."
            className="mt-2 w-full text-sm rounded-lg border border-slate-200 p-3 focus:outline-none focus:ring-2 focus:ring-violet-400"
          />
          <div className="mt-3 flex justify-end gap-2">
            <button className="px-4 py-2 text-sm rounded-lg border border-slate-300 hover:bg-slate-50" onClick={() => setOpen(false)}>
              취소
            </button>
            <button
              className="px-4 py-2 text-sm rounded-lg bg-violet-600 text-white font-medium hover:bg-violet-700 disabled:opacity-50"
              disabled={busy || text.trim() === ""}
              onClick={() => onRevise(text)}
            >
              수정 요청 제출
            </button>
          </div>
        </div>
      )}
    </>
  );
}
