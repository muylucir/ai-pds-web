# Language convention (a premise for this entire document)

**Conduct all conversation, document writing, and Q&A in English.**

The workflow and the document formats under `aws-aiplc-rule-details/` are already
written in English, so follow them as they stand. There is nothing to translate.

Keep file names, paths, tool names, and code identifiers exactly as the rules
spell them — `submit_document` parses some of them, and a renamed path means the
document never reaches the review screen.

## Calibrate length against a bar, not by feel

<!-- depth-bar-language-clause -->

**Do not calibrate length by feel.** The same content costs a different number of
tokens in different languages, so following a sense of "about the right length"
makes a document's depth track **the language** rather than the task. Measured on
2026-08-13: running one stage in each language produced the same section and question
counts but diverging per-field density, and both documents passed the completeness
check.

**How deep to write** does not depend on the language, so it does not live in this
file. The shared config `CLAUDE.md` (its "Depth of what you write" section) carries
that bar and applies unchanged whichever language you write in — read it with the
same weight as this document's language convention.

---
