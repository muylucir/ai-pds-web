// frontend/components/canvas/ReasoningTrace.tsx
import type { TraceEntry } from "@/lib/useTurnStream";

// Collapsible "추론 과정" (a plan-defined label; mockup 04 has no
// reasoning-trace collapsible). Also serves as the build-log surface: status
// frames are progress lines, file_changed frames are touched paths — all
// arriving over the existing /events SSE.
export function ReasoningTrace({ entries }: { entries: TraceEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px]">
      <summary className="cursor-pointer text-slate-500 font-medium">추론 과정</summary>
      <ul className="mt-1.5 space-y-1 text-slate-500">
        {entries.map((e, i) => (
          <li key={i} className="font-mono">
            {e.kind === "file_changed" ? `📝 파일 변경: ${e.path ?? ""}` : e.text}
          </li>
        ))}
      </ul>
    </details>
  );
}
