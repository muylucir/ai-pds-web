// frontend/lib/useTextDeltas.ts — 텍스트 델타를 화면 프레임마다 한 번 반영한다.
"use client";
import { useCallback, useEffect, useRef } from "react";

// 백엔드는 4~5자마다 `message` 이벤트를 하나 보내고(공백 경계로 자른 델타),
// EventSource는 이벤트마다 별개의 태스크라 React가 그것들을 묶지 못한다. 델타마다
// setItems를 하면 화면 전체가 초당 수십 번 다시 그려진다. 그래서 델타는 모았다가
// 다음 프레임에 한 번 붙인다.
//
// **순서가 정답의 일부다.** 말풍선의 `activity`는 마지막에 온 이벤트가 덮는다
// (useWorkspaceStream.applyEvent). 텍스트만 늦게 붙이면 뒤따른 도구 이벤트보다
// 텍스트가 나중에 반영되어 "글을 쓰는 중"이 "자료를 확인하는 중"을 덮는다. 그래서
// 텍스트가 아닌 갱신은 **반드시 먼저 `flush()`를 부른다** — 호출부의 patchAi가
// 그렇게 감싸므로 개별 분기가 잊을 수 없다.
//
// 숨긴 탭에서는 프레임이 오지 않으므로 델타가 쌓이기만 한다. 그려 봐야 보이지
// 않으니 그것이 맞고, 종결 이벤트(patchAi 경유)가 오거나 탭이 다시 보이면 붙는다.
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
      // 다른 말풍선으로 넘어갔다 — 앞 말풍선의 몫을 먼저 붙인다.
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
