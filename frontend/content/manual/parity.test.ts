// frontend/content/manual/parity.test.ts
//
// **두 언어의 매뉴얼이 같은 문서여야 한다.** 절 집합은 타입이 이미 강제하지만
// (ManualContent = Record<ManualSectionId, ManualSection>), 타입이 못 보는 것이
// 세 가지 있고 셋 다 실제로 어긋나기 쉽다:
//
//   1. 한쪽에만 블록이 더 있거나 순서가 다른 것 — 한국어 매뉴얼에 문단을 하나
//      더 붙이고 영어를 잊는 것이 가장 흔한 실패다. 그러면 영어 사용자는 그
//      설명이 존재하는지조차 모른다.
//   2. heading id가 다른 것 — 목차의 하위 항목과 딥링크가 언어에 따라 달라지면
//      `/manual#reset` 같은 링크를 공유할 수 없다.
//   3. 목업·도식 id가 다른 것 — 한쪽 언어에만 화면 그림이 있는 상태.
//
// 그래서 **블록 구조를 그대로 비교**한다. 값(문장)은 당연히 다르므로 kind와
// id만 뽑아서 비교한다.
import { describe, expect, it } from "vitest";

import { MANUAL_ORDER, manualFor } from "./index";
import type { ManualBlock, ManualSection } from "./types";

/** 번역돼도 변하지 않아야 하는 것들만 남긴 블록의 지문. */
function shape(b: ManualBlock): string {
  switch (b.kind) {
    case "heading":
      return `heading:${b.id}`;
    case "mockup":
      return `mockup:${b.id}`;
    case "diagram":
      // 상자의 **이름 집합**까지 비교한다. 타입이 Record로 이미 요구하지만,
      // 이 줄이 있어야 실패 메시지가 "어느 상자가 다른지"를 말해 준다.
      return `diagram:${b.id}:${Object.keys(b.nodes).sort().join(",")}`;
    case "callout":
      return `callout:${b.tone}`;
    case "steps":
      return `steps:${b.items.length}`;
    case "cmd":
      // 명령어는 번역하지 않는다 — 줄까지 같아야 한다.
      return `cmd:${b.lines.join(" | ")}`;
    default:
      return b.kind;
  }
}

function shapes(s: ManualSection): string[] {
  return s.blocks.map(shape);
}

const ko = manualFor("ko");
const en = manualFor("en");

describe("매뉴얼 콘텐츠 패리티", () => {
  it("두 언어가 같은 절을 같은 순서로 갖는다", () => {
    expect(MANUAL_ORDER.map((id) => ko[id].id)).toEqual(MANUAL_ORDER);
    expect(MANUAL_ORDER.map((id) => en[id].id)).toEqual(MANUAL_ORDER);
  });

  it.each([...MANUAL_ORDER])("'%s' 절의 블록 구조가 두 언어에서 같다", (id) => {
    expect(shapes(en[id])).toEqual(shapes(ko[id]));
  });

  it.each([...MANUAL_ORDER])("'%s' 절의 제목·요약·본문이 비어 있지 않다", (id) => {
    for (const [locale, content] of [["ko", ko], ["en", en]] as const) {
      const s = content[id];
      expect(s.title.trim(), `${locale}/${id} title`).not.toEqual("");
      expect(s.lede.trim(), `${locale}/${id} lede`).not.toEqual("");
      expect(s.blocks.length, `${locale}/${id} blocks`).toBeGreaterThan(0);
      for (const b of s.blocks) {
        if (b.kind === "md") expect(b.md.trim(), `${locale}/${id} md`).not.toEqual("");
        if (b.kind === "callout") expect(b.md.trim(), `${locale}/${id} callout`).not.toEqual("");
        if (b.kind === "details") {
          expect(b.summary.trim(), `${locale}/${id} details summary`).not.toEqual("");
          expect(b.md.trim(), `${locale}/${id} details body`).not.toEqual("");
        }
        if (b.kind === "steps") {
          expect(b.items.length, `${locale}/${id} steps`).toBeGreaterThan(0);
          for (const item of b.items) expect(item.trim()).not.toEqual("");
        }
        if (b.kind === "mockup" || b.kind === "diagram") {
          expect(b.caption.trim(), `${locale}/${id} caption`).not.toEqual("");
        }
      }
    }
  });
});

describe("앵커", () => {
  // 절 id와 heading id가 한 문서 안에서 유일해야 한다 — 겹치면 목차 클릭이
  // 엉뚱한 곳으로 가고, 스크롤 위치 표시도 두 항목을 동시에 켠다.
  it.each([["ko", ko], ["en", en]] as const)("%s: 앵커 id가 유일하다", (_locale, content) => {
    const ids: string[] = [];
    for (const id of MANUAL_ORDER) {
      ids.push(id);
      for (const b of content[id].blocks) if (b.kind === "heading") ids.push(b.id);
    }
    expect(ids).toEqual([...new Set(ids)]);
  });

  it.each([["ko", ko], ["en", en]] as const)(
    "%s: 본문의 /manual#앵커 링크가 실제 앵커를 가리킨다",
    (_locale, content) => {
      const known = new Set<string>();
      for (const id of MANUAL_ORDER) {
        known.add(id);
        for (const b of content[id].blocks) if (b.kind === "heading") known.add(b.id);
      }
      const dangling: string[] = [];
      for (const id of MANUAL_ORDER) {
        for (const b of content[id].blocks) {
          // 도식 상자의 링크. 여기가 깨지면 그림을 눌렀을 때 아무 일도
          // 일어나지 않는다 — 본문 링크와 달리 눈에 띄지 않는 고장이다.
          if (b.kind === "diagram") {
            for (const [name, node] of Object.entries(b.nodes)) {
              if (node.to && !known.has(node.to)) {
                dangling.push(`${id}: diagram ${b.id}.${name} → #${node.to}`);
              }
            }
            continue;
          }
          const text = b.kind === "md" || b.kind === "callout" || b.kind === "details"
            ? b.md
            : b.kind === "steps" ? b.items.join("\n") : "";
          for (const m of text.matchAll(/\/manual#([\w-]+)/g)) {
            if (!known.has(m[1])) dangling.push(`${id}: #${m[1]}`);
          }
        }
      }
      expect(dangling).toEqual([]);
    },
  );
});
