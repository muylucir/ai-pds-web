import type { ManualSection } from "../types";

export const dashboard: ManualSection = {
  id: "dashboard",
  title: "Reading the dashboard",
  lede: "Where you are and what has been produced, on one screen.",
  blocks: [
    {
      kind: "mockup",
      id: "dashboard",
      caption: "The dashboard — summary cards and the stage timeline",
    },
    {
      kind: "md",
      md: `| Card | Meaning |
|---|---|
| Overall progress | Stages marked completed ÷ all stages |
| Completed stages | How many stages are settled |
| Question records | Question sheets presented so far. You answer them in the workspace |
| Generated artifacts | How many documents and specs exist |

The timeline on the left is the state of each stage — *Done* / *In progress* / (unmarked = not yet).
When questions are waiting, **Continue answering →** appears and takes you to the workspace.`,
    },
    { kind: "heading", id: "progress-meaning", text: "Do not read the percentage too literally" },
    {
      kind: "md",
      md: `Progress is only **the share of stages marked completed**. It is not a measure of how complete
the methodology is.

Because the workflow adapts to the work, the stage list itself changes as you go — skip a stage and
Discovery can finish without ever reaching 100%, and going back makes the percentage drop. Both are
normal. **Judge whether you are finished by the approval state in the Document Review tab.**`,
    },
    { kind: "heading", id: "recent", text: "Artifacts and recent activity" },
    {
      kind: "md",
      md: `**Generated artifacts** lists the documents written; selecting one takes you to it.
**Recent activity** is a chronological record of what happened when — coming back after a few days,
this is the fastest way to find where you left off.`,
    },
  ],
};
