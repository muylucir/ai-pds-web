"use client";
// frontend/components/canvas/ReasoningTrace.tsx
import type { TraceEntry } from "@/lib/chatItems";
import { useT } from "@/lib/i18n/provider";

// Accordion of the turn's progress record. What it holds is **not** the model's reasoning text —
// it is the thinking spans (thinking), the tools run and their targets (status), and the changed files (file_changed),
// all of which arrive over the existing /events SSE. In a prototype build, the same component becomes the build log
// surface.
// Per-tool icons. **This is a UI concern, so the frontend owns it** — the backend sends only values
// (see "labels are not built here" in backend/aipds/tool_trace.py).
// Unknown tools get only their name, with no icon — no icon is better than a wrong one.
const TOOL_ICON: Record<string, string> = {
  Read: "🔍",
  Bash: "⌘",
  Glob: "🗂",
  Grep: "🔎",
  ToolSearch: "🧰",
  WebFetch: "🌐",
};

// Whether a finished subagent succeeded or failed is distinguished by its icon — if a failure
// looks like the same line as a completion, the trace cannot say what went wrong.
// Only `completed` is success (`failed`/`stopped`/`killed` are all not).
const AGENT_ICON = { done: "✅", failed: "⚠️" } as const;

// One trace line. Tool names are **proper names and are not translated** (`Read` is `Read` in any
// language) — only labels such as "파일 변경" (file changed) are translated, and those come from the dictionary.
function traceLine(e: TraceEntry, fileChangedLabel: string,
                   thinkingLabel: string, agentDoneLabel: string,
                   agentFailedLabel: string): string {
  if (e.kind === "thinking") return `🧠 ${thinkingLabel}`;
  if (e.kind === "file_changed") return `📝 ${fileChangedLabel}: ${e.path ?? ""}`;
  if (e.kind === "agent") {
    const ok = e.status === "completed";
    // The name is the job delegated to that agent (the backend carries the Agent tool's description
    // through verbatim). If none was received, only the label remains — better than an empty name.
    const head = `${ok ? AGENT_ICON.done : AGENT_ICON.failed} ${
      ok ? agentDoneLabel : agentFailedLabel}`;
    const name = e.text ? `: ${e.text}` : "";
    return e.detail ? `${head}${name} · ${e.detail}` : `${head}${name}`;
  }
  const name = e.text ?? "";
  const icon = TOOL_ICON[name];
  const head = icon ? `${icon} ${name}` : name;
  return e.detail ? `${head} · ${e.detail}` : head;
}

export function ReasoningTrace({ entries }: { entries: TraceEntry[] }) {
  const t = useT();
  if (entries.length === 0) return null;
  return (
    // Always starts collapsed. **While the turn is running, this component is not rendered at all**
    // (AiMessage blocks it with `!item.streaming`) — progress is handled by the fixed line above the
    // input box, and this is the record of a finished turn. Auto-expanding it would defeat the reason
    // it was moved.
    <details className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px]">
      <summary className="cursor-pointer text-slate-500 font-medium">{t("canvas.reasoningTrace")}</summary>
      <ul className="mt-1.5 space-y-1 text-slate-500">
        {entries.map((e, i) => (
          <li key={i} className="font-mono">
            {traceLine(e, t("canvas.fileChanged"), t("canvas.thinkingTrace"),
                       t("canvas.agentDone"), t("canvas.agentFailed"))}
          </li>
        ))}
      </ul>
    </details>
  );
}
