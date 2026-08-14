"use client";
// frontend/lib/useScrollSpy.ts — 지금 화면에 보이는 앵커를 알려준다.
//
// 매뉴얼 목차가 "여기를 읽고 있다"를 표시하는 데 쓴다. scroll 이벤트로
// offsetTop을 재는 방식을 쓰지 않는 이유: 매 프레임마다 레이아웃을 강제로
// 계산하게 되고(reflow), 긴 문서에서 스크롤이 눈에 띄게 끊긴다.
// IntersectionObserver는 그 계산을 브라우저에 맡긴다.
import { useEffect, useState } from "react";

/**
 * `ids` 중 화면에 보이는 첫 앵커의 id.
 *
 * - `enabled`가 false면 관찰하지 않고 null을 준다(검색 중에는 본문이 걸러져
 *   있어 "현재 위치"가 의미를 잃는다).
 * - IntersectionObserver가 없는 환경(jsdom, 아주 오래된 브라우저)에서는
 *   조용히 null을 준다 — 목차는 강조 없이 그대로 동작한다. 던지면 매뉴얼
 *   전체가 렌더되지 않는다.
 */
export function useScrollSpy(ids: string[], enabled = true): string | null {
  const [active, setActive] = useState<string | null>(null);
  // 배열 리터럴이 매 렌더 새 참조가 되므로 값으로 의존한다.
  const key = ids.join(",");

  useEffect(() => {
    if (!enabled) {
      setActive(null);
      return;
    }
    if (typeof IntersectionObserver === "undefined") return;

    const list = key ? key.split(",") : [];
    const seen = new Map<string, boolean>();

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          seen.set(entry.target.id, entry.isIntersecting);
        }
        // **문서 순서로** 첫 번째 보이는 것을 고른다. entries의 순서가 아니다 —
        // 콜백은 바뀐 것만 담고 순서를 보장하지 않으므로, entries에서 고르면
        // 위로 스크롤할 때 아래쪽 절이 켜진다.
        const first = list.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      {
        // 위쪽 여백: sticky 헤더(h-16)에 가려진 영역은 "보이는" 것이 아니다.
        // 아래쪽을 크게 깎아 화면 상단 근처의 절만 후보로 남긴다.
        rootMargin: "-72px 0px -70% 0px",
      },
    );

    const elements = list
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    for (const el of elements) observer.observe(el);

    return () => observer.disconnect();
  }, [key, enabled]);

  return active;
}
