"use client";
// frontend/components/manual/diagrams.tsx — 흐름 도식.
//
// SVG가 아니라 div/flex다. 이유는 두 가지다: 상자 안의 글자가 **번역된 문구**
// 이므로 길이가 언어마다 달라져 좌표를 고정할 수 없고(SVG <text>는 줄바꿈도
// 하지 않는다), 상자가 **링크**여야 하기 때문이다.
//
// **문구는 콘텐츠가 소유한다.** 이 파일에는 라벨이 없다 — 블록의 `nodes`에서
// 받아 그린다(content/manual/types.ts의 ManualDiagramBlock). 그래서 도식을
// 번역하는 일이 따로 생기지 않고, 한쪽 언어에서 상자를 빼면 컴파일이 실패한다.
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

/** 상자 하나. `to`가 있으면 링크, 없으면 그냥 상자다. */
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
  // 세로로 쌓이는 좁은 화면에서는 ↓, 가로로 놓이는 화면에서는 →.
  return (
    <span aria-hidden="true" className="select-none text-center text-slate-400">
      <span className="sm:hidden">↓</span>
      <span className="hidden sm:inline">→</span>
    </span>
  );
}

/** 세 진입점이 프로토타입에서 합류하고, 검증을 지나 그 뒤로 이어진다. */
function EntryPoints({ nodes }: { nodes: Extract<ManualDiagramBlock, { id: "entry-points" }>["nodes"] }) {
  return (
    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
      {/* 세 진입점 — 서로 배타적이므로 한 열에 쌓아 "이 중 하나"임을 보인다. */}
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

/** 만들고 → 묻고 → 반영하고 → 다시 만드는 고리. */
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
 * 도식 하나를 그린다. 블록을 그대로 받는 이유: id마다 nodes의 모양이 다르므로
 * (판별 유니온) 여기서 좁혀야 각 컴포넌트가 자기 키를 타입으로 보장받는다.
 */
export function Diagram({ block }: { block: ManualDiagramBlock }) {
  if (block.id === "entry-points") return <EntryPoints nodes={block.nodes} />;
  return <ValidationLoop nodes={block.nodes} />;
}

/** 도식 아래의 조작 안내 — 상자가 눌린다는 사실을 알려 준다. */
export function DiagramHint() {
  const t = useT();
  return <p className="mt-2 text-xs text-slate-400">{t("manual.diagramHint")}</p>;
}
