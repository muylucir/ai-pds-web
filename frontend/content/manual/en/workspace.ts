import type { ManualSection } from "../types";

export const workspace: ManualSection = {
  id: "workspace",
  title: "Working in the workspace",
  lede: "This is where the work happens. You talk to the AI, and that conversation produces the documents.",
  blocks: [
    {
      kind: "mockup",
      id: "workspace",
      caption: "The workspace — phase progress · the conversation · answering questions, with the generated document in a drawer at the right edge",
    },
    {
      kind: "md",
      md: `- **Left column · phases** — the six AI-PDS phases (Envision · Use Case Intake · Prioritize · Prototype · Product Strategy · Go-to-Market) and where you are. A phase the path you took does not pass through reads *Not on this path* and is left out of the count.
- **Middle column · the conversation** — what you and the AI say to each other. This is where you direct the work and where decisions are made.
- **Right column · answering questions** — where you answer the question sheets the AI raises. When there is no question, the prototype preview or the list of recent artifacts takes its place.
- **The generated document** — not a column. It opens in a drawer that slides over the right side when you press the **Generated documents** tab at the right edge of the screen ([below](/manual#doc-drawer)).

On a narrow window (a small laptop, or a screen split in half) the phase and question columns are
hidden and only the conversation is left. Questions then arrive as a badge above the chat, and the
document drawer covers the full width when it opens.`,
    },
    { kind: "heading", id: "doc-drawer", text: "The generated document" },
    {
      kind: "md",
      md: `The document just written is rendered in a drawer that covers the right side of the screen. It
starts closed, so the question sheet keeps its width. Three things open it:

- The **Generated documents** tab at the right edge, which also shows how many documents there are
- **Open document** in the *The document was updated (vN)* notice in the conversation
- **View {document}** above the question sheet — for rounds where you read the document before answering, such as an approval gate

When a new document is written while the drawer is closed, the tab fills in and reads **New
document**. It does not open by itself, because it would cover the sheet you were answering. Close
it with the same tab, ✕, Esc, or a click outside.

Inside, the dropdown at the top selects a different document and ↻ reloads it. The list is ordered
**most recently changed first** and the top entry is the one open — after a refresh you still land
on the newest document. **Go to the full document review →** at the bottom takes you to
[Document Review](/manual#review).

While the AI is writing a document, the progress line above the input box shows **which file and
how many characters so far** (*Writing the document*, with *business-context.md · 1,234 chars* on the
line below). A long document takes one to two minutes and appears in the drawer once it is written.`,
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

While the AI works, the progress line **just above the message box** tells you what it is doing
right now — *Thinking*, *Reading source material*, *Writing the document* — with the elapsed time,
and on the line below, what it is working on (the file it reads or writes, the command it runs). It
always shows only the **most recent** thing and never pushes the conversation around. While the
number keeps climbing, nothing is stuck.

When the turn ends, the order it went through is collected into an **activity log** that attaches
below the answer, collapsed. Expand it to see where the model thought, which tools ran and on what
(the file name when it read a file, the command when it ran one), and which files changed. It is
where you can retrace "why did this conclusion appear" without asking in chat.

**N% context left**, above the message box on the right, is how much room the AI has left to hold
the conversation. At 0% the AI summarises the earlier conversation to make room and carries on —
from then on it remembers earlier details only as far as the summary kept them. It turns amber
below 25% and red below 10%; hover to see the token counts.`,
    },
    { kind: "heading", id: "answer-panel", text: "The question panel (right column)" },
    {
      kind: "md",
      md: `The right column shows one of three things, in this order of priority.

| When | The right column shows |
|---|---|
| The AI has raised a question sheet | **The answer sheet** — pick the options, then **Submit answers → AI review** |
| The Prototype phase is in progress | **The prototype preview** — the built screen, right there |
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
      md: `The clip button (📎) in the input area opens the attach dialog. It lists the supported types and how the AI
receives each, and takes several files at once; a file that fails shows why, right there. If you already know
the types, drop files straight onto the conversation instead.

- Documents, sheets and slides: \`.md\` · \`.txt\` · \`.csv\` · \`.xlsx\` · \`.docx\` · \`.pptx\` · \`.hwpx\` · \`.pdf\`
- Web pages: \`.html\` · \`.htm\` (a page saved with the browser's "Save as")
- Meeting transcripts: \`.vtt\` · \`.srt\` (captions downloaded from Teams, Zoom or Chime)
- Images: \`.png\` · \`.jpg\` · \`.gif\` · \`.webp\` — screenshots, whiteboard photos, sketches
- Size: up to 10MB per file

Documents are **converted to text** before they reach the conversation. Formatting and
pictures inside a document are dropped, and anything past 50,000 characters is cut. Images
reach the AI as pictures. A PDF is both — the AI reads the converted text, and looks at the
original when it needs a chart or a scanned page.

Legacy formats (\`.doc\` · \`.ppt\` · \`.xls\` · \`.hwp\`) are not accepted. Save them in the newer format first.

Interview notes, requirement lists and market research go here, and the AI writes from them.`,
    },
    { kind: "heading", id: "audit", text: "What gets recorded" },
    {
      kind: "md",
      md: `What you type, and the answers you pick in a question form, are kept **verbatim** in \`audit.md\`
with the real time. AI-PDS records them itself rather than having the AI copy them, so nothing is
summarized or tidied up; the AI then appends its own reasoning and decisions. Decisions such as approvals and revision requests are recorded as they happen. Values
that look like API keys or passwords are never recorded.

You can read this record in the Document Review tab — it is what you go back to when someone
asks why a decision was made.`,
    },
    {
      kind: "callout",
      tone: "note",
      md: `You may see *"This document has not been saved yet"* in the document drawer. Documents are saved
**when the turn ends**, so that is expected. If the message is still there after the turn
finishes, reload it with ↻.`,
    },
  ],
};
