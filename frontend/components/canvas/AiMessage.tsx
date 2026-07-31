// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { Markdown } from "@/components/Markdown";
import { ReasoningTrace } from "./ReasoningTrace";
import { ActivityIndicator } from "./ActivityIndicator";

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
  // 진행 표시는 status가 있을 때가 아니라 **스트리밍 중이면 항상** 띄운다.
  // 종전에는 lastStatus가 있어야만 나왔는데, 도구를 아직 하나도 부르지 않은
  // 턴 시작 직후 구간(모델이 생각만 하는 구간)이 가장 길고 가장 불안하다 —
  // 정작 그 구간에 아무 표시도 없었다. tool이 null이면 인디케이터가
  // "생각하고 있어요"로 대체한다.
  //
  // 말풍선 안 타이핑 점과 동시에 뜨는 경우(텍스트가 아직 없는 구간)를 중복으로
  // 보고 생략해 봤지만 그것이 틀렸다: 그 구간이 바로 도구가 도는 구간이고,
  // "무엇을 하는 중인지 + 몇 초 됐는지"는 점 세 개가 대신할 수 없는 정보다.
  // 둘은 다른 것을 말한다 — 점은 "쓰고 있다", 인디케이터는 "무엇을 얼마나".

  return (
    <div className="flex gap-3">
      <span
        className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-xs font-bold"
        aria-hidden="true"
      >
        AI
      </span>
      <div className="max-w-[85%] min-w-0">
        {/* 말풍선은 보여줄 것이 있을 때만 그린다. 복원된 턴은 streaming이
            false이므로, 텍스트가 없으면 타이핑 점도 뜨지 않아 내용 없는 회색
            상자만 남는다 — 중단된 턴(유휴 타임아웃, SSE 끊김)이 그 모양이고
            라이브에서 그 자리에 있던 것은 진행 표시였다. 아래 ReasoningTrace는
            그대로 렌더되므로 도구를 무엇까지 돌렸는지는 남는다. */}
        {(item.streaming || item.text !== "" || item.error) && (
          <div data-testid="ai-bubble" className="bg-white border border-slate-200 rounded-2xl rounded-tl-md px-4 py-3 text-sm leading-relaxed" aria-live="polite">
            {item.streaming && item.text === "" ? (
              <TypingDots />
            ) : (
              <Markdown text={item.text} />
            )}
            {item.error && <p className="mt-2 text-rose-600">{item.error}</p>}
          </div>
        )}
        {item.streaming && <ActivityIndicator tool={lastStatus?.text} />}
        {item.interrupted && (
          <p className="mt-1.5 text-xs text-slate-400">중단됨</p>
        )}
        <ReasoningTrace entries={item.trace} />
      </div>
    </div>
  );
}
