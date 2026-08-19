# AI-PDS Web runtime integration contract

This file defines only the runtime boundary between the AI-PDS workflow and the web application. Follow the workspace `CLAUDE.md` and its referenced rule files
for the workflow, stage order, terminology, document formats, and project language. Where this contract overrides the upstream rules, the override applies only because the web runtime handles that interaction differently.

## Required artifacts

- Keep `aiplc-docs/aiplc-state.md` current as required by `common/workflow-changes.md` and each stage's "Update State Tracking" step. Preserve the `- **Current Stage**: <name>` line and the `## Stage Progress` checklist; AI-PDS Web derives the stage UI from this file.
- After creating or updating a discovery document, call `submit_document`. Write the file first; the tool rejects a missing or empty file.
- Append audit entries to `audit.md` with Edit. Do not replace the existing audit history with a partial Write.

## Turn-ending writes

Writing either of these files hands control to the user and immediately ends the
turn:

| File | Handoff |
|---|---|
| A question file containing `[Answer]:` tags | Shows the question form |
| `build-instructions.md` | Shows the prototype card |

Any tool calls placed after that write are discarded. Before the turn-ending write, provide the user-facing message and complete every required `submit_document` call, `audit.md` Edit, and `aiplc-state.md` update. This keeps the ordering already required by `core-workflow.md`.

## Question files

The question file is the web form.
Follow `aws-aiplc-rule-details/common/question-format-guide.md` with numbered questions, lettered options, and an `[Answer]:` line under each question.

- **AskUserQuestion is not available.** Write the complete question file under `aiplc-docs/`; AI-PDS Web renders it without the tool's question or option limits.
- **Use `## Question <number>` headings.** Keep the number stable. Answers are matched by question **number**.
- **Place file-wide preamble text above the first `##` heading.** A preface put under a `##` heading becomes the *first question's* context. Put context for one question under a `##` heading immediately before that question.
- **AI-PDS Web fills the `[Answer]:` tags. Do not write them yourself.** Read them when continuing after the user submits the form.
- **Write the question file last.** Do not continue with more tools after the write.
- **Do not restate the questions in chat.** Explain why the question round is needed before writing the file.
- **Do not send the user to edit the file or wait for a separate "done".** The web form is the editing surface, and submitting it is confirmation. If an expected answer is still missing, present it again through a question file.
- **Keep the audit trail.** Record the resulting decisions in `audit.md`.

## Prototype handoff

Discovery writes specifications; the Prototypes tab builds and runs them.

- **You write only under `aiplc-docs/`.** This boundary is enforced, not trusted: hooks reject writes elsewhere and reject build or serve commands.
- **Use the path defined by the applicable workflow path.**
  - Path A.1:
    `aiplc-docs/discovery/prototype/prototype-spec.md`
  - Path B:
    `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`
  Do not produce a slugged duplicate for Path A.1. The document that governs a path beats the overview diagram. For Path B, the slug must equal the directory name and contain lowercase letters, digits and hyphens only.
- **Do not execute the prototype build instructions.** Do not create executable source files, install packages, start processes, choose a port, or report a local server URL from Discovery.
- **End Path A.1 build preparation at `build-instructions.md`.** Explain the handoff to the user before writing it; that write is the handoff, and it ends your turn. Steps that require a running prototype are not abandoned; they are deferred until the user builds it in the Prototypes tab.
- **The model and credentials are already provisioned. Never ask for them.** Do not offer provider selection, do not write a model ID or port into the specification, and do not inspect the environment for credentials.
- **The hosted runtime is Node.** Describe the agent's behavior rather than interpreter setup or a second backend process. When an agentic specification must identify the implementation SDK, use the server-side TypeScript package `@strands-agents/sdk`.

## Depth of what you write

<!-- depth-bar-items: derive, prose, unknowns, brackets, defaults -->

Apply this quality bar to every document under `aiplc-docs/`:

- **Derive, do not only transcribe.** Add a calculation, relationship, or product implication where the evidence supports it.
- **Use prose for meaning.** Lists can carry facts, but explain what the facts imply for the decision.
- **State material unknowns.** Identify what a later stage still needs and why it matters.
- **Treat bracketed template guidance as a checklist.** Cover each requested item, replace example rows, and leave no placeholder answers.
- **Use explicit assumptions when evidence is missing.** Never present an assumption as customer evidence.

Be concise, avoid duplication, and do not invent evidence.

## Keep the conversation visible

- Include user-facing conversational text in every turn; never return tool activity alone.
- On a question or prototype-handoff turn, say what was completed and what the user should do next before the turn-ending write.
- Use the language specified by the workspace `CLAUDE.md`.
