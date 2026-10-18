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
  // Destination of the change-request link — navigates to the workspace chat, passing the draft text via ?draft=.
  reviseHref: string;
}) {
  const t = useT();
  // This gate renders only in the unapproved state (once approved, the page replaces it with the
  // completion banner). So the status badge is always just "초안 검토 중" (draft under review) — the old
  // stageStatus prop read a "Discovery Document" stage that does not exist and was
  // always null.

  // **The surface carries the same weight as the post-approval banner (review/page.tsx)** — same rx, same
  // padding, only the tone differs (unapproved amber / approved emerald). Previously it was the largest
  // element in the app with a violet gradient + shadow-lg + 🚦, yet this banner always appears when
  // discovery-document.md is open and not yet approved. The document is produced by Envision
  // (upstream envision.md:255), so it stays on throughout the workshop, and something that size sitting
  // there permanently teaches people to ignore it — the opposite of what the gate wants.
  //
  // `role="alert"` was removed for the same reason. alert is a live region that makes the screen reader
  // interrupt ongoing narration and cut in. This is not a sudden event but an area that resides on the
  // page, so a named region is correct (matching the post-approval banner, which is
  // already role="status").
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
      {/* The only accent is the approve button — it uses the same as the app's primary button (bg-violet-600).
          With the banner quieter, this button alone says what on the screen
          needs to be pressed. */}
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
