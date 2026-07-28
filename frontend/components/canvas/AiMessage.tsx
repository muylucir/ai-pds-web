// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { Markdown } from "@/components/Markdown";
import { ReasoningTrace } from "./ReasoningTrace";

// 도구명 → 사용자 친화 활동 문구. 턴 진행 중 "무슨 일이 벌어지고 있는지"를
// 접힌 추론 과정 밖에서 상시 보여준다 — 없으면 질문/문서 생성처럼 수십 초
// 걸리는 도구 실행 동안 화면이 멈춘 것처럼 보인다.
//
// 두 드라이버가 서로 다른 도구 이름을 보낸다: Claude Agent SDK는 내장 도구명
// (Write/Read/Edit/AskUserQuestion), Strands는 자작 도구명(file_write/…).
// 매핑에 없으면 activityLabel의 폴백이 영어 도구명을 그대로 노출하므로 양쪽을
// 모두 둔다(PATHFINDER_DISCOVERY_DRIVER 폴백 기간 동안 필요).
const ACTIVITY_LABELS: Record<string, string> = {
  // Claude Agent SDK 제한된 도구 (MCP 또는 allowed_tools로만 활성화)
  AskUserQuestion: "질문을 준비하고 있어요…",
  Write: "문서를 작성하고 있어요…",
  Edit: "문서를 작성하고 있어요…",
  MultiEdit: "문서를 작성하고 있어요…",
  // Claude Agent SDK 기본 도구 (tools=None이므로 CLI 기본 도구 전체 사용 가능).
  // 드라이버가 tools=를 설정하지 않아 제한이 없으므로, Discovery 턴에서 실제로
  // 도달 가능한 도구들을 여기 둔다. envision.md의 "URL로 분석(Mode B/C)"은
  // WebFetch가 필수이고, workspace-detection 단계는 Glob/Grep로 파일 탐색이 자연스럽다.
  // 목록이 바뀌면 여기도 함께 갱신해야 한다.
  Read: "자료를 확인하고 있어요…",
  Glob: "자료를 찾고 있어요…",
  Grep: "자료를 찾고 있어요…",
  Bash: "작업을 진행하고 있어요…",
  WebFetch: "정보를 수집하고 있어요…",
  // 양쪽 드라이버 공통 커스텀 도구
  report_stage: "진행 상황을 기록하고 있어요…",
  submit_document: "문서를 제출하고 있어요…",
  // Strands 드라이버 (env 폴백 기간 유지)
  ask_questions: "질문을 준비하고 있어요…",
  file_write: "문서를 작성하고 있어요…",
  file_append: "문서를 작성하고 있어요…",
  file_read: "자료를 확인하고 있어요…",
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
