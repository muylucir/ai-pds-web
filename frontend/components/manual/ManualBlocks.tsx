"use client";
// frontend/components/manual/ManualBlocks.tsx — content blocks → screen.
//
// Prose is rendered by the existing components/Markdown.tsx (react-markdown + gfm).
// What is here is only **what markdown cannot express**: subheadings with anchors,
// callout boxes, commands with copy buttons, screen mockups, and flow diagrams.
import { useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/Markdown";
import type { ManualBlock } from "@/content/manual";
import type { Dict } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

import { Diagram, DiagramHint } from "./diagrams";
import { MOCKUPS } from "./mockups";

const CALLOUT: Record<string, { box: string; label: keyof Dict }> = {
  note: { box: "border-sky-200 bg-sky-50 text-sky-900", label: "manual.calloutNote" },
  warn: { box: "border-rose-200 bg-rose-50 text-rose-900", label: "manual.calloutWarn" },
  tip: { box: "border-violet-200 bg-violet-50 text-violet-900", label: "manual.calloutTip" },
};

/**
 * Command block. If copying fails it does not pretend to succeed —
 * `navigator.clipboard` is missing or throws on non-HTTPS origins or when permission is denied,
 * and showing "복사됨" (Copied) then would have the user paste an empty clipboard
 * (same discipline as CopyLinkButton in components/prototypes/PrototypeCard.tsx).
 */
function Command({ lines, caption }: { lines: string[]; caption?: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const text = lines.join("\n");

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return (
    <figure className="my-4">
      <div className="relative overflow-hidden rounded-lg bg-slate-900">
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={t("manual.copyAria")}
          className="absolute right-2 top-2 rounded-md bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600"
        >
          {copied ? t("admin.copied") : t("admin.copy")}
        </button>
        <pre className="overflow-x-auto px-4 py-3 pr-20 text-xs leading-relaxed text-slate-100">
          <code>{text}</code>
        </pre>
      </div>
      {caption && (
        <figcaption className="mt-1.5 text-xs text-slate-500">{caption}</figcaption>
      )}
    </figure>
  );
}

/**
 * Screen mockup.
 *
 * If real screenshots are added, replace the <Mockup/> inside this <figure> with an <img> —
 * the block type (`{kind:"mockup"}`) and the caption can be used as is, and the content files
 * need not be touched.
 */
function MockupFigure({ id, caption }: { id: keyof typeof MOCKUPS; caption: string }) {
  const t = useT();
  const Mockup = MOCKUPS[id];
  return (
    <figure className="my-5">
      <Mockup />
      <figcaption className="mt-2 text-xs text-slate-500">
        {caption}
        <span className="ml-1.5 text-slate-400">· {t("manual.mockupNotice")}</span>
      </figcaption>
    </figure>
  );
}

function Block({ block }: { block: ManualBlock }) {
  const t = useT();

  switch (block.kind) {
    case "md":
      return <Markdown text={block.md} className="my-3" />;

    case "heading":
      // scroll-mt: gives the anchor a margin so the title is not hidden under the sticky header (h-16).
      return (
        <h3
          id={block.id}
          className="mt-8 scroll-mt-20 border-b border-slate-100 pb-1 text-base font-bold text-slate-800"
        >
          {block.text}
        </h3>
      );

    case "callout": {
      const style = CALLOUT[block.tone];
      return (
        <aside className={`my-4 rounded-lg border px-4 py-3 ${style.box}`}>
          <p className="mb-1 text-xs font-bold uppercase tracking-wide opacity-70">
            {t(style.label)}
          </p>
          <Markdown text={block.md} />
        </aside>
      );
    }

    case "steps":
      return (
        <ol className="my-4 space-y-2">
          {block.items.map((item, i) => (
            <li key={i} className="flex gap-3">
              <span
                aria-hidden="true"
                className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-violet-100 text-[11px] font-bold text-violet-700"
              >
                {i + 1}
              </span>
              <Markdown text={item} className="flex-1 [&>p]:my-0" />
            </li>
          ))}
        </ol>
      );

    case "cmd":
      return <Command lines={block.lines} caption={block.caption} />;

    case "mockup":
      return <MockupFigure id={block.id} caption={block.caption} />;

    case "diagram":
      return (
        <figure className="my-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <Diagram block={block} />
          <figcaption className="mt-3 text-xs text-slate-500">{block.caption}</figcaption>
          <DiagramHint />
        </figure>
      );

    case "details":
      return (
        <details className="my-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-2">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            {block.summary}
          </summary>
          <div className="mt-2">
            <Markdown text={block.md} />
          </div>
        </details>
      );

    default: {
      // Adding a block kind and forgetting this place makes compilation fail.
      const exhaustive: never = block;
      void exhaustive;
      return null;
    }
  }
}

export function ManualBlocks({ blocks }: { blocks: ManualBlock[] }) {
  return (
    <>
      {blocks.map((block, i) => (
        <Block key={i} block={block} />
      ))}
    </>
  );
}
