"use client";
import { DocumentView } from "./DocumentView";
import { PreviewPanelBody } from "./PreviewPanel";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

// 라벨을 딕셔너리 키로 둔다 — 모듈 상수는 훅을 부를 수 없으므로 렌더에서
// t(labelKey)로 푼다(ActivityIndicator의 LABEL_KEYS와 같은 규약).
const TABS: { key: "document" | "preview"; labelKey: keyof Dict }[] = [
  { key: "document", labelKey: "canvas.tabDocument" },
  { key: "preview", labelKey: "canvas.tabPreview" },
];

// The switchable right panel (C1's PreviewPanel-only pane, now a controlled
// 문서/프리뷰 toggle). Geometry unchanged from C1: hidden xl:flex w-[420px].
export function CanvasRightPanel({
  projectId,
  tab,
  onTabChange,
  onApprove,
  onRevise,
  busy,
}: {
  projectId: string;
  tab: "document" | "preview";
  onTabChange: (tab: "document" | "preview") => void;
  onApprove: () => void;
  onRevise: (text: string) => void;
  busy: boolean;
}) {
  const t = useT();
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label={t("canvas.artifactPanelLabel")}
    >
      <div
        className="px-4 pt-3 flex gap-1 border-b border-slate-100 text-xs shrink-0"
        role="tablist"
        aria-label={t("canvas.artifactPanelTabsLabel")}
      >
        {/* 루프 변수를 `tabDef`로 둔다 — `t`는 번역 함수 이름이다. */}
        {TABS.map((tabDef) => {
          const active = tabDef.key === tab;
          return (
            <button
              key={tabDef.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(tabDef.key)}
              className={
                active
                  ? "px-3 py-2 rounded-t-lg bg-violet-50 text-violet-700 font-bold border-b-2 border-violet-600"
                  : "px-3 py-2 text-slate-400 hover:text-slate-600"
              }
            >
              {t(tabDef.labelKey)}
            </button>
          );
        })}
      </div>
      {tab === "document" ? (
        <DocumentView projectId={projectId} onApprove={onApprove} onRevise={onRevise} busy={busy} />
      ) : (
        <PreviewPanelBody projectId={projectId} />
      )}
    </aside>
  );
}
