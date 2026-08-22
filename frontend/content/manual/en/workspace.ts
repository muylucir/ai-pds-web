import type { ManualSection } from "../types";

export const workspace: ManualSection = {
  id: "workspace",
  title: "Working in the workspace",
  lede: "This is where the work happens. You talk to the AI, and that conversation produces the documents.",
  blocks: [
    {
      kind: "mockup",
      id: "workspace",
      caption: "The workspace has four columns — stage progress · the conversation · answering questions · the generated document",
    },
    {
      kind: "md",
      md: `- **First column · stages** — the Discovery stages and where you are. Stages can be added or dropped as you go.
- **Second column · the conversation** — what you and the AI say to each other. This is where you direct the work and where decisions are made.
- **Third column · answering questions** — where you answer the question sheets the AI raises. When there is no question, the prototype preview or the list of recent artifacts takes its place.
- **Fourth column · the generated document** — the document just written, rendered in place. The dropdown at the top selects a different one, and ↻ reloads it. The list is ordered **most recently changed first** and the top entry is the one open — after a refresh you still land on the newest document.

On a narrow window (a small laptop, or a screen split in half) the three side columns are hidden and
only the conversation is left. Questions then arrive as a badge above the chat, and documents lead
out to the Document Review screen.`,
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
*Writing the document*, *Preparing questions*, and so on.

Expand that line to see the **reasoning**, along with what each tool actually did — the file name
when it read a file, the command when it ran one. It is where you can retrace "why did this
conclusion appear" without asking in chat.`,
    },
    { kind: "heading", id: "answer-panel", text: "The question panel (third column)" },
    {
      kind: "md",
      md: `The third column shows one of three things, in this order of priority.

| When | The third column shows |
|---|---|
| The AI has raised a question sheet | **The answer sheet** — pick the options, then **Submit answers → AI review** |
| The prototype stage is in progress | **The prototype preview** — the built screen, right there |
| Anything else | **Recent artifacts** — the files changed in this conversation |

Questions come before everything else. When *Questions presented* appears in the conversation this
column turns into the answer sheet, and Discovery waits there until an answer arrives. More
questions may follow the ones you just submitted, so the column does not slide over to the preview
on its own while the AI is still thinking.

How to fill the sheet in — the option badges, what *★ AI recommended* means, the note field,
changing an answer you already submitted — is covered in
[Answering questions](/manual#questions). You can also skip the sheet and say "question 1 is B" in
the conversation.

When the window is too narrow for this column, a **Questions awaiting answers →** badge appears
above the chat. It opens the same sheet from the bottom; Escape or a click outside closes it.`,
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
      md: `You may see *"This document has not been saved yet"* in the fourth column. Documents are saved
**when the turn ends**, so that is expected. If the message is still there after the turn
finishes, reload it with ↻.`,
    },
  ],
};
