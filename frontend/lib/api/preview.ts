// DEFERRED BACKEND SEAM — the single typed owner of the prototype preview URL.
//
// There is NO backend route today that returns a running prototype's preview
// URL: the prototype build/preview/publish pipeline is spec Phase 2/3 and is
// NOT implemented (the generic POST /message + GET /events SSE relay is all
// that exists). Until a build backend lands and exposes a /preview/* reverse
// proxy (spec §2), this returns null and the canvas renders the
// "프로토타입 빌드 대기 중" placeholder. When the build backend is present it
// sets NEXT_PUBLIC_PREVIEW_BASE_URL (or this helper is re-pointed at the real
// route), and the SAME panel renders a live <iframe> — no other code changes.
//
// This is the ONLY place a preview URL is constructed (Global Constraint:
// no scattered string-building outside the API client / this seam).
export function previewUrl(projectId: string, prototypeId?: string | null): string | null {
  const base = (process.env.NEXT_PUBLIC_PREVIEW_BASE_URL ?? "").replace(/\/$/, "");
  if (base === "") return null; // no build backend configured — deferred state
  const pid = encodeURIComponent(projectId);
  const proto = encodeURIComponent(prototypeId ?? "default");
  return `${base}/projects/${pid}/preview/${proto}`;
}
