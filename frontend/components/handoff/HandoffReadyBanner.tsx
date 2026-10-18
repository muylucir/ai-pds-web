// The door to the handoff tab once Go-to-Market is finished.
//
// **It does not rely on the agent's sentences.** GTM's final instruction is "proceed to Inception"
// (upstream go-to-market.md Step 6), a stage that does not exist in this web app. Just as the prototype banner
// has been derived from disk rather than agent sentences since ws-a-v5 for the same reason, here we check
// whether go_to_market in the capability normalized from `aiplc-state.md` (backend capabilities.py)
// is completed. Projects that wrote stage names in Korean are already folded in on that side.
//
// There is no close button — the same judgment as the prototype banner. It is the door to what comes next after
// Discovery ends, and if closed by mistake the user would have to find "인계" (Handoff) in the tab bar on their own.
"use client";
import Link from "next/link";
import type { ProjectState } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

export function discoveryFinished(state: ProjectState | null | undefined): boolean {
  return !!state?.capabilities?.some((c) => c.key === "go_to_market" && c.status === "completed");
}

export function HandoffReadyBanner({ projectId, state, className }: {
  projectId: string;
  state: ProjectState | null | undefined;
  className?: string;
}) {
  const t = useT();
  if (!discoveryFinished(state)) return null;
  return (
    <div role="status"
         className={`rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 flex items-center justify-between gap-3 text-sm ${className ?? ""}`}>
      <span className="text-violet-900">{t("handoff.ready.banner")}</span>
      <Link href={`/projects/${projectId}/handoff`}
            className="shrink-0 rounded-lg bg-violet-600 px-3 py-1.5 font-medium text-white hover:bg-violet-700">
        {t("handoff.ready.cta")}
      </Link>
    </div>
  );
}
