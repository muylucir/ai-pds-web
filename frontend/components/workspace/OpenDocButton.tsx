// frontend/components/workspace/OpenDocButton.tsx
import { useT } from "@/lib/i18n/provider";

// The "{이름} 보기" (View {name}) above the question form. In rounds where the user must read a document
// before answering (approval gates — "Envision — Step 6 Approval Gate" etc.), it opens the document drawer in place.
// The right panel and the narrow-screen bottom sheet use the same button.
//
// Why the name is shown: the drawer opens the document the conversation is currently on. Whether that is
// the document related to this question is for the user to judge, so the button says what will open.
// Document icon. Why SVG rather than an emoji (📄): emoji drop out depending on the OS font
// (it was actually a blank on a headless Chromium screen). The drawer handle uses it too.
export function DocIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M4 1.75h5.25L12.5 5v9.25H4z" />
      <path d="M9 1.75V5.25h3.5M6 8.25h4.5M6 11h4.5" />
    </svg>
  );
}

export function OpenDocButton({ name, onClick }: { name: string; onClick: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      className="mb-4 inline-flex max-w-full items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-100"
    >
      <DocIcon />
      <span className="truncate">{t("ws.openDocNamed").replace("{name}", name)}</span>
    </button>
  );
}
