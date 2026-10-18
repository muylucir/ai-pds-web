// frontend/content/manual/types.ts — the content model for the user manual.
//
// Why not lib/i18n/{ko,en}.ts: that dictionary is a flat key set of **screen wording**,
// while the manual is prose with paragraphs, lists, and tables. Making a key per paragraph would force
// someone looking for UI wording to wade through hundreds of lines of manual text. So it lives in a
// separate content module, and **the same discipline** is re-established with types:
// since it is `Record<ManualSectionId, ManualSection>`, a section missing from either language makes
// compilation fail (just as en.ts is `Record<keyof typeof ko, string>`).
//
// Prose is markdown strings rendered by the existing components/Markdown.tsx (react-markdown +
// gfm) — so that tables, lists, and emphasis do not become new block kinds.
// Block kinds exist **only for things markdown cannot express**.
import type { EntryPointNode, MockupId, ValidationLoopNode } from "./visuals";

/** One box in a diagram. */
export interface DiagramNode {
  /** The text inside the box. **Owned by the content** — components do not hold sentences. */
  label: string;
  /**
   * The anchor to go to when clicked (section id or heading id). Without it the box is not clickable.
   * parity.test.ts asserts that this value points to a real anchor.
   */
  to?: string;
}

/**
 * Diagram block. **Each id has different node keys, so it is a discriminated union** — so removing
 * a single box in one language makes compilation fail. Keyed by position (array index),
 * nobody would catch the order drifting.
 */
export type ManualDiagramBlock =
  | {
      kind: "diagram";
      id: "entry-points";
      caption: string;
      nodes: Record<EntryPointNode, DiagramNode>;
    }
  | {
      kind: "diagram";
      id: "validation-loop";
      caption: string;
      nodes: Record<ValidationLoopNode, DiagramNode>;
    };

export type ManualBlock =
  /** Prose. Markdown (GFM). */
  | { kind: "md"; md: string }
  /** A subheading within a section. `id` becomes a sub-item in the table of contents and a deep-link anchor. */
  | { kind: "heading"; id: string; text: string }
  /** A paragraph that must stand out. `warn` is used only for irreversible actions. */
  | { kind: "callout"; tone: "note" | "warn" | "tip"; md: string }
  /** An ordered operating procedure. Each item is inline markdown. */
  | { kind: "steps"; items: string[] }
  /** A command block. Gets a copy button. */
  | { kind: "cmd"; lines: string[]; caption?: string }
  /** A screen mockup. The component reads labels directly from the app dictionary. */
  | { kind: "mockup"; id: MockupId; caption: string }
  /** A flow diagram. Clicking a node moves to the corresponding section. */
  | ManualDiagramBlock
  /** A collapsed in-depth explanation — something a first-time reader should be able to skip. */
  | { kind: "details"; summary: string; md: string };

export type ManualBlockKind = ManualBlock["kind"];

/** A manual section. `id` becomes the `/manual#{id}` deep link. */
export interface ManualSection {
  id: ManualSectionId;
  /** Used in the table of contents and the body h2. */
  title: string;
  /** A one-line summary under the title. Also included in search. */
  lede: string;
  blocks: ManualBlock[];
}

// The **order** of sections is set by MANUAL_ORDER in index.ts. Only the set is defined here —
// putting the order in the type would change the type every time a section moved.
export type ManualSectionId =
  | "intro"
  | "getting-started"
  | "create-project"
  | "workspace"
  | "questions"
  | "review"
  | "prototypes"
  | "survey"
  | "dashboard"
  | "admin"
  | "operations";

/** The whole manual for one language. A compile error if even one section is missing. */
export type ManualContent = Record<ManualSectionId, ManualSection>;
