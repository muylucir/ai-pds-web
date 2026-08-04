"use client";
import Link from "next/link";
import { useT } from "@/lib/i18n/provider";

export function ClarificationBanner({
  projectId,
  path,
  preamble,
}: {
  projectId: string;
  path: string;
  preamble: string | null;
}) {
  const t = useT();
  return (
    <div role="alert" className="rounded-xl border border-amber-300 bg-amber-50 overflow-hidden mb-6">
      <div className="px-6 py-4 flex gap-3">
        <span className="text-xl shrink-0" aria-hidden="true">⚠️</span>
        <div className="text-sm">
          <p className="font-bold text-amber-900">{t("q.clarificationBanner")}</p>
          {preamble && <p className="text-amber-800 mt-1">{preamble}</p>}
          <Link
            href={`/projects/${projectId}/questions?file=${encodeURIComponent(path)}`}
            className="mt-3 inline-block px-3 py-1.5 rounded-lg bg-amber-600 text-white text-xs font-medium hover:bg-amber-700"
          >
            {t("q.goAnswerClarification")}
          </Link>
        </div>
      </div>
    </div>
  );
}
