import { describe, it, expect } from "vitest";
import { deriveApprovalState } from "./approvalState";
import type { AuditEntry } from "@/lib/api/types";

function entry(index: number, user_input: string, ai_response = "", context = ""): AuditEntry {
  return { index, timestamp: `2026-07-25T00:00:${index.toString().padStart(2, "0")}Z`,
           user_input, ai_response, context };
}

describe("deriveApprovalState", () => {
  it("is not approved with no audit entries", () => {
    expect(deriveApprovalState([])).toEqual({ approved: false, approvedAtIndex: null });
  });

  it("is not approved from ordinary turns", () => {
    const state = deriveApprovalState([
      entry(1, "NOTAM 대시보드를 고도화 하고싶어", "Discovery 시작"),
      entry(2, "Q1: A) 운항관리사", "페인포인트 확인"),
    ]);
    expect(state.approved).toBe(false);
  });

  it("is approved after the gate's 승인 turn", () => {
    const state = deriveApprovalState([
      entry(1, "고도화 하고싶어", "시작"),
      entry(2, "승인", "승인 완료 — Discovery 단계를 종료합니다.", "최종 승인"),
    ]);
    expect(state).toEqual({ approved: true, approvedAtIndex: 2 });
  });

  it("does NOT count a narrative mention of 승인 as a decision", () => {
    // Only the gate's own turn text counts — otherwise the AI describing a
    // future approval would hide the gate before the PM ever clicked it.
    const state = deriveApprovalState([
      entry(1, "다음은 뭐야?", "다음 단계는 승인 게이트에서 승인하시면 됩니다."),
    ]);
    expect(state.approved).toBe(false);
  });

  it("returns to unapproved when a revision follows the approval", () => {
    const state = deriveApprovalState([
      entry(2, "승인", "승인 완료", "최종 승인"),
      entry(3, "discovery-document.md 수정 요청: 3장 보강", "문서를 수정했습니다", "수정 요청"),
    ]);
    expect(state.approved).toBe(false);
  });

  it("is approved again after re-approving a revised document", () => {
    const state = deriveApprovalState([
      entry(2, "승인", "승인 완료", "최종 승인"),
      entry(3, "수정 요청: 3장 보강", "문서를 수정했습니다", "수정 요청"),
      entry(4, "승인", "재승인 완료", "최종 승인"),
    ]);
    expect(state).toEqual({ approved: true, approvedAtIndex: 4 });
  });

  it("ignores post-approval entries that are not document changes", () => {
    // Reading/summarising after approval must not resurrect the gate.
    const state = deriveApprovalState([
      entry(2, "승인", "승인 완료", "최종 승인"),
      entry(3, "설문 결과 보여줘", "설문 응답 3건을 요약했습니다", "검증 결과 분석 기록"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("evaluates by index order, not array order", () => {
    // getAudit callers sort newest-first elsewhere; the derivation must not
    // depend on the incoming order.
    const state = deriveApprovalState([
      entry(3, "수정 요청: 보강", "수정했습니다", "수정 요청"),
      entry(2, "승인", "승인 완료", "최종 승인"),
    ]);
    expect(state.approved).toBe(false);
  });

  it("영어 프로젝트의 Approved 턴도 승인으로 센다", () => {
    const state = deriveApprovalState([
      entry(1, "I want to improve the NOTAM dashboard", "Discovery started"),
      entry(2, "Approved", "Approval recorded — Discovery is complete.", "Final approval"),
    ]);
    expect(state).toEqual({ approved: true, approvedAtIndex: 2 });
  });

  it("영어 표기가 흔들려도 인식한다", () => {
    // 감사 로그는 에이전트가 옮겨 적은 것이라 대소문자가 일정하지 않다.
    const state = deriveApprovalState([entry(1, "approved", "ok", "Final approval")]);
    expect(state.approved).toBe(true);
  });

  it("문장 속의 approved는 결정이 아니다", () => {
    const state = deriveApprovalState([
      entry(1, "what's next?", "Once you have approved, we move to Inception."),
    ]);
    expect(state.approved).toBe(false);
  });
});
