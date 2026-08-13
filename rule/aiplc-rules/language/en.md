# Language convention (a premise for this entire document)

**Conduct all conversation, document writing, and Q&A in English.**

The workflow and the document formats under `aws-aiplc-rule-details/` are already
written in English, so follow them as they stand. There is nothing to translate.

Keep file names, paths, tool names, and code identifiers exactly as the rules
spell them — `submit_document` parses some of them, and a renamed path means the
document never reaches the review screen.

## How deep to write each field (a volume bar)

<!-- depth-bar-items: brackets, paragraph-fields, faq-answers, tables, defaults -->

**Do not calibrate length by feel.** The same content costs a different number of
tokens in different languages, so following a sense of "about the right length"
makes a document's depth track the language rather than the task. Measured on
2026-08-13: running one stage in each language produced the same section and
question counts, but the per-field density diverged. So calibrate against the bar
below rather than by feel.

- **Bracketed guidance is a checklist.** Cover every item the sentence inside
  `[...]` asks for. If it asks for three things, write all three.
- **Fill a prose field with at least three concrete facts** — a number, a piece of
  evidence, a source, or a fact carried over from the earlier analysis. Do not
  stop at one sentence.
- **An FAQ answer is at least two sentences**: the answer and what it rests on.
  Do not stop at "yes/no" or a one-line summary.
- **Fill in the table rows.** Do not leave a single example row behind — carry
  over every item the analysis has.
- **Where you have no evidence, write an intelligent default and state what you
  assumed.** Leaving a blank or a leftover `[Answer]` is the worst outcome.

None of this is licence to pad. Do not say the same thing twice or invent
evidence you do not have — cover the items you were asked for, with the numbers
and reasoning behind them.

---
