// Shared markdown renderer — only for AI messages, the document viewer, and question preambles.
// User input is never sent here (it stays plain text — spec §3).
// Raw HTML is not rendered, per react-markdown's default behavior (XSS).
//
// **`[Answer]:` lines are adjusted here (the 2026-08-16 defect).** The backend writes
// answers into the question file's `[Answer]:` slot, yet nothing appeared on screen —
// because that line is a CommonMark link reference definition, the renderer produces no
// output for it (full rationale in the lib/questionMarkdown.ts header).
//
// Why hook it into the renderer once instead of at each of the 5 call sites: question files
// are opened everywhere — the workspace panel, document review, the canvas, the parse-failure
// fallback. Scattered, a sixth call site would silently miss it when added, and that failure
// looks like "the answer was not recorded" — which is exactly how it looked this time. For
// the same reason, option lines (`A) …`) get a hard break — without it, the options run
// together on one line. Both transforms are idempotent.
//
// **Both renderers are `memo`, and the plugin and component tables are module constants.**
// Markdown parsing is the most expensive render work in this app, and during streaming the
// screen re-renders every frame. Without memo, every bubble in the past conversation and the
// long document in the document panel would be re-parsed from scratch each time. Building
// the tables inside render would yield a new object every time, defeating ReactMarkdown's comparison too.
import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { prepareQuestionMarkdown } from "@/lib/questionMarkdown";

const REMARK_PLUGINS = [remarkGfm];

const BLOCK_COMPONENTS: Components = {
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  ),
};

export const Markdown = memo(function Markdown(
  { text, className }: { text: string; className?: string },
) {
  return (
    <div className={"prose prose-sm prose-slate max-w-none [&_table]:text-xs " + (className ?? "")}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={BLOCK_COMPONENTS}>
        {prepareQuestionMarkdown(text)}
      </ReactMarkdown>
    </div>
  );
});

// Inline-only renderer — question sentences and option labels.
//
// **Why the `Markdown` above cannot be used.** Options are a flex layout inside a `<label>`,
// and the renderer above wraps everything in `<div className="prose …">` and draws
// paragraphs as `<p>`. That puts block elements and prose margins inside the label and
// breaks alignment with the selection control.
//
// **Why not skip rendering altogether (observed 2026-08-18).** Options in question files
// really are written in markdown — `A) **「조정 브리프」** — 조정 건 하나를 열면 …`. Drawn as
// plain text, the `**` stays on screen. The same goes for question sentences
// (`제품명을 무엇으로 할까요? *(보도자료의 Heading)*`).
//
// Block elements are **discarded on purpose**: headings, lists, or tables in an option label
// are invalid input to begin with, and rendering them breaks the card. `unwrapDisallowed`
// keeps the content — it strips only the tags and loses no text.
//
// `prepareQuestionMarkdown` is not applied. That transform is for the **full file text**:
// it rescues a line-leading `[Answer]:` from being a link definition and adds hard breaks to
// option lines. What arrives here is a single fragment the parser has already cut out, so
// neither transform has a target, and the latter would actually create line breaks inside the label.
const BLOCK_ELEMENTS = ["h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
                        "table", "thead", "tbody", "tr", "th", "td",
                        "blockquote", "pre", "hr", "img"];

const INLINE_COMPONENTS: Components = {
  // Do not wrap paragraphs — let the content flow inline inside labels/headings.
  p: ({ children }) => <>{children}</>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className="underline">{children}</a>
  ),
};

export const InlineMarkdown = memo(function InlineMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      components={INLINE_COMPONENTS}
      disallowedElements={BLOCK_ELEMENTS}
      unwrapDisallowed
    >
      {text}
    </ReactMarkdown>
  );
});
