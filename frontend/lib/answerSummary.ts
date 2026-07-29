// frontend/lib/answerSummary.ts — render submitted answers as chat text.
//
// The user bubble for an answer submission used to read "답변 제출", which left
// the transcript unreadable: scrolling back showed a question form's worth of
// decisions collapsed into two words, so the conversation lost its own context.
// This turns the same payload into the questions and the answers.
//
// It has to undo QuestionCard's value contract to do that. That contract packs
// four shapes into one string per question (see components/questions/
// QuestionCard.tsx): a bare letter ("A"), a letter with an addendum
// ("A: 부연"), comma-joined letters for multi-select ("A,C"), and free text
// that may itself start with something letter-shaped ("Broker: ..."). Only the
// first three are expandable, and telling them apart is the whole job here —
// splitting free text on ": " would mangle the answer we are trying to surface.
import type { QuestionFile, QuestionOption } from "@/lib/api/types";

/** Shown when a submission carries no non-blank answer, so the bubble is never
 *  empty. Matches the old wording, which is still right for this one case. */
const EMPTY = "답변 제출";

function letterText(options: QuestionOption[], letter: string): string | null {
  // is_other options are excluded deliberately: their letter is bookkeeping,
  // and their `text` is a placeholder, not the user's answer.
  const hit = options.find((o) => !o.is_other && o.letter === letter);
  return hit ? hit.text : null;
}

/** "A,C" -> "A. 자동 생성, C. 이력 관리", or null if any token is not a letter
 *  of a non-Other option (which means the value is free text, not a list). */
function expandLetterList(options: QuestionOption[], value: string): string | null {
  const parts = value.split(",").map((p) => p.trim());
  if (parts.length < 2) return null;
  const expanded: string[] = [];
  for (const p of parts) {
    const text = letterText(options, p);
    if (text === null) return null; // one bad token => treat the whole value as free text
    expanded.push(`${p}. ${text}`);
  }
  return expanded.join(", ");
}

/** The answer as a reader should see it: option text where the value names an
 *  option, the raw value where it is free text. */
function renderAnswer(options: QuestionOption[], value: string): string {
  const multi = expandLetterList(options, value);
  if (multi !== null) return multi;

  const whole = letterText(options, value);
  if (whole !== null) return `${value}. ${whole}`;

  // "A: 부연" — only when the head is a real non-Other letter. Anything else
  // (including "Broker: ...") is free text and passes through untouched.
  const idx = value.indexOf(": ");
  if (idx > 0) {
    const head = value.slice(0, idx);
    const text = letterText(options, head);
    if (text !== null) return `${head}. ${text} — ${value.slice(idx + 2)}`;
  }

  return value;
}

/**
 * Build the chat text for a submitted answer set.
 *
 * Ordered by the question list, not by `answers`' key order, so the bubble
 * reads in the same order the form did. Answers whose key matches no question
 * are appended rather than dropped — a stale form or a server-side renumber
 * should cost the reader a missing question line, not the answer itself.
 */
export function answerSummary(
  file: QuestionFile,
  answers: Record<string, string>,
): string {
  const blocks: string[] = [];
  const seen = new Set<string>();

  for (const q of file.questions) {
    const key = String(q.number);
    const value = answers[key];
    seen.add(key);
    if (value === undefined || value.trim() === "") continue;
    blocks.push(`Q${q.number}. ${q.text}\n→ ${renderAnswer(q.options, value)}`);
  }

  for (const [key, value] of Object.entries(answers)) {
    if (seen.has(key) || value.trim() === "") continue;
    blocks.push(`Q${key}.\n→ ${value}`);
  }

  return blocks.length > 0 ? blocks.join("\n\n") : EMPTY;
}
