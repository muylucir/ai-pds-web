// frontend/lib/manualSearch.ts — 매뉴얼 본문 검색.
//
// 서버를 부르지 않는다. 매뉴얼 전체가 이미 클라이언트 번들에 있으므로,
// 검색은 문자열 매칭이면 충분하다.
import type { ManualSection } from "@/content/manual";

/**
 * 한 절의 검색 대상 텍스트. **화면에 보이는 것만** 모은다 —
 * heading id나 mockup id 같은 내부 식별자는 넣지 않는다. 넣으면
 * "workspace"를 검색했을 때 그 단어가 보이지 않는 절이 결과에 뜬다.
 */
export function sectionText(section: ManualSection): string {
  const parts: string[] = [section.title, section.lede];
  for (const b of section.blocks) {
    switch (b.kind) {
      case "md":
      case "callout":
        parts.push(b.md);
        break;
      case "heading":
        parts.push(b.text);
        break;
      case "steps":
        parts.push(...b.items);
        break;
      case "cmd":
        parts.push(...b.lines);
        if (b.caption) parts.push(b.caption);
        break;
      case "mockup":
        parts.push(b.caption);
        break;
      case "diagram":
        parts.push(b.caption);
        for (const node of Object.values(b.nodes)) parts.push(node.label);
        break;
      case "details":
        parts.push(b.summary, b.md);
        break;
    }
  }
  return parts.join("\n");
}

/**
 * 질의에 맞는 절만 남긴다. 빈 질의(또는 공백만)는 **전부** 통과시킨다 —
 * 검색창이 비어 있을 때 문서가 사라지지 않게 하는 것이 이 함수의 기본값이다.
 *
 * 대소문자를 무시한다. 한국어에는 영향이 없고, 영어 매뉴얼에서
 * "prototype"과 "Prototype"이 다른 결과를 내지 않게 한다.
 */
export function filterSections(
  sections: ManualSection[],
  query: string,
): ManualSection[] {
  const q = query.trim().toLowerCase();
  if (!q) return sections;
  return sections.filter((s) => sectionText(s).toLowerCase().includes(q));
}
