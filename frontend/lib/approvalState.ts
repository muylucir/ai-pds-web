import type { AuditEntry } from "@/lib/api/types";
import { isApprovalText, isChatApprovalInGateContext } from "@/lib/approvalMarker";

// 승인 판정의 근거는 **두 계층**이다.
//
// 1순위: 승인 레코드(backend/aipds/approval_store.py). 게이트 버튼을 누른
//   순간 백엔드가 쓰는 구조화된 기록이다.
// 폴백: 감사 로그 파싱. 레코드가 없는 기존 프로젝트에만 쓴다.
//
// **왜 레코드가 생겼는가.** 종전에는 폴백만 있었다. 즉 사용자가 누른 사실을
// 버리고, 에이전트가 자연어로 옮겨 적은 것을 정규식으로 되찾았다. 실측
// (pilot1의 audit.md 41건): 승인 게이트 5건 중 2건만 인식됐다 — "승인"은
// 되지만 "동의"(최종 승인!), "진행", 객관식 "A"는 실패. 게다가 정상 진행
// 서술의 'update'가 남은 승인마저 무효화했다(idx=40이 idx=37을 지웠다).
// 화면에는 "기록된 승인 이력이 없습니다"만 남았다.
//
// 같은 증상을 세 번 고쳤고(ca8c508 파서, 68e143f 표시조건, e18d681 언어)
// 전부 "에이전트의 출력을 어떻게 읽을까"였다. 우리가 통제하지 못하는 값을
// 근거로 쓰는 한 다음 표현에서 또 깨진다.

/** An audit entry that records an approval decision at the gate. */
function isApproval(e: AuditEntry): boolean {
  // 판정식은 approvalMarker.ts가 소유한다 — 게이트가 보내는 텍스트와 같은
  // 파일에 두어야 한쪽만 바뀌는 일이 없다. 두 언어를 다 받으므로 기존 한국어
  // 감사 로그도 계속 인식된다.
  //
  // INPUT을 보고 AI의 산문을 보지 않는 이유는 그대로다: 다른 답변에 등장한
  // "승인"이 결정으로 세어지면 PM이 누르기 전에 게이트가 사라진다.
  const input = e.user_input ?? "";
  if (isApprovalText(input)) return true;
  // 채팅으로 답한 승인 — 승인 게이트 문맥에서만 인정한다.
  return isChatApprovalInGateContext(input, e.context ?? "");
}

/** An audit entry that records work which would invalidate a prior approval. */
function isDocumentChange(e: AuditEntry): boolean {
  // **user_input만 본다.** 종전에는 `context + ai_response`를 봤는데, 정상
  // 진행 서술이 'update'/'갱신'을 흔히 포함해 승인이 멋대로 무효화됐다
  // (실측: "Go-to-Market ... Written to Living Document, Discovery Phase
  // Completion"이 걸렸다). 사용자가 수정을 **요청한** 것만 승인을 무효화한다.
  //
  // 레코드 경로에는 이 판정이 아예 없다 — 해시 비교가 사실을 말해준다.
  return /수정|revise|revision|재작성|고쳐|바꿔/i.test(e.user_input ?? "");
}

export interface ApprovalState {
  /** True when the document has been approved and nothing has changed since. */
  approved: boolean;
  /** The approving entry's index, for display. */
  approvedAtIndex: number | null;
}

/** 승인 레코드 한 건 (GET /projects/{pid}/approvals). */
export interface ApprovalRecord {
  document: string;
  doc_hash: string;
  approved_at: string;
}

export interface ApprovalEvidence {
  /** 승인 이력, 시간순. 비어 있으면 감사 로그 폴백으로 판정한다. */
  approvals: ApprovalRecord[];
  /** 지금 화면의 문서 내용 해시. 로딩 중이면 null. */
  currentDocHash: string | null;
}

/**
 * Decide whether the approval gate should still be shown.
 *
 * 레코드가 있으면 그것만 본다 — 해시가 일치하면 승인, 다르면 문서가 바뀐
 * 것이므로 재승인이 필요하다. 이 비교는 추측이 아니라 사실이다.
 *
 * 레코드가 없으면 감사 로그로 떨어진다: 가장 최근 승인이 여전히 가장 최근의
 * 유효한 항목인가. 그 뒤에 수정 요청이 있으면 승인은 낡았고 게이트가 돌아온다.
 */
export function deriveApprovalState(entries: AuditEntry[],
                                    evidence?: ApprovalEvidence): ApprovalState {
  const approvals = evidence?.approvals ?? [];
  if (approvals.length > 0) {
    // 해시를 모르는 동안(문서 로딩 중)은 승인을 단정하지 않는다. 문서가 실은
    // 바뀌었는데 게이트가 사라진 화면을 잠깐 보여주는 것보다 안전하다.
    const hash = evidence?.currentDocHash ?? null;
    const approved = hash !== null && approvals.some((a) => a.doc_hash === hash);
    return { approved, approvedAtIndex: null };
  }

  if (entries.length === 0) return { approved: false, approvedAtIndex: null };
  const ordered = [...entries].sort((a, b) => a.index - b.index);

  let approvedAtIndex: number | null = null;
  for (const e of ordered) {
    if (isApproval(e)) {
      approvedAtIndex = e.index;
    } else if (approvedAtIndex !== null && isDocumentChange(e)) {
      // Work after the approval invalidates it.
      approvedAtIndex = null;
    }
  }
  return { approved: approvedAtIndex !== null, approvedAtIndex };
}
