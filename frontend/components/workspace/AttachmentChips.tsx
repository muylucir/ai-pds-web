// frontend/components/workspace/AttachmentChips.tsx
export function AttachmentChips({
  paths,
  onRemove,
}: {
  paths: string[];
  onRemove: (path: string) => void;
}) {
  if (paths.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 px-1 pb-2">
      {paths.map((p) => (
        <span
          key={p}
          className="inline-flex items-center gap-1.5 rounded-full bg-violet-50 border border-violet-200 px-3 py-1 text-xs text-violet-700"
        >
          <span aria-hidden="true">📎</span>
          <span>{p.replace(/^uploads\//, "")}</span>
          <button
            type="button"
            aria-label={`${p} 제거`}
            onClick={() => onRemove(p)}
            className="text-violet-400 hover:text-violet-700"
          >
            ✕
          </button>
        </span>
      ))}
    </div>
  );
}
