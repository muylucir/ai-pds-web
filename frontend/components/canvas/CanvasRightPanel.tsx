import { DocumentView } from "./DocumentView";
import { PreviewPanelBody } from "./PreviewPanel";

const TABS: { key: "document" | "preview"; label: string }[] = [
  { key: "document", label: "문서" },
  { key: "preview", label: "프리뷰" },
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
  return (
    <aside
      className="hidden xl:flex w-[420px] shrink-0 bg-white border-l border-slate-200 flex-col"
      aria-label="아티팩트 패널"
    >
      <div
        className="px-4 pt-3 flex gap-1 border-b border-slate-100 text-xs shrink-0"
        role="tablist"
        aria-label="아티팩트 패널 탭"
      >
        {TABS.map((t) => {
          const active = t.key === tab;
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(t.key)}
              className={
                active
                  ? "px-3 py-2 rounded-t-lg bg-violet-50 text-violet-700 font-bold border-b-2 border-violet-600"
                  : "px-3 py-2 text-slate-400 hover:text-slate-600"
              }
            >
              {t.label}
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
