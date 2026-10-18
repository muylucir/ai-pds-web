"use client";
import Link from "next/link";
import { useT } from "@/lib/i18n/provider";

/** The first guidance shown on the dashboard of a project where no workflow has run yet.
 *
 * **Why it is needed.** After creating a project the first screen is the dashboard, where every
 * number is 0 and the stage list, artifacts, and activity are all empty. The only clue about
 * what to do was one small gray line under the question records card
 * (`dash.questionRecordsHint`), and real users were confused on that screen.
 *
 * **Why there is no close button.** This banner disappears on its own once work starts —
 * the user has no reason to dismiss it, and adding a close button would let the user create
 * a "not started yet, but no guidance" state themselves.
 */
export function GetStartedBanner({ projectId }: { projectId: string }) {
  const t = useT();
  return (
    <section
      className="mb-8 rounded-xl border border-violet-200 bg-violet-50 px-6 py-5 flex flex-wrap items-center justify-between gap-4"
      aria-labelledby="get-started-heading"
    >
      <div className="min-w-0">
        <h2 id="get-started-heading" className="font-bold text-violet-900">
          {t("dash.notStartedTitle")}
        </h2>
        <p className="mt-1 text-sm text-violet-800">{t("dash.notStartedBody")}</p>
      </div>
      <Link
        href={`/projects/${projectId}/workspace`}
        className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium"
      >
        {t("dash.notStartedCta")}
      </Link>
    </section>
  );
}
