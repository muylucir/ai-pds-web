import type { ManualSection } from "../types";

export const review: ManualSection = {
  id: "review",
  title: "Document review and approval",
  lede: "The screen where you read what was written and decide whether it stands or goes back for changes.",
  blocks: [
    {
      kind: "md",
      md: `Documents are listed as a tree on the left, and the one you select renders on the right.
The badge above it tells you its state — *Draft under review* or *✓ Approved*.`,
    },
    {
      kind: "mockup",
      id: "approval-gate",
      caption: "The approval gate — two buttons decide this document's next state",
    },
    { kind: "heading", id: "gate", text: "The approval gate" },
    {
      kind: "md",
      md: `When a document is waiting to be finalized, the gate appears below it. You have two choices.

- **✓ Approve and continue** — the stage is settled with this document and the work moves on.
- **✏️ Request a revision** — takes you to the workspace chat with a revision draft prefilled in
  the input. You describe there what should change and how.

Both decisions are recorded in \`audit.md\` immediately, and everything that passed through the
gate accumulates under **Approval gate history**.`,
    },
    { kind: "heading", id: "verification", text: "The AI verification summary" },
    {
      kind: "md",
      md: `Above the gate, the AI's own check of the document appears — missing items, claims with thin
support, places that contradict earlier answers. **Read this before approving.** Sending what it
lists straight back as a revision request usually resolves the document in one pass.`,
    },
    { kind: "heading", id: "export", text: "Exporting" },
    {
      kind: "md",
      md: `| Button | What you get |
|---|---|
| ⬇ Download .md | Just the document you are reading |
| ⬇ Download all (.zip) | Every artifact in the project |

For a handoff to a build team, the \`.zip\` is the convenient one. If only the prototype specs are
wanted, pulling out the \`PROTOTYPE-*.md\` files is enough — a build can start from those files
alone in another environment.`,
    },
    {
      kind: "callout",
      tone: "note",
      md: `Revising a document after approving it means it **needs approval again.** An approval applies
to a particular state of the document; once the content changes, that approval no longer points at
the content.`,
    },
    {
      kind: "details",
      summary: "What ends up in the audit trail (audit.md)",
      md: `- Everything the user typed — verbatim, with timestamps
- Question sheet answers (both the options and the notes)
- Approval and revision decisions, and when they happened
- Stage start and completion records

Values that look like API keys or passwords are not recorded. Nothing is summarized or rewritten,
so this is the file you open when you need what was actually said.`,
    },
  ],
};
