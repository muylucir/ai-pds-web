import type { ManualSection } from "../types";

export const workspace: ManualSection = {
  id: "workspace",
  title: "Working in the workspace",
  lede: "This is where the work happens. You talk to the AI, and that conversation produces the documents.",
  blocks: [
    {
      kind: "mockup",
      id: "workspace",
      caption: "The workspace has three parts — stage progress on the left, the conversation in the middle, artifacts on the right",
    },
    {
      kind: "md",
      md: `- **Left** — the Discovery stages and where you are. Stages can be added or dropped as you go.
- **Middle** — the conversation with the AI. Question sheets are answered here.
- **Right** — documents just written and files just changed. Select one to read it in place.`,
    },
    { kind: "heading", id: "start", text: "Starting the conversation" },
    {
      kind: "md",
      md: `The first time you open it, two paths are offered.

- **Path A — start from pain points**: collect and analyze customer problems, write the PR/FAQ, derive the solution.
- **Path B — start from use cases**: if you already have use cases written up, start by prioritizing them.

Instead of picking either, **you can just start typing.** Paste in what you have and say "start
from this" — the AI works out which path that is.`,
    },
    { kind: "heading", id: "chat", text: "Chat does everything" },
    {
      kind: "md",
      md: `Almost anything a button does, a sentence does too. Common ones:

| What you want | What to say |
|---|---|
| Approve | "Approved", "go ahead with this" |
| Ask for a revision | "Rewrite the customer quote from a team lead's perspective" |
| Go back | "I want to go back to the previous stage" |
| Skip | "Let's skip this stage" |
| Check the reasoning | "Tell me where this conclusion came from" |

While the AI works, a single line tells you what it is doing — *Thinking*,
*Writing the document*, *Preparing questions*, and so on.`,
    },
    { kind: "heading", id: "interrupt", text: "Stopping a turn" },
    {
      kind: "md",
      md: `While the AI is writing, the **■** button in the input area stops that turn. Whatever it
already wrote stays, and the conversation is marked *Interrupted*. If you can see it heading the
wrong way, stopping and re-directing is faster than waiting for it to finish.`,
    },
    { kind: "heading", id: "attach", text: "Attaching files" },
    {
      kind: "md",
      md: `The clip button in the input area attaches source material.

- Types: \`.md\` · \`.txt\` · \`.csv\` · \`.xlsx\` · \`.pdf\`
- Size: up to 5MB per file
- Attachments are **converted to text** before they reach the conversation. Images and
  formatting do not survive, so if the tables matter, \`.csv\` or \`.xlsx\` beats \`.pdf\`.

Interview notes, requirement lists and market research go here, and the AI writes from them.`,
    },
    { kind: "heading", id: "audit", text: "What gets recorded" },
    {
      kind: "md",
      md: `What you type is kept **verbatim** in \`audit.md\` with a timestamp. It is not summarized or
tidied up. Decisions such as approvals and revision requests are recorded as they happen. Values
that look like API keys or passwords are never recorded.

You can read this record in the Document Review tab — it is what you go back to when someone
asks why a decision was made.`,
    },
    {
      kind: "callout",
      tone: "note",
      md: `You may see *"This document has not been saved yet"* in the right panel. Documents are saved
**when the turn ends**, so that is expected. If the message is still there after the turn
finishes, reload it with ↻.`,
    },
  ],
};
