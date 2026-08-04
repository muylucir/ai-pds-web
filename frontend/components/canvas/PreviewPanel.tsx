"use client";
import { previewUrl } from "@/lib/api/preview";
import { useT } from "@/lib/i18n/provider";

// Inner content (no <aside> wrapper) — reused by CanvasRightPanel's Preview
// tab so the switchable right panel doesn't nest two <aside> elements. The
// preview URL comes ONLY from the previewUrl seam (C1 Task 1), which returns
// null until the Phase 2/3 prototype build backend exists — so today this
// renders the documented "프로토타입 빌드 대기 중" placeholder. When the build
// backend lands the same body renders a live <iframe>, no other change.
export function PreviewPanelBody({
  projectId,
  prototypeId,
}: {
  projectId: string;
  prototypeId?: string | null;
}) {
  const t = useT();
  const url = previewUrl(projectId, prototypeId);
  return (
    <>
      <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2 shrink-0">
        <span aria-hidden="true">🖥️</span>
        <p className="font-bold text-sm">{t("canvas.previewTitle")}</p>
      </div>
      {url ? (
        <iframe title={t("canvas.previewTitle")} src={url} className="flex-1 w-full border-0" />
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-center p-8 gap-3">
          <span className="text-4xl" aria-hidden="true">
            🛠️
          </span>
          <p className="font-bold text-slate-600">{t("canvas.previewWaiting")}</p>
          <p className="text-xs text-slate-400 leading-relaxed max-w-[16rem]">
            {t("canvas.previewWaitingBody")}
          </p>
        </div>
      )}
    </>
  );
}

// C1's original aside-wrapped shape — kept for backward compatibility (its
// test is unchanged). C2's canvas page (Task 5) renders CanvasRightPanel
// instead of this directly; CanvasRightPanel nests PreviewPanelBody inside
// its own single <aside>.
export function PreviewPanel({
  projectId,
  prototypeId,
}: {
  projectId: string;
  prototypeId?: string | null;
}) {
  const t = useT();
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label={t("canvas.previewPanelLabel")}
    >
      <PreviewPanelBody projectId={projectId} prototypeId={prototypeId} />
    </aside>
  );
}
