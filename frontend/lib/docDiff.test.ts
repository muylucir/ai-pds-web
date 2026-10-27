import { describe, it, expect } from "vitest";
import { changedSections, diffBlocks, splitBlocks } from "./docDiff";

const ops = (before: string, after: string) => diffBlocks(before, after).map((c) => c.op);

describe("splitBlocks", () => {
  it("splits into rendered blocks, one per list item, and keeps a code fence whole", () => {
    const md = "# 제목\n\n문단 하나.\n\n- 첫째\n- 둘째\n\n```\na\n\nb\n```\n";
    const blocks = splitBlocks(md);
    expect(blocks.map((b) => b.text)).toEqual(["# 제목", "문단 하나.", "- 첫째", "- 둘째", "```\na\n\nb\n```"]);
    expect(blocks[0]).toMatchObject({ kind: "heading", heading: "제목" });
  });

  it("keeps an ordered item's number, so the item renders as the same number alone", () => {
    expect(splitBlocks("1. 하나\n2. 둘\n").map((b) => b.text)).toEqual(["1. 하나", "2. 둘"]);
  });
});

describe("diffBlocks", () => {
  it("marks only the list item that was added", () => {
    expect(ops("# 페인 포인트\n\n- 재고 확인\n- 배송 지연\n",
               "# 페인 포인트\n\n- 재고 확인\n- 반품 수작업\n- 배송 지연\n"))
      .toEqual(["same", "same", "added", "same"]);
  });

  it("reads a rewritten paragraph as modified, with its earlier text", () => {
    const changes = diffBlocks("# A\n\n예전 문장.\n", "# A\n\n새 문장.\n");
    expect(changes.map((c) => c.op)).toEqual(["same", "modified"]);
    const modified = changes[1];
    expect(modified.op === "modified" && modified.before.text).toBe("예전 문장.");
  });

  it("reports a removed block", () => {
    expect(ops("하나\n\n둘\n\n셋\n", "하나\n\n셋\n")).toEqual(["same", "removed", "same"]);
  });

  it("ignores whitespace the reader cannot see", () => {
    expect(ops("# A\n\n문장.   \n", "# A\n\n\n문장.\n")).toEqual(["same", "same"]);
  });

  it("finds the changed and removed rows of a table", () => {
    const before = "| 항목 | 결정 |\n|---|---|\n| 결제 | 카드 |\n| 배송 | 택배 |\n| 반품 | 없음 |\n";
    const after = "| 항목 | 결정 |\n|---|---|\n| 결제 | 카드, 포인트 |\n| 배송 | 택배 |\n";
    const [change] = diffBlocks(before, after);
    expect(change.op).toBe("modified");
    if (change.op !== "modified") return;
    expect([...(change.rows?.changed ?? [])]).toEqual([3]);
    expect(change.rows?.removed).toBe(1);   // 반품 — the rewritten 결제 row is a change, not a removal
  });
});

describe("changedSections", () => {
  it("names each section holding a change by its heading, once", () => {
    const changes = diffBlocks(
      "서문\n\n# 하나\n\n가\n\n나\n\n# 둘\n\n다\n",
      "서문 수정\n\n# 하나\n\n가 수정\n\n나 수정\n\n# 둘\n\n다\n");
    expect(changedSections(changes).map((s) => s.heading)).toEqual([null, "하나"]);
  });
});
