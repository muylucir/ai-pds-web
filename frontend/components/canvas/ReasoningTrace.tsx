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

// 종료한 서브에이전트가 성공했는지 실패했는지는 아이콘으로 갈라진다 — 실패가
// 완료와 같은 줄로 보이면 트레이스가 무엇이 잘못됐는지 말하지 못한다.
// `completed`만 성공이다(`failed`/`stopped`/`killed`는 전부 그렇지 않다).
const AGENT_ICON = { done: "✅", failed: "⚠️" } as const;

// 한 트레이스 줄. 도구 이름은 **고유명이라 번역하지 않는다**(`Read`는 어느 언어에서도
// Read다) — 번역되는 것은 "파일 변경" 같은 라벨뿐이고 그것은 사전에서 온다.
function traceLine(e: TraceEntry, fileChangedLabel: string,
                   thinkingLabel: string, agentDoneLabel: string,
                   agentFailedLabel: string): string {
  if (e.kind === "thinking") return `🧠 ${thinkingLabel}`;
  if (e.kind === "file_changed") return `📝 ${fileChangedLabel}: ${e.path ?? ""}`;
  if (e.kind === "agent") {
    const ok = e.status === "completed";
    // 이름은 그 에이전트에 맡긴 일이다(백엔드가 Agent 도구의 description을
    // 그대로 싣는다). 못 받았으면 라벨만 남긴다 — 빈 이름보다 낫다.
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
    // 항상 접힌 채로 시작한다. **도는 동안에는 이 컴포넌트가 아예 렌더되지 않는다**
    // (AiMessage가 `!item.streaming`으로 막는다) — 진행 상황은 입력창 위 고정 줄이
    // 맡고, 여기는 끝난 턴의 기록이다. 펼침 상태를 자동으로 만들면 옮긴 이유가
    // 없어진다.
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
