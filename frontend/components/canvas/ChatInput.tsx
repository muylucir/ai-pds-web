"use client";
// frontend/components/canvas/ChatInput.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";

export function ChatInput({
  onSend,
  disabled,
  onAttach,
  initialText,
  onInterrupt,
  interrupting,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  onAttach?: (file: File) => void;
  // 마운트 시 1회 프리필 + 포커스(예: 리뷰 화면의 수정 요청 링크에서 넘어온 초안).
  initialText?: string;
  // 진행 중인 턴을 끊는다. 이 두 값은 짝이다 — 핸들러가 없으면 버튼을 띄우지
  // 않는다(이 컴포넌트를 쓰는 다른 화면에 저절로 생기지 않게).
  onInterrupt?: () => void;
  // "중단할 턴이 있는가". `disabled`("입력을 막는가")와 다르다: 프로토타입
  // 패널은 빌드가 끝난 뒤에도 disabled가 참이므로(BuildPanel.tsx의
  // `streaming || buildComplete !== null`) 그 값으로 판단하면 중단할 것이
  // 없는데 ■이 뜬다.
  interrupting?: boolean;
}) {
  const t = useT();
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
                aria-label={t("canvas.attachFile")}
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
            placeholder={t("canvas.messagePlaceholder")}
            className="flex-1 resize-none text-sm focus:outline-none bg-transparent disabled:opacity-50"
            aria-label={t("canvas.messageInputLabel")}
            disabled={disabled}
          />
          {interrupting && onInterrupt ? (
            <button
              type="button"
              onClick={onInterrupt}
              className="shrink-0 w-8 h-8 rounded-lg bg-slate-700 hover:bg-slate-800 text-white flex items-center justify-center"
              aria-label={t("canvas.stop")}
            >
              ■
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || text.trim() === ""}
              className="shrink-0 w-8 h-8 rounded-lg bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white flex items-center justify-center"
              aria-label={t("canvas.send")}
            >
              ↑
            </button>
          )}
        </div>
        <p className="text-[10px] text-slate-400 mt-1.5 text-center">
          {t("chat.auditNotice")}
        </p>
      </div>
    </div>
  );
}
