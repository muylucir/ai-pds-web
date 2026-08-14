// frontend/lib/manualToc.ts — 목차와 스크롤 위치 표시가 공유하는 앵커 계산.
//
// 목차(ManualToc)와 스크롤 감시(useScrollSpy)가 **같은 목록**을 봐야 한다.
// 각자 blocks를 훑으면 한쪽이 소제목을 빠뜨렸을 때 강조가 조용히 어긋난다.
import type { ManualSection } from "@/content/manual";

export interface ManualHeading {
  id: string;
  text: string;
}

/** 절 안의 소제목. 목차의 하위 항목이자 딥링크 앵커다. */
export function headingsOf(section: ManualSection): ManualHeading[] {
  return section.blocks.flatMap((b) =>
    b.kind === "heading" ? [{ id: b.id, text: b.text }] : [],
  );
}

/**
 * 문서에 있는 모든 앵커를 **문서 순서대로**. useScrollSpy가 이 순서로
 * "첫 번째로 보이는 것"을 고르므로 순서가 load-bearing이다.
 */
export function anchorIds(sections: ManualSection[]): string[] {
  return sections.flatMap((s) => [s.id, ...headingsOf(s).map((h) => h.id)]);
}
