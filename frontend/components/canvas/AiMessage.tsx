// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { Markdown } from "@/components/Markdown";
import { ReasoningTrace } from "./ReasoningTrace";

// 도구명 → 사용자 친화 활동 문구. 턴 진행 중 "무슨 일이 벌어지고 있는지"를
// 접힌 추론 과정 밖에서 상시 보여준다 — 없으면 질문/문서 생성처럼 수십 초
// 걸리는 도구 실행 동안 화면이 멈춘 것처럼 보인다.
const ACTIVITY_LABELS: Record<string, string> = {
  ask_questions: "질문을 준비하고 있어요…",
  file_write: "문서를 작성하고 있어요…",
  file_append: "문서를 작성하고 있어요…",
  file_read: "자료를 확인하고 있어요…",
  report_stage: "진행 상황을 기록하고 있어요…",
  submit_document: "문서를 제출하고 있어요…",
};

function activityLabel(tool: string): string {
  return ACTIVITY_LABELS[tool] ?? `${tool} 실행 중…`;
}

function TypingDots() {
  return (
    <span aria-label="AI가 작성 중" className="inline-flex items-center gap-1 py-1">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

export function AiMessage({ item }: { item: AiItem }) {
  // 라이브 활동 라인: 스트리밍 중 가장 최근 status(도구 실행) 항목.
  // file_changed는 결과 기록이지 진행 상태가 아니므로 제외.
  const lastStatus = item.streaming
    ? [...item.trace].reverse().find((t) => t.kind === "status")
    : undefined;

  return (
    <div className="flex gap-3">
      <span
        className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-bold"
        aria-hidden="true"
      >
        AI
      </span>
      <div className="max-w-[85%] min-w-0">
        <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-md px-4 py-3 text-sm leading-relaxed" aria-live="polite">
          {item.streaming && item.text === "" ? (
            <TypingDots />
          ) : (
            <Markdown text={item.text} />
          )}
          {item.error && <p className="mt-2 text-rose-600">{item.error}</p>}
        </div>
        {lastStatus?.text && (
          <p className="mt-1.5 flex items-center gap-1.5 text-xs text-violet-600">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" aria-hidden="true" />
            {activityLabel(lastStatus.text)}
          </p>
        )}
        <ReasoningTrace entries={item.trace} />
      </div>
    </div>
  );
}
