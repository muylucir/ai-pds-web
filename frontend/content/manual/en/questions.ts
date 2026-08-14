import type { ManualSection } from "../types";

export const questions: ManualSection = {
  id: "questions",
  title: "Answering questions",
  lede: "Most of Discovery is answering questions. The answers have a shape, which is what makes them auditable later.",
  blocks: [
    {
      kind: "mockup",
      id: "question-sheet",
      caption: "A question sheet — options, your own wording, and a note all in one place",
    },
    {
      kind: "md",
      md: `When the AI needs more from you, **Questions presented** appears in the conversation and a
question sheet opens. A counter at the top shows how many you have answered, and partial work is
kept, so you can answer in more than one sitting.`,
    },
    {
      kind: "steps",
      items: [
        "Read each question's **category** and badge first — it tells you whether it is *Pick one* or *Select all that apply*.",
        "Choose an option. If none of them fit, write your own under **Other — write your own**.",
        "Use the **note** field for conditions, reasoning, or a change you want.",
        "Press **Submit answers → AI review**.",
      ],
    },
    { kind: "heading", id: "answer-kinds", text: "Reading the options" },
    {
      kind: "md",
      md: `| Marker | Meaning |
|---|---|
| *Pick one* | Radio buttons — exactly one |
| *Select all that apply* | Checkboxes — as many as fit |
| **★ AI pick** | The option the AI thinks most likely given the context so far. It is **not a request to agree** — choosing something else is often the more useful answer |
| Other — write your own | An answer outside the options. What you write becomes the record |

The **note** field is where anything the options cannot express goes. "B, but only within this
budget" survives because of it. If the options alone would be misleading, do not leave it empty.`,
    },
    { kind: "heading", id: "verify", text: "After you submit" },
    {
      kind: "md",
      md: `On submit the AI **reviews** your answers. If something you just said does not square with
something you said earlier, it stops there and asks a **clarifying question**. On screen this
reads *Conflicting answers were detected, so the gate is on hold*.

All it needs is which one holds. Answer briefly and it continues.`,
    },
    {
      kind: "callout",
      tone: "tip",
      md: `**You do not have to use the form.** Even with a sheet open you can type "Q1 is B, and for Q2
here is my own answer…" in chat. Use the sheet when the sheet is the easier way to answer.`,
    },
    { kind: "heading", id: "change-answer", text: "Changing an answer you submitted" },
    {
      kind: "md",
      md: `Change submitted answers through chat — "change my answer to Q3 from A to C" is enough.
Both the original and the change stay in \`audit.md\`, so what changed and why does not disappear.`,
    },
  ],
};
