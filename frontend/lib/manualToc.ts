// frontend/lib/manualToc.ts — the anchor computation shared by the table of contents and the scroll position indicator.
//
// The table of contents (ManualToc) and the scroll watcher (useScrollSpy) must see **the same list**.
// If each scanned blocks on its own, the highlight would silently drift when one side missed a subheading.
import type { ManualSection } from "@/content/manual";

export interface ManualHeading {
  id: string;
  text: string;
}

/** Subheadings within a section. Sub-items of the table of contents and deep-link anchors. */
export function headingsOf(section: ManualSection): ManualHeading[] {
  return section.blocks.flatMap((b) =>
    b.kind === "heading" ? [{ id: b.id, text: b.text }] : [],
  );
}

/**
 * Every anchor in the document, **in document order**. useScrollSpy picks
 * "the first one visible" in this order, so the order is load-bearing.
 */
export function anchorIds(sections: ManualSection[]): string[] {
  return sections.flatMap((s) => [s.id, ...headingsOf(s).map((h) => h.id)]);
}
