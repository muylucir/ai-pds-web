"use client";
// frontend/components/canvas/ReasoningTrace.tsx
import type { TraceEntry } from "@/lib/useTurnStream";
import { useT } from "@/lib/i18n/provider";

// Collapsible "추론 과정" (a plan-defined label; mockup 04 has no
// reasoning-trace collapsible). Also serves as the build-log surface: status
// frames are progress lines, file_changed frames are touched paths — all
// arriving over the existing /events SSE.
// 도구별 아이콘. **UI 관심사이므로 프론트가 소유한다** — 백엔드는 값만 보낸다
// (backend/aipds/tool_trace.py의 "라벨은 여기서 만들지 않는다").
// 모르는 도구는 아이콘 없이 이름만 — 잘못된 아이콘보다 없는 편이 낫다.
const TOOL_ICON: Record<string, string> = {
  Read: "🔍",
  Bash: "⌘",
  Glob: "🗂",
  Grep: "🔎",
  ToolSearch: "🧰",
  WebFetch: "🌐",
};

// 한 트레이스 줄. 도구 이름은 **고유명이라 번역하지 않는다**(`Read`는 어느 언어에서도
// Read다) — 번역되는 것은 "파일 변경" 같은 라벨뿐이고 그것은 사전에서 온다.
function traceLine(e: TraceEntry, fileChangedLabel: string): string {
  if (e.kind === "file_changed") return `📝 ${fileChangedLabel}: ${e.path ?? ""}`;
  const name = e.text ?? "";
  const icon = TOOL_ICON[name];
  const head = icon ? `${icon} ${name}` : name;
  return e.detail ? `${head} · ${e.detail}` : head;
}

export function ReasoningTrace({ entries }: { entries: TraceEntry[] }) {
  const t = useT();
  if (entries.length === 0) return null;
  return (
    <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px]">
      <summary className="cursor-pointer text-slate-500 font-medium">{t("canvas.reasoningTrace")}</summary>
      <ul className="mt-1.5 space-y-1 text-slate-500">
        {entries.map((e, i) => (
          <li key={i} className="font-mono">
            {traceLine(e, t("canvas.fileChanged"))}
          </li>
        ))}
      </ul>
    </details>
  );
}
