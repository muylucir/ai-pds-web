import type { ManualSection } from "../types";

export const survey: ManualSection = {
  id: "survey",
  title: "Validation surveys",
  lede: "Collect reactions from people who used the prototype and feed them back into the Discovery documents.",
  blocks: [
    {
      kind: "diagram",
      id: "validation-loop",
      caption: "Prototype → survey → revision. Each turn of this loop changes what your judgment rests on.",
      nodes: {
        build: { label: "Build or change the prototype", to: "prototypes" },
        ask: { label: "Collect reactions in a survey", to: "limits-survey" },
        reflect: { label: "Feed the results into the documents", to: "feedback" },
      },
    },
    {
      kind: "md",
      md: `Surveys start from the **Survey** button on a prototype card. One prototype carries one survey.`,
    },
    {
      kind: "mockup",
      id: "survey-panel",
      caption: "The survey panel — generating questions, sharing the link, and the rollup in one place",
    },
    {
      kind: "steps",
      items: [
        "**Generate questions** — built from the validation hypotheses and feature list in the prototype spec, together with the evidence in the Envision artifacts.",
        "**Copy link** and send it to respondents. It opens without an account.",
        "As responses arrive, **Refresh** updates the rollup.",
        "**Synthesize results** — the AI reads the responses and lays out what was confirmed and what was contradicted.",
        "**Export CSV** gives you every individual response.",
      ],
    },
    { kind: "heading", id: "questions-source", text: "What the questions are built from" },
    {
      kind: "md",
      md: `The prototype spec is not enough on its own. Its problem statement and business value are a
one- or two-line summary, and the evidence behind that summary — how severe and how frequent each
pain point is, the workaround people use today, the industry and how the work is done now — exists
only in the Envision artifacts. A survey validates that evidence, so questions written from the
summary alone do not know what they are validating.

So **the pain-point analysis and the business-context document are read along with the spec when
they exist.** Without them the questions still get generated, but only from the spec summary — it is
worth checking that the Envision-stage documents are still there before you generate a survey.

Some specs carry no validation hypothesis (the Path B spec format, the one you get when you start
from use cases, has no such section). In that case the **top-priority** item in the pain-point
analysis becomes the hypothesis to validate.`,
    },
    { kind: "heading", id: "questions-philosophy", text: "What the questions ask, and what they leave out" },
    {
      kind: "md",
      md: `The questions are written on the assumption that **what the respondent saw was a demo**. Some
things are deliberately not asked:

- Performance and response time
- Security
- The accuracy of the data on screen
- When it could be adopted

The prototype was not built to satisfy any of those, so a low score on them tells you nothing you
can act on. Instead the questions ask, hypothetically, **whether the approach would be right if
this were adopted for real work**.

Feature questions include a **"did not use it / not applicable"** option. Without it, respondents
guess at features they never reached, and the rollup can no longer separate signal from noise. The
notice respondents see says the same thing — judge the direction, not the polish.`,
    },
    { kind: "heading", id: "limits-survey", text: "Limits" },
    {
      kind: "md",
      md: `| Item | Limit |
|---|---|
| Responses per survey | 1,000 |
| Length of one answer | 2,000 characters |

This is the only path that is used without signing in, so the limits are deliberately tight. At
the ceiling you see *The response limit has been reached* and the survey has to be closed.`,
    },
    { kind: "heading", id: "close", text: "Closing, and starting another" },
    {
      kind: "md",
      md: `**Close the survey** stops the link from accepting responses (*This survey is closed*).
After a substantial change to the prototype, **Create a new survey** is usually the right move —
it keeps reactions to the new screens out of the same bucket as the old ones.`,
    },
    { kind: "heading", id: "feedback", text: "Feeding it back into Discovery" },
    {
      kind: "md",
      md: `**Synthesize results** does not only put something on screen. It is saved as
\`validation-results.md\` in that prototype's document folder, so you can open it in
[Document Review](/manual#review) and the later stages read it too. **Each prototype gets its own
file** — build several prototypes, run a survey on each, and the results never overwrite one another.

The survey is not a feature that ends at a results screen. Attach the synthesis or the CSV
[to the workspace chat](/manual#attach) and ask it to "update the documents with these responses",
and that evidence lands in the documents.

If a hypothesis was contradicted, the point of the loop is not to stop at editing the document —
change the prototype too ([continue improving](/manual#complete-card)) and ask again.`,
    },
  ],
};
