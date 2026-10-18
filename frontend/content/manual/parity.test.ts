// frontend/content/manual/parity.test.ts
//
// **The manuals in the two languages must be the same document.** The section set is already
// enforced by the type (ManualContent = Record<ManualSectionId, ManualSection>), but there are
// three things the type cannot see, and all three really do drift easily:
//
//   1. One side having an extra block or a different order — adding one more paragraph to the
//      Korean manual and forgetting the English is the most common failure. Then English users do
//      not even know that explanation exists.
//   2. Different heading ids — if the table of contents' sub-items and deep links differ by
//      language, links like `/manual#reset` cannot be shared.
//   3. Different mockup/diagram ids — a state where only one language has a screen illustration.
//
// So **the block structure is compared as is**. The values (sentences) naturally differ, so only
// kind and id are extracted and compared.
import { describe, expect, it } from "vitest";

import { MANUAL_ORDER, manualFor } from "./index";
import type { ManualBlock, ManualSection } from "./types";

/** The block's fingerprint, keeping only what must not change under translation. */
function shape(b: ManualBlock): string {
  switch (b.kind) {
    case "heading":
      return `heading:${b.id}`;
    case "mockup":
      return `mockup:${b.id}`;
    case "diagram":
      // Even the box **name set** is compared. The type already requires it as a Record, but this
      // line is needed for the failure message to say "which box differs".
      return `diagram:${b.id}:${Object.keys(b.nodes).sort().join(",")}`;
    case "callout":
      return `callout:${b.tone}`;
    case "steps":
      return `steps:${b.items.length}`;
    case "cmd":
      // Commands are not translated — they must match line for line.
      return `cmd:${b.lines.join(" | ")}`;
    default:
      return b.kind;
  }
}

function shapes(s: ManualSection): string[] {
  return s.blocks.map(shape);
}

const ko = manualFor("ko");
const en = manualFor("en");

describe("매뉴얼 콘텐츠 패리티", () => {
  it("두 언어가 같은 절을 같은 순서로 갖는다", () => {
    expect(MANUAL_ORDER.map((id) => ko[id].id)).toEqual(MANUAL_ORDER);
    expect(MANUAL_ORDER.map((id) => en[id].id)).toEqual(MANUAL_ORDER);
  });

  it.each([...MANUAL_ORDER])("'%s' 절의 블록 구조가 두 언어에서 같다", (id) => {
    expect(shapes(en[id])).toEqual(shapes(ko[id]));
  });

  it.each([...MANUAL_ORDER])("'%s' 절의 제목·요약·본문이 비어 있지 않다", (id) => {
    for (const [locale, content] of [["ko", ko], ["en", en]] as const) {
      const s = content[id];
      expect(s.title.trim(), `${locale}/${id} title`).not.toEqual("");
      expect(s.lede.trim(), `${locale}/${id} lede`).not.toEqual("");
      expect(s.blocks.length, `${locale}/${id} blocks`).toBeGreaterThan(0);
      for (const b of s.blocks) {
        if (b.kind === "md") expect(b.md.trim(), `${locale}/${id} md`).not.toEqual("");
        if (b.kind === "callout") expect(b.md.trim(), `${locale}/${id} callout`).not.toEqual("");
        if (b.kind === "details") {
          expect(b.summary.trim(), `${locale}/${id} details summary`).not.toEqual("");
          expect(b.md.trim(), `${locale}/${id} details body`).not.toEqual("");
        }
        if (b.kind === "steps") {
          expect(b.items.length, `${locale}/${id} steps`).toBeGreaterThan(0);
          for (const item of b.items) expect(item.trim()).not.toEqual("");
        }
        if (b.kind === "mockup" || b.kind === "diagram") {
          expect(b.caption.trim(), `${locale}/${id} caption`).not.toEqual("");
        }
      }
    }
  });
});

describe("앵커", () => {
  // Section ids and heading ids must be unique within one document — if they overlap, a table
  // of contents click goes to the wrong place, and the scroll position indicator lights up
  // two items at once.
  it.each([["ko", ko], ["en", en]] as const)("%s: 앵커 id가 유일하다", (_locale, content) => {
    const ids: string[] = [];
    for (const id of MANUAL_ORDER) {
      ids.push(id);
      for (const b of content[id].blocks) if (b.kind === "heading") ids.push(b.id);
    }
    expect(ids).toEqual([...new Set(ids)]);
  });

  it.each([["ko", ko], ["en", en]] as const)(
    "%s: 본문의 /manual#앵커 링크가 실제 앵커를 가리킨다",
    (_locale, content) => {
      const known = new Set<string>();
      for (const id of MANUAL_ORDER) {
        known.add(id);
        for (const b of content[id].blocks) if (b.kind === "heading") known.add(b.id);
      }
      const dangling: string[] = [];
      for (const id of MANUAL_ORDER) {
        for (const b of content[id].blocks) {
          // A diagram box's link. If this breaks, clicking the illustration does nothing —
          // unlike a body link, it is a breakage that does not stand out.
          if (b.kind === "diagram") {
            for (const [name, node] of Object.entries(b.nodes)) {
              if (node.to && !known.has(node.to)) {
                dangling.push(`${id}: diagram ${b.id}.${name} → #${node.to}`);
              }
            }
            continue;
          }
          const text = b.kind === "md" || b.kind === "callout" || b.kind === "details"
            ? b.md
            : b.kind === "steps" ? b.items.join("\n") : "";
          for (const m of text.matchAll(/\/manual#([\w-]+)/g)) {
            if (!known.has(m[1])) dangling.push(`${id}: #${m[1]}`);
          }
        }
      }
      expect(dangling).toEqual([]);
    },
  );
});
