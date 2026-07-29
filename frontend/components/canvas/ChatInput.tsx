// frontend/components/canvas/ChatInput.tsx
"use client";
import { useEffect, useRef, useState } from "react";

export function ChatInput({
  onSend,
  disabled,
  onAttach,
  initialText,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  onAttach?: (file: File) => void;
  // 마운트 시 1회 프리필 + 포커스(예: 리뷰 화면의 수정 요청 링크에서 넘어온 초안).
  initialText?: string;
}) {
  const [text, setText] = useState(initialText ?? "");
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (initialText) inputRef.current?.focus();
    // 마운트 시 1회만 — initialText 변경 추적은 불필요(워크스페이스가 마운트 시 전달).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          {onAttach && (
            <>
              <button
                type="button"
                aria-label="파일 첨부"
                disabled={disabled}
                onClick={() => fileRef.current?.click()}
                className="shrink-0 text-slate-400 hover:text-violet-600 disabled:opacity-50"
              >
                📎
              </button>
              <input
                ref={fileRef}
                type="file"
                hidden
                accept=".md,.txt,.csv,.xlsx,.pdf"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onAttach(f);
                  e.target.value = ""; // allow re-selecting the same file
                }}
              />
            </>
          )}
          <textarea
            ref={inputRef}
            // 3줄: 여기 쓰는 글이 대개 한 문장이 아니다(질문·수정요청·되돌아가기).
            // 1줄이던 동안은 사용자가 자기 초안을 다시 읽으려면 스크롤해야 했다.
            // 워크스페이스와 프로토타입 빌드 패널이 이 컴포넌트를 공유하므로 이
            // 값 하나가 두 화면에 함께 적용된다. 감싸는 flex가 items-end라
            // 첨부·전송 버튼은 계속 아래 줄에 정렬된다.
            rows={3}
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
