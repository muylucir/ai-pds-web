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
    // The audit log is transcribed by the agent, so its letter case is inconsistent.
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

// ---- The approval record is the primary evidence ----
//
// Audit log parsing is **demoted to a fallback**. The reason is measured (41 entries in
// ws-m's audit.md): only 2 of 5 approval gates were recognized — "승인" (approve) worked,
// but "동의" (agree — the final approval!), "진행" (proceed), and "A" from a multiple-choice
// question all failed. That is because users answer in chat, and that is not a value we control.
//
// We fixed the same symptom three times (ca8c508/68e143f/e18d681), each time as "how should
// we read the agent's output". Instead, we switch the evidence itself to a record we write.
describe("deriveApprovalState — 승인 레코드(1순위)", () => {
  const rec = (docHash: string, approvedAt = "2026-08-10T01:00:00Z") => ({
    document: "aiplc-docs/discovery/discovery-document.md",
    doc_hash: docHash,
    approved_at: approvedAt,
  });

  it("레코드가 있으면 감사 로그 문구와 무관하게 승인이다", () => {
    // This is the heart of the feature: whether the agent transcribed "동의" (agree) or
    // anything else has no effect on the verdict.
    const state = deriveApprovalState([entry(1, "동의", "최종 승인 완료", "Final Approval")],
                                      { approvals: [rec("h1")], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("문서가 승인 이후 바뀌면 미승인으로 돌아간다", () => {
    // A hash comparison — not a guess that looks for '수정' (revise) in prose.
    const state = deriveApprovalState([], { approvals: [rec("h1")], currentDocHash: "h2" });
    expect(state.approved).toBe(false);
  });

  it("재승인하면 다시 승인이다", () => {
    const state = deriveApprovalState([], {
      approvals: [rec("h1", "2026-08-10T01:00:00Z"), rec("h2", "2026-08-10T02:00:00Z")],
      currentDocHash: "h2",
    });
    expect(state.approved).toBe(true);
  });

  it("정상 진행 서술에 update가 있어도 승인을 무효화하지 않는다", () => {
    // Observed defect: ws-m idx=40's "...Written to Living Document..." contained 'update'
    // and erased the approval at idx=37. The record path has no such false positive.
    const state = deriveApprovalState(
      [entry(1, "승인", "승인 완료", "Final Approval"),
       entry(2, "다음", "Go-to-Market written to Living Document, updating state", "Completion")],
      { approvals: [rec("h1")], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("레코드가 없으면 감사 로그 폴백으로 판정한다", () => {
    // Every project from before this feature is in this state — existing behavior must be preserved.
    const state = deriveApprovalState([entry(2, "승인", "승인 완료", "최종 승인")],
                                      { approvals: [], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("두 번째 인자를 아예 주지 않아도 기존처럼 동작한다", () => {
    // Call sites must be able to migrate gradually.
    expect(deriveApprovalState([entry(2, "승인", "완료", "최종 승인")]).approved).toBe(true);
  });

  it("현재 문서 해시를 모르면(로딩 중) 레코드만으로 승인을 단정하지 않는다", () => {
    // If it looked approved while the hash is null, the screen would briefly show the gate
    // gone even though the document actually changed. Deferring the verdict is safer.
    const state = deriveApprovalState([], { approvals: [rec("h1")], currentDocHash: null });
    expect(state.approved).toBe(false);
  });
});

// ---- Extending the fallback predicate ----
//
// This path is for existing projects without a record. It accepts the 3 observed failures,
// but only when `context` points at an approval gate, to prevent false positives — in the
// ws-m log that context pointed at exactly the approval gates and nothing else.
describe("deriveApprovalState — 폴백: 채팅으로 답한 승인", () => {
  it("승인 게이트 문맥의 '동의'를 승인으로 센다", () => {
    // Observed idx=41 — the **final approval** took this form and was not recognized.
    const state = deriveApprovalState([
      entry(41, "동의", "User approved the complete Discovery Document",
            "Discovery Phase Complete — Final Approval"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥의 '진행'을 승인으로 센다", () => {
    // Observed idx=33.
    const state = deriveApprovalState([
      entry(33, "진행", "User approved Part 2",
            "Prototype & Validation — Step 9 Approval Gate → Product Strategy"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥의 객관식 답 'A'를 승인으로 센다", () => {
    // Observed idx=17 — the approval was asked as multiple choice and the user picked A.
    const state = deriveApprovalState([
      entry(17, "A", "User approved prototype spec as-is (Option A)",
            "Prototype & Validation — Step 1 Approval Gate"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥이 아니면 '진행'은 승인이 아니다", () => {
    // This restriction prevents false positives — if "진행" (proceed) in ordinary conversation
    // opened the gate, it would look approved before the PM pressed anything.
    const state = deriveApprovalState([
      entry(5, "진행", "다음 질문을 준비했습니다", "Envision — Step 2 Questions"),
    ]);
    expect(state.approved).toBe(false);
  });

  it("승인 게이트 문맥이어도 거절은 승인이 아니다", () => {
    const state = deriveApprovalState([
      entry(9, "아니요, 다시 써줘", "재작성합니다", "Envision — Step 6 Approval Gate"),
    ]);
    expect(state.approved).toBe(false);
  });
});
