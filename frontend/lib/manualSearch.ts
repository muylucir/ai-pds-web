// frontend/lib/manualSearch.ts — manual body search.
//
// Does not call the server. The whole manual is already in the client bundle,
// so string matching is enough for search.
import type { ManualSection } from "@/content/manual";

/**
 * Searchable text of one section. Collects **only what is visible on screen** —
 * internal identifiers like heading ids or mockup ids are left out. Including them would
 * make a search for "workspace" return sections where that word is not visible.
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
 * Keep only sections matching the query. An empty query (or whitespace only) passes **everything** —
 * keeping the document from vanishing when the search box is empty is this function's default.
 *
 * Case-insensitive. It has no effect on Korean, and in the English manual it keeps
 * "prototype" and "Prototype" from giving different results.
 */
export function filterSections(
  sections: ManualSection[],
  query: string,
): ManualSection[] {
  const q = query.trim().toLowerCase();
  if (!q) return sections;
  return sections.filter((s) => sectionText(s).toLowerCase().includes(q));
}
