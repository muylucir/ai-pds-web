"use client";
// frontend/components/canvas/HistoryQuestionsCard.tsx — 과거 턴에서 제시된
// 질문지의 **읽기 전용** 표시.
//
// 종전에는 이 자리에 "📋 질문지 제시됨" 한 줄뿐이었다. 복원 경로가 트랜스크립트의
// tool_use.input(질문 payload가 구조화된 채로 남아 있는 곳)을 버렸기 때문이다 —
// 그래서 스크롤백에서 "무엇을 물었는지"를 알 수 없었고, 답변 말풍선만 보면
// 문맥이 없었다. payload가 있으면 문항 수를 보여주고 펼쳐서 질문과 보기를 읽게
// 한다.
//
// 라이브 폼(QuestionCardSlot)이 **아니다**: 여기서 다시 답할 수는 없다. 그
// 라운드는 이미 끝났고, 답변은 바로 아래 말풍선에 있다.
import { useState } from "react";
import type { QuestionFile } from "@/lib/api/types";
import { useT } from "@/lib/i18n/provider";

export function HistoryQuestionsCard({
  name,
  file,
}: {
  name: string | null;
  file?: QuestionFile | null;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const questions = file?.questions ?? [];

  return (
    <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-xs text-violet-700">
      <div className="flex items-center justify-between gap-3">
        <p>
          📋 {t("chat.questionsPresented")}
          {name ? ` — ${name}` : ""}
          {questions.length > 0 ? ` · ${questions.length}${t("chat.questionCountSuffix")}` : ""}
        </p>
        {questions.length > 0 && (
          <button
            type="button"
            aria-expanded={expanded}
            className="text-[11px] text-violet-400 hover:text-violet-700 shrink-0"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? t("canvas.collapse") : t("canvas.expand")}
          </button>
        )}
      </div>
      {expanded && (
        <ul className="mt-2 space-y-2 border-t border-violet-200 pt-2">
          {questions.map((q) => (
            <li key={q.number}>
              <p className="font-medium text-slate-700">
                Q{q.number}. {q.text}
              </p>
              {/* is_other는 자유 입력 자리표시자다 — 실제로 제시된 보기가
                  아니므로 목록에서 뺀다(answerSummary의 letterText와 같은
                  판단). */}
              <ul className="mt-0.5 text-slate-500">
                {q.options
                  .filter((o) => !o.is_other)
                  .map((o) => (
                    <li key={o.letter}>
                      {o.letter}. {o.text}
                    </li>
                  ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
