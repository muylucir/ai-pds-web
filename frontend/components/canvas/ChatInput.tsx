// frontend/components/canvas/ChatInput.tsx
"use client";
import { useState } from "react";

export function ChatInput({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  function submit() {
    const trimmed = text.trim();
    if (disabled || trimmed === "") return;
    onSend(trimmed);
    setText("");
  }

  return (
    <div className="shrink-0 border-t border-slate-200 bg-white px-4 md:px-8 py-3">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white focus-within:ring-2 focus-within:ring-violet-400 px-4 py-2.5">
          <textarea
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="메시지를 입력하세요… (질문·수정요청·되돌아가기 무엇이든)"
            className="flex-1 resize-none text-sm focus:outline-none bg-transparent disabled:opacity-50"
            aria-label="채팅 메시지 입력"
            disabled={disabled}
          />
          <button
            type="button"
            onClick={submit}
            disabled={disabled || text.trim() === ""}
            className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white flex items-center justify-center"
            aria-label="전송"
          >
            ↑
          </button>
        </div>
        <p className="text-[10px] text-slate-400 mt-1.5 text-center">
          모든 입력은 원문 그대로 audit.md에 기록됩니다 · 크리덴셜은 절대 기록되지 않습니다
        </p>
      </div>
    </div>
  );
}
