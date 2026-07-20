// Left-pane file tree for the review page — groups aiplc-docs/ artifact paths
// by their sub-directory (relative to aiplc-docs/) and lets the caller drive
// selection. Purely presentational: no fetching here, the page owns the
// `paths`/`selected` state and passes a click handler.
export function DocTree({
  paths,
  selected,
  onSelect,
}: {
  paths: string[];
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const groups = new Map<string, string[]>();
  for (const p of paths) {
    const rel = p.replace(/^aiplc-docs\//, "");
    const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
    groups.set(dir, [...(groups.get(dir) ?? []), p]);
  }
  return (
    <nav aria-label="산출물 문서" className="text-sm space-y-3">
      {[...groups.entries()].sort().map(([dir, files]) => (
        <div key={dir || "(root)"}>
          {dir && <p className="px-2 pb-1 text-[11px] font-bold uppercase text-slate-400">{dir}</p>}
          {files.sort().map((p) => {
            const name = p.slice(p.lastIndexOf("/") + 1);
            const active = p === selected;
            return (
              <button
                key={p}
                type="button"
                onClick={() => onSelect(p)}
                aria-current={active ? "true" : undefined}
                className={`w-full text-left px-2.5 py-1.5 rounded-lg truncate ${
                  active ? "bg-violet-50 text-violet-700 font-medium" : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                📄 {name}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
