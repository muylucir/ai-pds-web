"use client";
import { previewUrl } from "@/lib/api/preview";
import { useT } from "@/lib/i18n/provider";

// Inner content (no <aside> wrapper) — the host panel supplies its own
// <aside>, so the two never nest. The preview URL comes ONLY from the previewUrl seam (C1 Task 1), which returns
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
