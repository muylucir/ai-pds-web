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

// ---- 승인 레코드가 1순위 근거 ----
//
// 감사 로그 파싱은 **폴백으로 강등**된다. 이유는 실측이다(pilot1의 audit.md
// 41건): 승인 게이트 5건 중 2건만 인식됐다 — "승인"은 되지만 "동의"(최종
// 승인!), "진행", 객관식의 "A"는 전부 실패했다. 사용자가 채팅으로 답하기
// 때문이고, 그것은 우리가 통제하는 값이 아니다.
//
// 같은 증상을 세 번 고쳤는데(ca8c508/68e143f/e18d681) 전부 "에이전트의 출력을
// 어떻게 읽을까"였다. 근거 자체를 우리가 쓰는 레코드로 바꾼다.
describe("deriveApprovalState — 승인 레코드(1순위)", () => {
  const rec = (docHash: string, approvedAt = "2026-08-10T01:00:00Z") => ({
    document: "aiplc-docs/discovery/discovery-document.md",
    doc_hash: docHash,
    approved_at: approvedAt,
  });

  it("레코드가 있으면 감사 로그 문구와 무관하게 승인이다", () => {
    // 이것이 이 기능의 핵심: 에이전트가 "동의"라고 옮겨 적었든 무엇이든
    // 판정에 영향이 없다.
    const state = deriveApprovalState([entry(1, "동의", "최종 승인 완료", "Final Approval")],
                                      { approvals: [rec("h1")], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("문서가 승인 이후 바뀌면 미승인으로 돌아간다", () => {
    // 해시 비교 — 산문에서 '수정'을 찾는 추측이 아니다.
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
    // 실측 결함: pilot1 idx=40의 "...Written to Living Document..."가 'update'를
    // 포함해 idx=37의 승인을 지웠다. 레코드 경로에는 그 오탐이 없다.
    const state = deriveApprovalState(
      [entry(1, "승인", "승인 완료", "Final Approval"),
       entry(2, "다음", "Go-to-Market written to Living Document, updating state", "Completion")],
      { approvals: [rec("h1")], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("레코드가 없으면 감사 로그 폴백으로 판정한다", () => {
    // 이 기능 이전의 모든 프로젝트가 이 상태다 — 기존 동작이 유지되어야 한다.
    const state = deriveApprovalState([entry(2, "승인", "승인 완료", "최종 승인")],
                                      { approvals: [], currentDocHash: "h1" });
    expect(state.approved).toBe(true);
  });

  it("두 번째 인자를 아예 주지 않아도 기존처럼 동작한다", () => {
    // 호출부가 점진적으로 옮겨갈 수 있어야 한다.
    expect(deriveApprovalState([entry(2, "승인", "완료", "최종 승인")]).approved).toBe(true);
  });

  it("현재 문서 해시를 모르면(로딩 중) 레코드만으로 승인을 단정하지 않는다", () => {
    // 해시가 null인 동안 승인으로 보이면, 문서가 실은 바뀌었는데 게이트가
    // 사라진 화면을 잠깐 보여준다. 판정을 미루는 편이 안전하다.
    const state = deriveApprovalState([], { approvals: [rec("h1")], currentDocHash: null });
    expect(state.approved).toBe(false);
  });
});

// ---- 폴백 판정식의 확장 ----
//
// 레코드가 없는 기존 프로젝트를 위한 경로다. 실측한 실패 3건을 받아들이되,
// `context`가 승인 게이트를 가리킬 때로 제한해 오탐을 막는다 — pilot1 로그에서
// 그 문맥은 정확히 승인 게이트만 가리켰다.
describe("deriveApprovalState — 폴백: 채팅으로 답한 승인", () => {
  it("승인 게이트 문맥의 '동의'를 승인으로 센다", () => {
    // 실측 idx=41 — **최종 승인**이 이 형태였고 인식되지 않았다.
    const state = deriveApprovalState([
      entry(41, "동의", "User approved the complete Discovery Document",
            "Discovery Phase Complete — Final Approval"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥의 '진행'을 승인으로 센다", () => {
    // 실측 idx=33.
    const state = deriveApprovalState([
      entry(33, "진행", "User approved Part 2",
            "Prototype & Validation — Step 9 Approval Gate → Product Strategy"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥의 객관식 답 'A'를 승인으로 센다", () => {
    // 실측 idx=17 — 승인을 객관식으로 물었고 사용자가 A를 골랐다.
    const state = deriveApprovalState([
      entry(17, "A", "User approved prototype spec as-is (Option A)",
            "Prototype & Validation — Step 1 Approval Gate"),
    ]);
    expect(state.approved).toBe(true);
  });

  it("승인 게이트 문맥이 아니면 '진행'은 승인이 아니다", () => {
    // 이 제한이 오탐을 막는다 — 평범한 대화의 "진행"이 게이트를 열면 PM이
    // 누르기 전에 승인된 것으로 보인다.
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
