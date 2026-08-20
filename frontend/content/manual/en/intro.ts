import type { ManualSection } from "../types";

export const intro: ManualSection = {
  id: "intro",
  title: "What AI-PDS Web is",
  lede: "A tool for running AI-PLC Discovery as a conversation in your browser. No developer tooling to install.",
  blocks: [
    {
      kind: "md",
      md: `AI-PDS Web helps a product person **push an idea all the way to something testable**.
You answer questions in chat, the AI writes the documents, those documents become a prototype
that actually runs, and reactions to that prototype come back through a survey and into the
documents.

There is no terminal, no git, no editor. Everything happens in the browser after you sign in.`,
    },
    { kind: "heading", id: "what-you-get", text: "What you end up with" },
    {
      kind: "md",
      md: `| Output | Where you find it |
|---|---|
| Discovery documents (Markdown) | Document Review tab — download individual \`.md\` files or all of them as \`.zip\` |
| Prototype specs \`PROTOTYPE-*.md\` | Document Review tab. You can hand off just these files to a build team |
| A running prototype | Prototypes tab — share the link and it opens without an account |
| Validation survey results | The survey panel in the Prototypes tab — a rollup view and a CSV |
| The audit record \`audit.md\` | Document Review tab — everything you typed, kept verbatim |`,
    },
    { kind: "heading", id: "flow", text: "The overall flow" },
    {
      kind: "diagram",
      id: "entry-points",
      caption: "There are three ways to start, and they all meet at the prototype.",
      nodes: {
        pain: { label: "Start from pain points", to: "start" },
        usecase: { label: "Start from use cases", to: "start" },
        spec: { label: "Start from an existing spec", to: "prototypes" },
        build: { label: "Build the prototype", to: "prototypes" },
        validate: { label: "Validate with a survey", to: "survey" },
        ship: { label: "Product strategy · go-to-market" },
      },
    },
    {
      kind: "md",
      md: `- **Start from customer pain points** — collect them, write the PR/FAQ, derive the solution from it.
- **Start from use cases** — if you already have candidates, begin by prioritizing them.
- **Start from prototype specs you already have** — if \`PROTOTYPE-*.md\` files exist, the earlier stages are skipped and building starts right away.

Whichever way you start, you build and validate a prototype and then continue into product
strategy and go-to-market.`,
    },
    { kind: "heading", id: "four-tabs", text: "The four screens" },
    {
      kind: "md",
      md: `| Tab | What it is for |
|---|---|
| Dashboard | See where you are — progress, completed stages, artifacts produced |
| Workspace | Where the work happens — talk to the AI and answer its questions |
| Document Review | Read what was written and approve it or ask for a revision |
| Prototypes | Build a spec into a real app, host it, and validate it with a survey |

All four belong to one project. They are not clickable until you have selected a project.`,
    },
    {
      kind: "callout",
      tone: "tip",
      md: `**You do not have to march through a fixed order.** The workflow adapts to the work —
say "I want to go back to the previous stage" or "let's skip this" in chat and it happens.`,
    },
    {
      kind: "details",
      summary: "Where AI-PDS Web sits within AI-PLC",
      md: `AI-PLC runs Discovery → Inception → Construction → Operations.
AI-PDS Web covers **Discovery only**. Once you hand the Discovery documents and the
\`PROTOTYPE-*.md\` files to a build team, the later phases happen in a developer workspace.

The prototype build exists to validate an idea inside Discovery. What it produces is not
production code.`,
    },
  ],
};
