// frontend/lib/useTextDeltas.ts — applies text deltas once per screen frame.
"use client";
import { useCallback, useEffect, useRef } from "react";

// The backend sends one `message` event every 4-5 characters (deltas cut at whitespace boundaries),
// and EventSource runs each event as a separate task, so React cannot batch them. Calling setItems
// per delta would redraw the whole screen dozens of times per second. So deltas are collected and
// appended once on the next frame.
//
// **Ordering is part of correctness.** The bubble's `activity` is overwritten by the last event to arrive
// (useWorkspaceStream.applyEvent). If only text were appended late, the text would be applied after a
// following tool event, and "글을 쓰는 중" (writing) would overwrite "자료를 확인하는 중" (checking sources). So
// non-text updates **must call `flush()` first** — the call site's patchAi wraps them that way,
// so an individual branch cannot forget.
//
// In a hidden tab no frames arrive, so deltas just accumulate. Drawing them would not be visible anyway,
// so that is correct; they are appended when a terminal event (via patchAi) arrives or the tab becomes visible again.
export function useTextDeltas(apply: (aiId: string, text: string) => void) {
  const pendingRef = useRef<{ aiId: string; text: string } | null>(null);
  const frameRef = useRef<number | null>(null);
  const applyRef = useRef(apply);
  applyRef.current = apply;

  const flush = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    applyRef.current(pending.aiId, pending.text);
  }, []);

  const push = useCallback(
    (aiId: string, text: string) => {
      // Moved on to a different bubble — append the previous bubble's share first.
      if (pendingRef.current && pendingRef.current.aiId !== aiId) flush();
      if (pendingRef.current) pendingRef.current.text += text;
      else pendingRef.current = { aiId, text };
      if (frameRef.current === null) {
        frameRef.current = requestAnimationFrame(() => {
          frameRef.current = null;
          flush();
        });
      }
    },
    [flush],
  );

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  return { push, flush };
}
