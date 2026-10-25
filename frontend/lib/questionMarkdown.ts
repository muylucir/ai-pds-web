// Adjusts question file markdown **only right before rendering**. The file itself is never touched.
//
// **Why this is needed (the 2026-08-16 defect).** The backend writes submitted answers
// into the question file's `[Answer]:` slot (backend/aipds/agent/question_file_answers.py),
// yet not a single answer appeared on screen. The file did contain `[Answer]: A`.
//
// The cause is CommonMark. A `[Answer]: A` at the start of a line is a **link
// reference definition**, and the renderer **produces no output at all** for that line.
// Worse, the moment one definition exists, every other `[Answer]` in the document turns
// into a **link** pointing at that definition — the evidence is the observed screen,
// where option text rendered as
// `X) Other (please describe after Answer: tag below)`, with the brackets gone and
// "Answer" underlined.
//
// **Why not change the file format.** The upstream ai-plc rules instruct reading the
// `[Answer]:` tag (aws-aiplc-rule-details/common/question-format-guide.md), and our parser
// uses that regex too (backend/aipds/parsers/questions.py). Changing the file would break
// that compatibility. So we change it **only right before display**.
//
// **Why not render through the parser.** A question file contains a lot of body text
// besides the questions — the required-areas table, stage metadata, the follow-up
// questions section. The `QuestionFile` model holds only preamble + questions, so
// drawing from it would lose all of that context.

//: A single `[Answer]:` line at the start of a line. `^` is required — an `[Answer]:` in
//: the middle of a sentence is plain text, not a link definition, and touching it would bold the guidance text.
const ANSWER_LINE = /^\[Answer\]:[ \t]*(.*)$/gm;

/**
 * Adjusts markdown right before rendering. `[Answer]: A` → `**\[Answer\]:** A`.
 *
 * Escaping the brackets is the key: without escaping, the line is still read as a
 * link definition and the same problem remains. Wrapping in bold is because the answer
 * must stand out in the document — this is where the user checks "was my answer recorded?".
 *
 * An empty slot (nothing after `[Answer]:`) is shown as is. The fact that a question
 * is not yet answered is itself information; hiding it would make "I answered but it
 * doesn't show" indistinguishable from "it hasn't been asked yet".
 */
export function revealAnswerTags(markdown: string): string {
  return markdown.replace(ANSWER_LINE, (_line, value: string) => {
    const answer = value.trim();
    // Keep the hard break `breakAnswerLines` put on a multi-line answer's first line — trimming
    // it would glue that line to the next one again.
    const hardBreak = answer !== "" && / {2}$/.test(value) ? "  " : "";
    return answer === "" ? "**\\[Answer\\]:**" : `**\\[Answer\\]:** ${answer}${hardBreak}`;
  });
}

//: Where an answer block ends — the **same rule** as `_ANSWER_STOP` in
//: backend/aipds/parsers/questions.py: a heading, a thematic break, a code fence, or another tag,
//: at column 0. If the two drift, the screen breaks lines the parser does not count as the answer.
const ANSWER_STOP = /^(?:#{1,6}\s|(?:-{3,}|\*{3,}|_{3,})\s*$|`{3,}|~{3,}|\[Answer\]:)/;
const ANSWER_TAG = /^\[Answer\]:[ \t]*(.*)$/;

/**
 * Appends a hard break to each line of a multi-line answer, so it shows line by line.
 *
 * **Why (2026-10-25).** The card's elaboration and Other fields are textareas, and the backend
 * writes such an answer as a block — the tag line plus indented continuation lines
 * (`serialize_answers`). Those lines form one CommonMark paragraph, so the user's line breaks
 * rendered as spaces: the context they typed line by line read as one run-on sentence. Blank
 * lines already separate paragraphs, so only the lines inside a paragraph need this — but a
 * hard break on a paragraph's last line is discarded, so every non-blank line gets one (the
 * same reasoning as `breakOptionLines`).
 *
 * An empty tag opens no block — the backend reads nothing after it as the answer either.
 */
export function breakAnswerLines(markdown: string): string {
  let inAnswer = false;
  return markdown
    .split("\n")
    .map((line) => {
      const cr = line.endsWith("\r") ? "\r" : "";
      const body = cr ? line.slice(0, -1) : line;
      const tag = ANSWER_TAG.exec(body);
      if (tag) inAnswer = tag[1].trim() !== "";
      else if (inAnswer && ANSWER_STOP.test(body)) inAnswer = false;
      return inAnswer && body.trim() !== "" ? body.replace(/[ \t]*$/, "  ") + cr : line;
    })
    .join("\n");
}


//: A single option line. This must be the **same set** as the backend parser
//: (`_OPTION` in backend/aipds/parsers/questions.py: `^([A-J]|X)\)\s+`).
//: Widening only one side makes a line look like an option on screen but fail to parse, or vice versa.
const OPTION_LINE = /^([A-JX]\)[ \t]+.*?)[ \t]*$/gm;

/**
 * Appends a markdown hard break (two spaces) to the end of each option line.
 *
 * **Why this is needed.** The upstream format separates options only by line breaks with
 * no trailing spaces (question-format-guide.md). In CommonMark, consecutive lines with no
 * blank line between them form one paragraph, and those line breaks render **as spaces**,
 * so `A) … B) … C) …` ran together on a single line and was unreadable. `A)` is not a list
 * marker either — ordered lists accept only a number + `.`/`)`, so letter options are
 * plain text.
 *
 * Why not convert to a bullet list (`- A) …`): it adds indentation and bullets and
 * changes the document's shape. A hard break produces exactly the intended "one per line".
 *
 * A hard break on the last line of a paragraph is simply discarded by the renderer, so it is harmless.
 */
export function breakOptionLines(markdown: string): string {
  return markdown.replace(OPTION_LINE, "$1  ");
}

/**
 * The bundle of transforms applied before putting a question file on screen.
 *
 * `breakAnswerLines` goes **first**: it finds answer blocks by the raw `[Answer]:` tag, which
 * `revealAnswerTags` rewrites. The other two look at different lines, so their order does not matter.
 */
export function prepareQuestionMarkdown(markdown: string): string {
  return breakOptionLines(revealAnswerTags(breakAnswerLines(markdown)));
}
