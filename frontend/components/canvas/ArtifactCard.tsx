// Presentational 📕 artifact button (mockup 04's inline artifact-card idiom).
// The mockup's title/sub-copy is verbatim static chrome: the backend document
// is one markdown blob (no part metadata), so "Part 1: Envision" is NOT
// derived from `path` — it's the mockup's fixed label for the one artifact
// card kind this slice produces (discovery-document.md).
export function ArtifactCard({ path, onOpen }: { path: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="discovery-document.md을 우측 패널에서 열기"
      className="w-full text-left rounded-xl border border-slate-200 bg-white hover:border-violet-300 hover:shadow-sm transition-all px-4 py-3 flex items-center gap-3"
    >
      <span
        className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center text-lg shrink-0"
        aria-hidden="true"
      >
        📕
      </span>
      <span className="flex-1 min-w-0">
        <span className="block font-medium text-sm">discovery-document.md — Part 1: Envision</span>
        {/* Deliberate deviation from the mockup (whole-branch review Minor-5):
            the mockup's sub-line is a content summary of the document, but
            deriving a summary from file content would be methodology logic
            (forbidden — see Global Constraints). We render the raw path
            instead, which is data we already have with no content sniffing. */}
        <span className="block text-[11px] text-slate-400 mt-0.5">{path}</span>
      </span>
      <span className="text-xs text-violet-600 shrink-0">패널에서 열기 →</span>
    </button>
  );
}
