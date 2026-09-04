"use client";
// frontend/components/canvas/AiMessage.tsx
import type { AiItem } from "@/lib/useTurnStream";
import { Markdown } from "@/components/Markdown";
import { ReasoningTrace } from "./ReasoningTrace";
import { useT } from "@/lib/i18n/provider";

function TypingDots() {
  const t = useT();
  return (
    <span aria-label={t("canvas.aiWriting")} className="inline-flex items-center gap-1 py-1">
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
  const t = useT();
  // **진행 상황은 이 컴포넌트의 일이 아니다(2026-09-04).** 진행 표시는 입력창 위
  // 고정 줄(LiveActivityBar)로, 화면이 소유한다. 말풍선 옆에서 진행 표시와 펼쳐진
  // 진행 기록이 함께 갱신되던 종전 방식은 토큰 스트리밍이 들어오면서 움직이는 것을
  // 셋으로 만들어 산만했다.
  //
  // 그래서 진행 기록은 **턴이 끝난 뒤에만** 접힌 채로 붙는다 — 도는 동안 그것을
  // 그리면 옮긴 이유가 없어진다. 여기 남는 움직임은 말풍선의 텍스트 하나뿐이다.
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
        {item.interrupted && (
          <p className="mt-1.5 text-xs text-slate-400">{t("canvas.interrupted")}</p>
        )}
        {!item.streaming && <ReasoningTrace entries={item.trace} />}
      </div>
    </div>
  );
}
