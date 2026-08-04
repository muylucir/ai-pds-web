"use client";
import Link from "next/link";
import { useT } from "@/lib/i18n/provider";

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
  const t = useT();
  // 이 게이트는 미승인 상태에서만 렌더된다(승인되면 페이지가 완료 배너로
  // 대체한다). 그래서 상태 배지는 항상 "초안 검토 중" 하나뿐이다 — 예전
  // stageStatus prop은 존재하지 않는 "Discovery Document" 스테이지를 읽어
  // 늘 null이었다.
  const badge = { label: t("review.badgeDraft"), cls: "bg-amber-400/20 border-amber-200/50 text-amber-50" };

  return (
    <div
      role="alert"
      className="rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 text-white p-6 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4 shadow-lg shadow-violet-200"
    >
      <div className="flex gap-4">
        <span className="text-3xl shrink-0" aria-hidden="true">🚦</span>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold">{t("review.gateTitle")}</h1>
            <span className={`text-xs px-2.5 py-0.5 rounded-full border ${badge.cls}`}>
              {badge.label}
            </span>
          </div>
          <p className="text-violet-100 text-sm mt-1">
            {t("review.gateIntro")}{" "}
            <b className="text-white">{t("review.gateBodyApprove")}</b>{" "}
            {t("review.gateBodyPart1")}{" "}
            <b className="text-white">{t("review.gateBodyRevise")}</b>{" "}
            {t("review.gateBodyPart2")}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <Link
          href={reviseHref}
          className="px-4 py-2.5 rounded-lg bg-white/15 hover:bg-white/25 border border-white/30 text-sm font-medium"
        >
          {t("review.gateReviseBtn")}
        </Link>
        <button
          className="px-6 py-2.5 rounded-lg bg-white text-violet-700 text-sm font-bold hover:bg-violet-50 disabled:opacity-50"
          disabled={busy}
          onClick={onApprove}
        >
          {t("review.gateApproveBtn")}
        </button>
      </div>
    </div>
  );
}
