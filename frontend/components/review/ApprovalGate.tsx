"use client";
import Link from "next/link";

export function ApprovalGate({
  onApprove,
  busy,
  reviseHref,
}: {
  onApprove: () => void;
  busy: boolean;
  // 수정 요청 링크의 목적지 — 워크스페이스 채팅으로 이동하며 초안 텍스트를 ?draft=로 전달한다.
  reviseHref: string;
}) {
  // 이 게이트는 미승인 상태에서만 렌더된다(승인되면 페이지가 완료 배너로
  // 대체한다). 그래서 상태 배지는 항상 "초안 검토 중" 하나뿐이다 — 예전
  // stageStatus prop은 존재하지 않는 "Discovery Document" 스테이지를 읽어
  // 늘 null이었다.
  const badge = { label: "초안 검토 중", cls: "bg-amber-400/20 border-amber-200/50 text-amber-50" };

  return (
    <div
      role="alert"
      className="rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 text-white p-6 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4 shadow-lg shadow-violet-200"
    >
      <div className="flex gap-4">
        <span className="text-3xl shrink-0" aria-hidden="true">🚦</span>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold">승인 게이트</h1>
            <span className={`text-xs px-2.5 py-0.5 rounded-full border ${badge.cls}`}>
              {badge.label}
            </span>
          </div>
          <p className="text-violet-100 text-sm mt-1">
            AI가 작성한 Discovery Document의 최종 확정 단계입니다.{" "}
            <b className="text-white">승인</b>하면 이 문서로 Discovery 단계를 완료하고 개발
            핸드오프 준비로 넘어갑니다. <b className="text-white">수정 요청</b>은 워크스페이스
            채팅으로 이동해 AI와 대화로 진행합니다 — 초안이 입력창에 채워집니다. 두 행동 모두
            감사 로그에 기록됩니다.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <Link
          href={reviseHref}
          className="px-4 py-2.5 rounded-lg bg-white/15 hover:bg-white/25 border border-white/30 text-sm font-medium"
        >
          ✏️ 수정 요청
        </Link>
        <button
          className="px-6 py-2.5 rounded-lg bg-white text-violet-700 text-sm font-bold hover:bg-violet-50 disabled:opacity-50"
          disabled={busy}
          onClick={onApprove}
        >
          ✓ 승인하고 다음 단계로
        </button>
      </div>
    </div>
  );
}
