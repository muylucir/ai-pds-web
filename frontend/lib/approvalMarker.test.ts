import { describe, it, expect } from "vitest";
import { isApprovalText } from "./approvalMarker";

describe("isApprovalText", () => {
  it("두 언어를 다 인식한다", () => {
    expect(isApprovalText("승인")).toBe(true);
    expect(isApprovalText("Approved")).toBe(true);
  });

  it("영어는 대소문자를 가리지 않는다 — 에이전트가 감사 로그에 옮겨 적을 때 표기가 흔들린다", () => {
    expect(isApprovalText("approved")).toBe(true);
    expect(isApprovalText("APPROVED")).toBe(true);
  });

  it("앞뒤 공백을 허용한다", () => {
    expect(isApprovalText("  승인  ")).toBe(true);
    expect(isApprovalText("\nApproved\n")).toBe(true);
  });

  it("문장 속에 든 승인은 인식하지 않는다", () => {
    // Only turns sent by the gate count as decisions — if a sentence in which the AI mentions approval
    // were counted as a decision, the gate would disappear before the PM pressed it.
    expect(isApprovalText("승인 게이트에서 승인하시면 됩니다")).toBe(false);
    expect(isApprovalText("I have approved the document")).toBe(false);
  });

  it("빈 문자열은 아니다", () => {
    expect(isApprovalText("")).toBe(false);
    expect(isApprovalText("   ")).toBe(false);
  });
});
