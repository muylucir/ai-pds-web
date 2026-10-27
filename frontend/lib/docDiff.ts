// frontend/lib/docDiff.ts — what changed in a document since the user handed over answers.
//
// **Why blocks, not lines.** The people reading this are not developers: what they look for is "which
// part of the document says something new", on the rendered document they already read. A line diff
// would have to be shown as source, and a markdown paragraph is often one long line, so a one-word
// change would still mark the whole paragraph — at no gain over a block. So the document is split
// into the blocks it renders as (a heading, a paragraph, a table, a code fence), each list item being
// its own block: a list is often the whole section, and marking the whole of it finds nothing.
//
// **Why parse instead of splitting on blank lines.** A blank line inside a code fence, or between the
// items of a loose list, is not a block boundary; cutting there renders broken fragments. The split
// comes from the same parser the renderer uses (remark + GFM), from each node's source offsets — so
// every block is a slice of the original text and renders exactly as it did in the whole document.
//
// **Tables get rows.** Decision tables are where answers usually land, and they run long. When a table
// changed, the rows that changed are found too, so the mark can sit on the row.
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import type { Root, RootContent } from "mdast";

export type BlockKind = "heading" | "table" | "other";

export interface Block {
  text: string;
  kind: BlockKind;
  // Plain text of a heading — the change summary names sections by it.
  heading?: string;
}

export type Change =
  | { op: "same"; block: Block }
  | { op: "added"; block: Block }
  | { op: "removed"; block: Block }
  // `rows`: for a table, the 1-based lines of `block.text` that are new or changed, and how many
  // rows of the earlier table are gone.
  | { op: "modified"; block: Block; before: Block; rows?: { changed: Set<number>; removed: number } };

const parser = unified().use(remarkParse).use(remarkGfm);

function plainText(node: RootContent | Root): string {
  if ("value" in node && typeof node.value === "string") return node.value;
  if ("children" in node) return (node.children as RootContent[]).map(plainText).join("");
  return "";
}

function slice(text: string, node: RootContent): string | null {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  return start === undefined || end === undefined ? null : text.slice(start, end);
}

export function splitBlocks(text: string): Block[] {
  const root = parser.parse(text) as Root;
  const blocks: Block[] = [];
  for (const node of root.children) {
    if (node.type === "list") {
      for (const item of node.children) {
        const body = slice(text, item);
        if (body !== null) blocks.push({ text: body, kind: "other" });
      }
      continue;
    }
    const body = slice(text, node);
    if (body === null) continue;
    if (node.type === "heading") {
      blocks.push({ text: body, kind: "heading", heading: plainText(node).trim() });
    } else {
      blocks.push({ text: body, kind: node.type === "table" ? "table" : "other" });
    }
  }
  return blocks;
}

// Whitespace the reader cannot see does not count as a change — the agent often rewrites a document
// whole, and trailing spaces or a reflowed blank line would otherwise mark untouched blocks.
function blockKey(block: Block): string {
  return block.text.split("\n").map((l) => l.trimEnd()).filter((l) => l !== "").join("\n").trim();
}

function rowKey(line: string): string {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()).join("|");
}

// Above this many cells the table of the middle part is not built: both sides are taken as replaced.
// Only a document rewritten from end to end gets near it, and there everything did change.
const MAX_CELLS = 4_000_000;

// Index pairs (i, j) with a[i] === b[j] forming a longest common subsequence, in order.
function commonPairs(a: string[], b: string[]): Array<[number, number]> {
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head
         && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const pairs: Array<[number, number]> = [];
  for (let k = 0; k < head; k++) pairs.push([k, k]);
  const n = a.length - head - tail;
  const m = b.length - head - tail;
  if (n > 0 && m > 0 && n * m <= MAX_CELLS) {
    // lengths[i][j] = LCS length of a[head+i..] and b[head+j..], flattened.
    const w = m + 1;
    const lengths = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lengths[i * w + j] = a[head + i] === b[head + j]
          ? lengths[(i + 1) * w + j + 1] + 1
          : Math.max(lengths[(i + 1) * w + j], lengths[i * w + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[head + i] === b[head + j]) {
        pairs.push([head + i, head + j]);
        i++;
        j++;
      } else if (lengths[(i + 1) * w + j] >= lengths[i * w + j + 1]) {
        i++;
      } else {
        j++;
      }
    }
  }
  for (let k = tail; k > 0; k--) pairs.push([a.length - k, b.length - k]);
  return pairs;
}

function tableRows(before: Block, after: Block): { changed: Set<number>; removed: number } {
  // Line 2 is the delimiter row (`|---|`); it is never a change of its own.
  const lines = (t: string) => t.split("\n").map(rowKey);
  const a = lines(before.text);
  const b = lines(after.text);
  const pairs = commonPairs(a, b);
  const kept = new Set(pairs.map(([, j]) => j));
  const changed = new Set<number>();
  b.forEach((_, j) => {
    if (j !== 1 && !kept.has(j)) changed.add(j + 1);
  });
  // A rewritten row is unmatched on both sides, so it is a change, not a removal: only the rows the
  // table lost count.
  return { changed, removed: Math.max(0, a.length - b.length) };
}

function modified(before: Block, after: Block): Change {
  return after.kind === "table"
    ? { op: "modified", block: after, before, rows: tableRows(before, after) }
    : { op: "modified", block: after, before };
}

// One stretch between two unchanged blocks. A removed and an added block of the same kind are read as
// one block rewritten, matched in order — so an edited paragraph shows as edited, with its earlier
// text beside it, rather than as one deletion and one addition far apart.
function gap(removed: Block[], added: Block[]): Change[] {
  const out: Change[] = [];
  let r = 0;
  for (const block of added) {
    let match = r;
    while (match < removed.length && removed[match].kind !== block.kind) match++;
    if (match < removed.length) {
      for (; r < match; r++) out.push({ op: "removed", block: removed[r] });
      out.push(modified(removed[match], block));
      r = match + 1;
    } else {
      out.push({ op: "added", block });
    }
  }
  for (; r < removed.length; r++) out.push({ op: "removed", block: removed[r] });
  return out;
}

export function diffBlocks(beforeText: string, afterText: string): Change[] {
  const before = splitBlocks(beforeText);
  const after = splitBlocks(afterText);
  const pairs = commonPairs(before.map(blockKey), after.map(blockKey));
  const out: Change[] = [];
  let i = 0;
  let j = 0;
  for (const [pi, pj] of [...pairs, [before.length, after.length] as [number, number]]) {
    out.push(...gap(before.slice(i, pi), after.slice(j, pj)));
    if (pi < before.length) out.push({ op: "same", block: after[pj] });
    i = pi + 1;
    j = pj + 1;
  }
  return out;
}

export interface ChangedSection {
  // null — changes above the first heading.
  heading: string | null;
  // Index into the change list of the first change in this section.
  first: number;
}

// Sections that hold a change, in document order, each named by the heading it sits under.
export function changedSections(changes: Change[]): ChangedSection[] {
  const sections: ChangedSection[] = [];
  let current: string | null = null;
  let marked = false;
  changes.forEach((c, index) => {
    if (c.op !== "removed" && c.block.kind === "heading") {
      current = c.block.heading ?? "";
      marked = false;
    }
    if (c.op !== "same" && !marked) {
      sections.push({ heading: current, first: index });
      marked = true;
    }
  });
  return sections;
}
