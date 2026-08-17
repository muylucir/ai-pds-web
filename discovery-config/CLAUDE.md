# Pathfinder integration contract (UI touchpoints — mandatory)

<!--
WHY THIS FILE IS IN ENGLISH, and why that is not a language directive.

This file lives in the shared CLAUDE_CONFIG_DIR ("user" in
setting_sources=["user", "project"]), so EVERY project reads it regardless of
the language its user chose — it cannot carry a per-project language. Spec
2026-08-03-bilingual-ko-en §3 concluded from that only that the language
DIRECTIVE had to be removed from here. That was not enough: on 2026-08-04 an
English project's chat still ran in Korean, because this whole file was Korean
prose. **The language a document is written in is itself a language signal**,
even when the document never says which language to use.

So the rule for this file is stronger than "no language directive": it must be
language-NEUTRAL, and for a document the model reads that means the upstream
rules' own language, English. The per-project language flows through the
workspace CLAUDE.md instead (rule/aiplc-rules/language/{ko,en}.md, assembled by
agent/workspace_rules.py) — that is the "project" level, the only level that can
vary per project. It stays the single source of truth for which language to
speak; nothing here competes with it, which is what commit 7f33652's failure was
about.

Keep this file in English when editing it. backend/tests/test_workspace_rules.py
pins the invariant.
-->

Write non-ASCII text (Korean, etc.) in tool-call parameters (JSON) as literal
UTF-8 — never as `\uXXXX` escapes. This is an encoding rule, not a language
rule: it says nothing about WHICH language to write in, only that whatever
language you write must reach the tool as real characters.

**That encoding rule applies to every tool call you make, in any language, and
nothing below narrows it.** It is repeated at the top of the working-directory
`CLAUDE.md` for the same reason. Hand-spelling `\uXXXX` mis-spells some
codepoints, and a mis-spelled one decodes to a different, valid-looking syllable
— the user then reads a nonsense question, and their answer cannot be matched
back to the question file it belongs to.

Apart from that rule, this file governs only the touchpoints with the Pathfinder
web UI. For the Discovery workflow itself, follow the `CLAUDE.md` in the working
directory (the AI-PLC core workflow), including its language convention.

- When you ask the user a multiple-choice question, you ask it by **writing the
  question file** (`aiplc-docs/**`, with `[Answer]:` tags). Pathfinder reads that
  file and shows the questions as written. The **AskUserQuestion** tool is not
  available — see the question-files section below.
- Call the **report_stage** tool whenever you start or complete a stage. That
  tool updates `aiplc-state.md` for you, so you do not need to write the state
  file yourself.
- Call the **submit_document** tool whenever you create or update a
  discovery-document. **Order matters: save the file first, then call
  submit_document.** If the file is missing or empty the tool refuses the
  declaration and tells you why — which means: go back and save the file.
- To add an entry to `audit.md`, append with **Edit**. **Write replaces the
  entire file** — calling Write with only the new entry destroys the whole
  audit record.

## Question files ARE the question form (overrides the upstream rules)

**Write the question file. That is how you ask.** `aws-aiplc-rule-details/common/question-format-guide.md`
applies as written — numbered questions, lettered options, an `[Answer]:` line
under each. Pathfinder reads the file the moment you finish writing it and shows
those questions to the user **exactly as you wrote them**, then fills the
`[Answer]:` tags with their answers.

- **AskUserQuestion is not available.** Calling it is refused, and the refusal
  points you back here. There is nothing to route through a tool: the file is
  the form.
- **Write the questions in full.** There is no 4-question or 4-option ceiling to
  work around, and nothing has to be shortened or split across rounds. A stage
  whose rules list nine question areas gets one file with nine questions and one
  screen.
- **Put the background where the format guide puts it.** Prose above a question —
  under a `##` heading, before the question's own heading — is shown to the user
  as that question's context, markdown and tables included. This is where the
  "why am I being asked this" belongs: an ambiguity you are resolving, the table
  a confirmation gate refers to as "the items above". A question that reads as
  unanswerable on its own is a question missing its context.
- **Pathfinder fills the `[Answer]:` tags, not you.** They are matched by
  question **number**, so numbering is what has to be stable — not wording.
  Read the tags freely (that is the point — `common/session-continuity.md` has
  you re-read the stage's question file on resume). **Do not write them
  yourself**: two writers on one line produce conflicts, and your copy would be
  the stale one.
- **Your turn ends when you finish writing the file.** Do not keep working, do
  not restate the questions in chat, and do not announce that you are about to
  ask. The answers arrive in the file and you continue on the next turn.
- **Do not apply its "Missing Answers" handling** by sending the user to the
  file. They cannot edit it directly — the form in the right-hand panel is the
  only way in. If a tag you expected is still empty, write the question again
  (a new file, or new numbered questions appended to the same one).
- **Do not wait for the user to say "done"** (its Step 3, "Wait for
  Confirmation"). Submitting the form *is* the confirmation.
- **Keep logging answers in `audit.md` as well.** The question file carries the
  decision; `audit.md` carries the audit trail the workshop record relies on.

## Prototypes: write the spec, do not build (overrides the upstream rules)

In Pathfinder, **building and running prototypes is the Prototypes tab's job**.
Only the dedicated hosting layer (`ProtoHost`) can allocate a port and register
with the preview proxy, so a server you start here appears on no screen and
opens from no preview link. Discovery's role ends at **writing the spec**.

- **You write only under `aiplc-docs/`. Nothing else, ever.** This is the rule,
  not a list of forbidden commands. A prototype is not "not built" because you
  skipped `npm install` — it is not built because no source file exists outside
  `aiplc-docs/`. A single self-contained `index.html` needs no package manager,
  no port and no network, and it is still a build. So is a `.py`, a `.jsx`, a
  `.css`. If you are about to create a file that a browser or a runtime would
  execute, you are outside your scope.
  This one is **enforced, not trusted**: a hook refuses `Write`/`Edit` outside
  `aiplc-docs/`, and refuses shell commands that build, serve, or redirect
  output to a path outside it. The refusal names the path or command and points
  you back here — read it and write the spec instead of retrying with a
  different path.
- **Write the spec where your own stage's rules say, and do not invent a second
  copy.** The two upstream layouts are both correct and Pathfinder reads both:
  - Path A.1 (Envision-derived, a single prototype) →
    `aiplc-docs/discovery/prototype/prototype-spec.md`, exactly as
    `aws-aiplc-rule-details/discovery/prototype-validation.md` lists in its
    deliverables. There is no slug, because there is nothing to distinguish.
  - Path B (use-case prioritization, three prototypes) →
    `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`, per
    `prototype-context-generation.md` and `prototype-md-format.md`.
  The directory tree in `core-workflow.md` marks `prototypes/` as "All paths",
  which reads as if A.1 owed a slugged file too. It does not: the document that
  governs a path beats the overview diagram. **Do not produce a slugged
  duplicate of a single-prototype spec** — two full copies drift, and the drift
  shows up as a content difference with no error.
  - When a path DOES use slugs, the slug must equal the directory name: the
    Prototypes tab matches with a regex whose directory capture is
    back-referenced in the filename, so `prototypes/foo/PROTOTYPE-bar.md` lists
    nothing. Derive it as kebab-case and sanitize it the way
    `prototype-context-generation.md` requires: lowercase letters, digits and
    hyphens only; reject anything containing `/`, `\` or `..`.
- **Do not perform** the build steps in
  `aws-aiplc-rule-details/discovery/prototype-building.md`. Those rules were
  written for the upstream workshop, where a human runs everything locally.
  Beyond the scope rule above, that also means: do not start prototype
  subprocesses (the credential-isolation guidance therefore has nothing to
  apply to), and do not report progress or completion like "Deploying to…",
  "Running at http://localhost:{port}", or instructions for the user to serve
  the files themselves.
- **Do not choose a port.** The upstream rules' `Port: {3000 + X}` and the
  spec template's `Port` field are void in Pathfinder — hosting assigns the port
  at build time. A port written into the spec will disagree with the assigned
  one and mislead the user.
- **Where Path A.1 stops, and how it continues.**
  `aws-aiplc-rule-details/discovery/prototype-validation.md` runs Step 1 → Step
  11, and its Step 3 is "Build Prototype". In Pathfinder Step 3 ends one document
  earlier:
  - Write `build-instructions.md` as that step specifies, then call
    **handoff_prototype** with the prototype's slug and **end your turn**,
    telling the user to build it from the Prototypes tab. That tool is the
    replacement for building — it is what makes the card actionable there.
  - **Steps 4-6 (Iterate, Validation Setup, Feedback Synthesis) are not
    abandoned; they are deferred.** They all presume a running prototype, which
    only exists after the user builds. Resume them when the user comes back with
    a built prototype or survey results — Pathfinder runs the survey, so Step 5's
    questionnaire is generated for you.
  - Do not talk as if a build is about to start in the Discovery chat.
- **The model and the credentials are already provisioned. Never ask for them.**
  `aws-aiplc-rule-details/common/llm-model-configuration.md` has the agent pick a
  provider, hard-codes model IDs (its own three disagree with each other) and
  treats API keys as a prerequisite. All of that is void here: the project was
  created with a model, every build inherits it, and the runtime holds the
  credentials.
  - Do not offer a provider/model choice, do not write a model ID into the spec
    or the build instructions, and do not check environment variables for API
    keys. A model ID you write will disagree with the one the build actually
    uses, and asking for a key blocks the user on something they cannot give.
  - This is the same reason the `Port` field is void (above): anything the
    hosting layer assigns at build time must not be guessed in the spec.
- **The prototype runtime is Node, and "agent" means the TypeScript SDK.**
  Hosting runs the npm lifecycle and nothing else (`npm install` →
  `npm run build` → `npm run start`). A Python prototype is therefore never
  started: the build reports success and the preview opens as a blank page, with
  no error anywhere. `aws-aiplc-rule-details/discovery/prototype-building.md`
  reaches for the Python Strands SDK and a Python web framework because upstream
  assumes a laptop where a human runs both processes by hand — that assumption
  does not hold here.
  - **Do not write an interpreter setup, a package-manager command, or a second
    backend process into the spec or the build instructions.** Describe what the
    agent must *do* — its tools, its inputs, what it decides, what it must not
    decide — and leave the stack to the build agent, which has its own rules for
    it.
  - When the spec genuinely has to name the stack (an agentic use case whose
    feasibility argument depends on it), name **`@strands-agents/sdk`** — the
    Strands Agents *TypeScript* SDK, running server-side. It reaches Bedrock
    through the credentials the build already holds, which is why there is still
    nothing to ask the user for.
  - This is also what keeps the spec portable in the direction that matters. A
    team that picks up a `PROTOTYPE-*.md` in an IDE can still build it with the
    Python SDK, because the spec describes the agent rather than the install. A
    spec that hard-codes one runtime is the one that does not travel.

## Depth of what you write (overrides the upstream rules)

<!-- depth-bar-items: derive, prose, unknowns, brackets, defaults -->

The upstream rules specify document *structure* thoroughly and say almost nothing
about depth. Several steps give you mandatory content areas and no template at all —
`aws-aiplc-rule-details/discovery/envision.md` Step 0.2 (business context) is the
clearest — and there the cheapest compliant output is a bulleted transcription of
what the user just said.

Measured on 2026-08-13 across two sessions given the same input in different
languages: the same step produced 20% prose in one and 58% in the other, 484 versus
3,823 characters of it. **Both passed the mandatory-area completeness check.** So
completeness is not the bar. The bar is below, and it applies to every document you
write under `aiplc-docs/`, template or no template.

- **Derive, do not transcribe.** For each area you cover, add at least one thing the
  user did not say: a figure computed from the ones they gave (4 hours a week is
  ~208 hours a year, about 26 working days), a ratio between two of them (1,200 users
  across 80 companies is ~15 per company), or what a fact means for a product
  decision. A document that only reorganizes the input under headings has not done
  the analysis the step asks for.
- **Lists carry facts; prose carries what they mean.** A section that is only bullets
  is a transcription. Give every section at least one paragraph of prose and put the
  reasoning there.
- **State what is still unknown.** Close the document with what the next stage needs
  answered and why each one matters — market sizing, volumes, alternatives already
  evaluated, the cost of the pain beyond the time it consumes. Omitting this makes a
  document look finished when it is not.
- **Where a template exists, its bracketed guidance is a checklist.** Cover every
  item the sentence inside `[...]` asks for; if it asks for three things, write all
  three. Fill in the table rows instead of leaving the example row behind. An FAQ
  answer is at least two sentences — the answer and what it rests on.
- **Where you have no evidence, write an intelligent default and say what you
  assumed.** A blank or a leftover `[Answer]` is the worst outcome.

None of this is licence to pad. Do not say the same thing twice, and do not invent
evidence you do not have.

**Why this bar lives here and not in the workspace `CLAUDE.md`.** It is
language-neutral — it says how deep to write, never which language to write in — so
keeping it here means one copy instead of a Korean one and an English one that drift
apart. The language-dependent half of the problem (a sense of "about the right
length" is not a stable target when tokens cost differently per language) does live
in the workspace `CLAUDE.md`, next to the language convention it belongs to.

## Keep the conversation visible (this must reach the user's screen)

- Never end a turn with tool calls alone. **Every turn must include
  conversational text for the user** — before calling tools, say in a sentence
  or two what you are doing and why; when ending the turn, summarize what you
  did and what you are asking or expecting next. The chat bubble is filled from
  that text. A turn with tool calls and no text renders as an empty bubble, so
  it is forbidden.
- On a turn that delivers questions, explain in one sentence why they are needed
  before the form appears. Per-question background belongs in the file (above the
  question's heading) — this line is about the round as a whole.

Write this conversational text in the language the workspace `CLAUDE.md`
specifies — that is where the project's language is defined.
