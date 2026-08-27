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

  // **표면은 승인 후 배너(review/page.tsx)와 같은 무게다** — 같은 rx, 같은
  // padding, 톤만 다르다(미승인 amber / 승인 emerald). 종전에는 보라
  // 그라데이션 + shadow-lg + 🚦로 앱에서 가장 큰 요소였는데, 이 배너는
  // discovery-document.md를 열고 승인 전이면 항상 뜬다. 문서는 Envision이
  // 만들므로(상류 envision.md:255) 워크숍 내내 켜져 있고, 그 크기로 상주하면
  // 사람은 그것을 무시하도록 학습한다 — 게이트가 원하는 것의 반대다.
  //
  // `role="alert"`도 같은 이유로 걷어냈다. alert는 스크린리더가 진행 중인
  // 낭독을 끊고 끼어드는 라이브 리전이다. 이것은 갑자기 발생한 사건이 아니라
  // 페이지에 상주하는 영역이므로, 이름을 가진 region이 맞다(승인 후 배너가
  // 이미 role="status"인 것과 짝이 맞는다).
  return (
    <section
      aria-labelledby="approval-gate-heading"
      className="rounded-xl border border-amber-200 bg-amber-50 p-4 mb-6 flex flex-col lg:flex-row lg:items-center justify-between gap-4"
    >
      <div>
        <div className="flex items-center gap-2">
          <h1 id="approval-gate-heading" className="text-sm font-bold text-amber-900">
            {t("review.gateTitle")}
          </h1>
          <span className="text-xs px-2.5 py-0.5 rounded-full border border-amber-300 bg-amber-100 text-amber-800">
            {t("review.badgeDraft")}
          </span>
        </div>
        <p className="text-sm text-amber-900 mt-1">
          {t("review.gateIntro")}{" "}
          <b className="font-semibold">{t("review.gateBodyApprove")}</b>{" "}
          {t("review.gateBodyPart1")}{" "}
          <b className="font-semibold">{t("review.gateBodyRevise")}</b>{" "}
          {t("review.gateBodyPart2")}
        </p>
      </div>
      {/* 액센트는 승인 버튼 하나다 — 앱의 기본 버튼(bg-violet-600)과 같은 것을
          쓴다. 배너가 조용해진 만큼, 화면에서 눌러야 하는 것이 무엇인지는
          이 버튼이 혼자 말한다. */}
      <div className="flex items-center gap-3 shrink-0">
        <Link
          href={reviseHref}
          className="px-4 py-2 rounded-lg border border-amber-300 bg-white text-amber-900 text-sm font-medium hover:bg-amber-100"
        >
          {t("review.gateReviseBtn")}
        </Link>
        <button
          className="px-5 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-bold"
          disabled={busy}
          onClick={onApprove}
        >
          {t("review.gateApproveBtn")}
        </button>
      </div>
    </section>
  );
}
