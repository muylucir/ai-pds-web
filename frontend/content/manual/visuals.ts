// frontend/content/manual/visuals.ts — identifiers for mockups and diagrams.
//
// Only the ids live here, to keep content (data) and components (JSX) apart. If a content
// file imported React components directly, the content files for the two languages would
// each hold their own component tree, and changing one mockup would mean editing two files.
//
// The id → component mapping is owned by components/manual/registry.tsx. That file's
// Record type forces it to cover **all** the ids here, so adding an id and forgetting
// the component fails compilation.

export type MockupId =
  /** Project creation form — ID, name, model, document language */
  | "project-create"
  /** Workspace — phase / chat / questions and context; generated documents in a drawer opened from the tab at the far right */
  | "workspace"
  /** Question sheet — single and multiple choice, Other, AI recommendation, elaboration */
  | "question-sheet"
  /** The approval gate of document review */
  | "approval-gate"
  /** The prototype card and the build-complete card */
  | "prototype-card"
  /** Validation survey panel — link sharing and tallies */
  | "survey-panel"
  /** Dashboard — progress cards and the 6-phase timeline */
  | "dashboard";

export type DiagramId =
  /** The overall flow: three entry points, with Path A and B converging at the prototype */
  | "entry-points"
  /** The loop of prototype → survey → feeding back into Discovery */
  | "validation-loop";

// Each diagram fixes **names** for its boxes. Content fills the labels by these keys
// (ManualDiagramBlock in types.ts requires all of them as a Record), and components read
// by these keys. Passing them by array index could not catch the two languages' orders drifting apart.

/** Boxes of the entry-points diagram. */
export type EntryPointNode =
  /** Start from a customer problem (pain point) */
  | "pain"
  /** Start from an already defined use case */
  | "usecase"
  /** Start from an existing prototype spec */
  | "spec"
  /** Where the three paths converge — build the prototype */
  | "build"
  /** Validate what was built by asking people */
  | "validate"
  /** The product strategy and go-to-market that follow */
  | "ship";

/** Boxes of the validation-loop diagram. */
export type ValidationLoopNode =
  /** Build or revise the prototype */
  | "build"
  /** Collect reactions through a survey */
  | "ask"
  /** Feed the results back into the documents */
  | "reflect";
