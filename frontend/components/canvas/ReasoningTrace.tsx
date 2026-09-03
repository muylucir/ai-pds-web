"use client";
// frontend/components/canvas/ReasoningTrace.tsx
import type { TraceEntry } from "@/lib/useTurnStream";
import { useT } from "@/lib/i18n/provider";

// 턴의 진행 기록 아코디언. 안에 있는 것은 모델의 추론 텍스트가 **아니다** —
// 사고 구간(thinking), 실행한 도구와 그 대상(status), 바뀐 파일(file_changed)이고
// 모두 기존 /events SSE로 온다. 프로토타입 빌드에서는 같은 컴포넌트가 빌드 로그
// 표면이 된다.
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
function traceLine(e: TraceEntry, fileChangedLabel: string,
                   thinkingLabel: string): string {
  if (e.kind === "thinking") return `🧠 ${thinkingLabel}`;
  if (e.kind === "file_changed") return `📝 ${fileChangedLabel}: ${e.path ?? ""}`;
  const name = e.text ?? "";
  const icon = TOOL_ICON[name];
  const head = icon ? `${icon} ${name}` : name;
  return e.detail ? `${head} · ${e.detail}` : head;
}

export function ReasoningTrace(
  { entries, streaming }: { entries: TraceEntry[]; streaming?: boolean },
) {
  const t = useT();
  if (entries.length === 0) return null;
  return (
    // **턴이 도는 동안에는 펼친다.** 접혀 있으면 실시간으로 쌓이는 줄을 아무도
    // 보지 못하고, 그것이 "아무 일도 일어나지 않는다"는 인상의 절반이었다 —
    // 사용자가 펼쳐야 비로소 무슨 일이 있었는지 보였다. 끝난 턴은 다시 접어
    // 대화 기록을 조용하게 둔다(`open`은 초기 상태일 뿐이므로 사용자가 직접
    // 접거나 펼친 것을 뒤집지 않는다).
    <details
      open={streaming}
      className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px]"
    >
      <summary className="cursor-pointer text-slate-500 font-medium">{t("canvas.reasoningTrace")}</summary>
      <ul className="mt-1.5 space-y-1 text-slate-500">
        {entries.map((e, i) => (
          <li key={i} className="font-mono">
            {traceLine(e, t("canvas.fileChanged"), t("canvas.thinkingTrace"))}
          </li>
        ))}
      </ul>
    </details>
  );
}
