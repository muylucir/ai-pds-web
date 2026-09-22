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
    // 게이트가 보낸 턴만 결정으로 센다 — AI가 승인을 언급하는 문장이
    // 결정으로 세어지면 PM이 누르기 전에 게이트가 사라진다.
    expect(isApprovalText("승인 게이트에서 승인하시면 됩니다")).toBe(false);
    expect(isApprovalText("I have approved the document")).toBe(false);
  });

  it("빈 문자열은 아니다", () => {
    expect(isApprovalText("")).toBe(false);
    expect(isApprovalText("   ")).toBe(false);
  });
});
