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

This file governs only the touchpoints with the Pathfinder web UI. For the
Discovery workflow itself, follow the `CLAUDE.md` in the working directory
(the AI-PLC core workflow), including its language convention.

- When you ask the user a multiple-choice question, you MUST use the
  **AskUserQuestion** tool. Keep writing the question files
  (`aiplc-docs/**-questions.md`) as a record, but deliver the questions
  themselves through the tool only.
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

## Question files: answers are written back for you (overrides the upstream rules)

The questions themselves reach the user through **AskUserQuestion**, and their
answers come back through that same tool call. `aws-aiplc-rule-details/common/question-format-guide.md`
applies to the file — its `[Answer]:` tags **do get filled in** — with these
differences in *how*:

- **Pathfinder fills the `[Answer]:` tags, not you.** The moment the user
  submits the form, the backend writes each answer into the matching question in
  `aiplc-docs/**/*-questions.md`. Read those tags freely (that is the point —
  `common/session-continuity.md` has you re-read the stage's question file on
  resume). **Do not write them yourself**: two writers on one line produce
  conflicts, and your copy would be the stale one.
- **Matching is by question text, not by question number.** So the question
  wording you put in the file must be the *same sentence* you pass to
  AskUserQuestion. If you reword it in the tool call, the answer has nothing to
  match and the tag stays empty.
- **Do not apply its "Missing Answers" handling** by sending the user to the
  file. They cannot edit it from the UI — the form in the right-hand panel is
  the only way in. If a tag you expected is still empty, ask that question again
  through AskUserQuestion.
- **Do not wait for the user to say "done"** (its Step 3, "Wait for
  Confirmation"). The AskUserQuestion round-trip *is* the confirmation: your
  turn resumes the moment they submit the form, with their answers attached.
- **Keep logging answers in `audit.md` as well.** The question file carries the
  decision; `audit.md` carries the audit trail the workshop record relies on.

One consequence worth knowing, so you do not try to "fix" it: AskUserQuestion
takes **at most 4 questions with at most 4 options each**, and those are hard
schema limits. A stage whose rules list more question areas than that will
deliver them across several calls, so the file and the forms will not line up
one-to-one — round 2 starts numbering at 1 again. Text matching is what carries
the answers to the right rows across that split, which is why the wording has to
stay identical. Do not trim the file down to match the tool, and do not try to
cram extra questions into one call.

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
- **The spec goes to `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`,
  on every path — including Path A.1.** Follow
  `aws-aiplc-rule-details/discovery/prototype-md-format.md` for its contents.
  `aws-aiplc-rule-details/discovery/prototype-validation.md` tells you to write
  only `aiplc-docs/discovery/prototype/prototype-spec.md` and stop; that is an
  upstream gap, not permission to skip the slugged file. (The same upstream
  document lists `Existing PROTOTYPE-*.md` as the build entry point, and
  `prototype-context-generation.md` does produce the slugged path — Path A.1 is
  the one flow that forgets it.)
  - **The slug must match the directory name exactly.** The Prototypes tab finds
    cards with a regex whose directory capture is back-referenced in the
    filename, so `prototypes/foo/PROTOTYPE-bar.md` lists nothing. That slug is
    also the id for building, hosting, surveys and deletion.
  - Derive it as kebab-case from the use-case name and sanitize it the way
    `prototype-context-generation.md` requires: lowercase letters, digits and
    hyphens only; reject anything containing `/`, `\` or `..`.
  - **`PROTOTYPE-{slug}.md` is the artifact of record.** Nothing in Pathfinder
    reads `prototype-spec.md`. If Path A.1 already produced one, do not maintain
    two full copies — they drift, and the drift shows up as a content
    difference with no error. Keep the detail in `PROTOTYPE-{slug}.md`.
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
- After saving the spec, **end your turn by telling the user to build it from
  the Prototypes tab.** Do not talk as if a build is about to start in the
  Discovery chat.

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
- On a turn that delivers questions via AskUserQuestion, explain in one
  sentence why the question is needed before the form appears.

Write this conversational text in the language the workspace `CLAUDE.md`
specifies — that is where the project's language is defined.
