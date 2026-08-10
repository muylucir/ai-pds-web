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

## Question files: a record, not the answer sheet (overrides the upstream rules)

The questions themselves reach the user through **AskUserQuestion**, and their
answers come back through that same tool call. The markdown file is a record of
what was asked — nothing reads answers back out of it. So its `[Answer]:` tags
**stay empty by design**, and `aws-aiplc-rule-details/common/question-format-guide.md`
does not apply to them:

- **Do not apply its "Missing Answers" handling** to these files. An empty
  `[Answer]:` is the expected end state, not an oversight. Telling the user to
  "provide an answer for Question X" sends them to a file they cannot edit from
  the UI — the form in the right-hand panel is the only way in.
- **Do not wait for the user to say "done"** (its Step 3, "Wait for
  Confirmation"). The AskUserQuestion round-trip *is* the confirmation: your
  turn resumes the moment they submit the form, with their answers attached.
- **The record of truth for answers is `audit.md`.** Keep logging them there
  exactly as the stage rules require — that is what later stages and the
  workshop record rely on, not the question file.

One consequence worth knowing, so you do not try to "fix" it: AskUserQuestion
takes **at most 4 questions with at most 4 options each**, and those are hard
schema limits. A stage whose rules list more question areas than that will
deliver them across several calls, so the file and the forms will not line up
one-to-one. That is expected. Do not trim the file down to match the tool, and
do not try to cram extra questions into one call.

## Prototypes: write the spec, do not build (overrides the upstream rules)

In Pathfinder, **building and running prototypes is the Prototypes tab's job**.
Only the dedicated hosting layer (`ProtoHost`) can allocate a port and register
with the preview proxy, so a server you start here appears on no screen and
opens from no preview link. Discovery's role ends at **writing the spec**.

- Follow `aws-aiplc-rule-details/discovery/prototype-md-format.md` and write
  `aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`. That path
  convention is what the Prototypes tab lists cards from — deviate and no card
  appears.
- **Do not perform** the build steps in
  `aws-aiplc-rule-details/discovery/prototype-building.md`. Those rules were
  written for the upstream workshop, where a human runs everything locally.
  Specifically, do not:
  - run build/run commands such as `npm install` / `npm run build` /
    `npm run dev`
  - start prototype subprocesses (the credential-isolation guidance therefore
    has nothing to apply to)
  - report progress or completion like "Deploying to…" or
    "Running at http://localhost:{port}"
- **Do not choose a port.** The upstream rules' `Port: {3000 + X}` and the
  spec template's `Port` field are void in Pathfinder — hosting assigns the port
  at build time. A port written into the spec will disagree with the assigned
  one and mislead the user.
- After saving the spec, **end your turn by telling the user to build it from
  the Prototypes tab.** Do not talk as if a build is about to start in the
  Discovery chat.

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
