import { describe, it, expect } from "vitest";
import { approvalTurnText, isApprovalText } from "./approvalMarker";

describe("approvalTurnText", () => {
  it("프로젝트 언어의 승인 단어를 준다", () => {
    expect(approvalTurnText("ko")).toBe("승인");
    expect(approvalTurnText("en")).toBe("Approved");
  });
});

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

  it("두 함수가 어긋나지 않는다 — 보낼 단어는 반드시 판정을 통과한다", () => {
    // 한쪽만 바뀌면 게이트가 조용히 안 열린다. 이 단정이 그 회귀를 막는다.
    for (const lang of ["ko", "en"] as const) {
      expect(isApprovalText(approvalTurnText(lang))).toBe(true);
    }
  });
});
