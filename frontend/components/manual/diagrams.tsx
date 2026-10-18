"use client";
// frontend/components/manual/diagrams.tsx — flow diagrams.
//
// Built with div/flex rather than SVG, for two reasons: the text inside boxes is **translated wording**
// whose length varies by language, so coordinates cannot be fixed (SVG <text> does not wrap either),
// and the boxes must be **links**.
//
// **The wording is owned by the content.** There are no labels in this file — they are received from
// the block's `nodes` and drawn (ManualDiagramBlock in content/manual/types.ts). So there is no separate
// job of translating diagrams, and removing a box in one language makes compilation fail.
import Link from "next/link";

import type { DiagramNode, ManualDiagramBlock } from "@/content/manual";
import { useT } from "@/lib/i18n/provider";

type Tone = "slate" | "violet" | "emerald";

const TONES: Record<Tone, string> = {
  slate: "border-slate-300 bg-white text-slate-700",
  violet: "border-violet-300 bg-violet-50 text-violet-800",
  emerald: "border-emerald-300 bg-emerald-50 text-emerald-800",
};

const HOVER: Record<Tone, string> = {
  slate: "hover:bg-slate-50",
  violet: "hover:bg-violet-100",
  emerald: "hover:bg-emerald-100",
};

/** One box. A link if `to` is present, otherwise just a box. */
function Node({ node, tone = "slate" }: { node: DiagramNode; tone?: Tone }) {
  const base = `block rounded-lg border px-3 py-2 text-center text-xs font-medium ${TONES[tone]}`;
  if (!node.to) {
    return <span className={base}>{node.label}</span>;
  }
  return (
    <Link href={`/manual#${node.to}`} className={`${base} ${HOVER[tone]} transition-colors`}>
      {node.label}
    </Link>
  );
}

function Arrow() {
  // ↓ on narrow screens where they stack vertically, → on screens where they lie horizontally.
  return (
    <span aria-hidden="true" className="select-none text-center text-slate-400">
      <span className="sm:hidden">↓</span>
      <span className="hidden sm:inline">→</span>
    </span>
  );
}

/** Three entry points join at the prototype, pass through validation, and continue beyond. */
function EntryPoints({ nodes }: { nodes: Extract<ManualDiagramBlock, { id: "entry-points" }>["nodes"] }) {
  return (
    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
      {/* Three entry points — mutually exclusive, so stacked in one column to show "one of these". */}
      <div className="flex flex-1 flex-col gap-1.5">
        <Node node={nodes.pain} tone="violet" />
        <Node node={nodes.usecase} tone="violet" />
        <Node node={nodes.spec} tone="violet" />
      </div>
      <Arrow />
      <div className="flex-1">
        <Node node={nodes.build} tone="emerald" />
      </div>
      <Arrow />
      <div className="flex-1">
        <Node node={nodes.validate} tone="emerald" />
      </div>
      <Arrow />
      <div className="flex-1">
        <Node node={nodes.ship} />
      </div>
    </div>
  );
}

/** The loop of build → ask → incorporate → build again. */
function ValidationLoop({
  nodes,
}: {
  nodes: Extract<ManualDiagramBlock, { id: "validation-loop" }>["nodes"];
}) {
  return (
    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
      <div className="flex-1">
        <Node node={nodes.build} tone="emerald" />
      </div>
      <Arrow />
      <div className="flex-1">
        <Node node={nodes.ask} tone="violet" />
      </div>
      <Arrow />
      <div className="flex-1">
        <Node node={nodes.reflect} />
      </div>
      <span aria-hidden="true" className="select-none text-center text-lg text-slate-400">
        ↻
      </span>
    </div>
  );
}

/**
 * Draws one diagram. Why it takes the block as is: each id has a different nodes shape
 * (discriminated union), so narrowing here lets each component get its own keys guaranteed by type.
 */
export function Diagram({ block }: { block: ManualDiagramBlock }) {
  if (block.id === "entry-points") return <EntryPoints nodes={block.nodes} />;
  return <ValidationLoop nodes={block.nodes} />;
}

/** Operating hint under the diagram — tells the reader the boxes are clickable. */
export function DiagramHint() {
  const t = useT();
  return <p className="mt-2 text-xs text-slate-400">{t("manual.diagramHint")}</p>;
}
