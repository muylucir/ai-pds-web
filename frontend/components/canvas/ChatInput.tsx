"use client";
// frontend/components/canvas/ChatInput.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n/provider";
import type { ContextUsage } from "@/lib/api/types";
import { ContextMeter } from "./ContextMeter";

export function ChatInput({
  onSend,
  disabled,
  onAttach,
  initialText,
  onInterrupt,
  interrupting,
  context,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  // When 📎 is clicked. Picking the file is the caller's job (the workspace opens the attachment modal).
  onAttach?: () => void;
  // One-time prefill + focus on mount (e.g. a draft carried over from the review screen's change-request link).
  initialText?: string;
  // Stops the running turn. These two values are a pair — without a handler the button is not
  // shown (so it does not appear on its own in other screens that use this component).
  onInterrupt?: () => void;
  // "Is there a turn to stop?" Different from `disabled` ("is input blocked?"): the prototype
  // panel keeps disabled true even after the build ends (BuildPanel.tsx's
  // `streaming || buildComplete !== null`), so deciding by that value would show ■ when there
  // is nothing to stop.
  interrupting?: boolean;
  // The agent's context usage. Not shown when absent (before the first turn, just before a new conversation).
  context?: ContextUsage | null;
}) {
  const t = useT();
  const [text, setText] = useState(initialText ?? "");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (initialText) inputRef.current?.focus();
    // Only once on mount — tracking initialText changes is unnecessary (the workspace passes it on mount).
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
      {/* Same cap as ChatTimeline — the input is as wide as the bubble area. */}
      <div className="max-w-4xl mx-auto">
        {context && <ContextMeter usage={context} />}
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white focus-within:ring-2 focus-within:ring-violet-400 px-4 py-2.5">
          {onAttach && (
            <button
              type="button"
              aria-label={t("canvas.attachFile")}
              disabled={disabled}
              onClick={onAttach}
              className="shrink-0 text-slate-400 hover:text-violet-600 disabled:opacity-50"
            >
              📎
            </button>
          )}
          <textarea
            ref={inputRef}
            // 3 rows: what is written here is usually more than one sentence (questions, change requests, going back).
            // While it was 1 row, users had to scroll to reread their own draft.
            // The workspace and the prototype build panel share this component, so this one
            // value applies to both screens. The wrapping flex is items-end, so the
            // attach and send buttons stay aligned to the bottom row.
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
