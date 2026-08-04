import type { AuditEntry } from "@/lib/api/types";
import { isApprovalText } from "@/lib/approvalMarker";

// Approval state is derived from the audit log, which is the only durable
// record of the decision (aiplc-state.md has no "Discovery Document" stage —
// nothing in the rules or the agent ever writes one, so the review page's old
// stage lookup was always undefined).
//
// The gate must disappear once the document is approved, and come back when
// the document changes afterwards: a revision invalidates the approval, so the
// PM has to approve the new text.

/** An audit entry that records an approval decision at the gate. */
function isApproval(e: AuditEntry): boolean {
  // 판정식은 approvalMarker.ts가 소유한다 — 게이트가 보내는 텍스트와 같은
  // 파일에 두어야 한쪽만 바뀌는 일이 없다. 두 언어를 다 받으므로 기존 한국어
  // 감사 로그도 계속 인식된다.
  //
  // INPUT을 보고 AI의 산문을 보지 않는 이유는 그대로다: 다른 답변에 등장한
  // "승인"이 결정으로 세어지면 PM이 누르기 전에 게이트가 사라진다.
  return isApprovalText(e.user_input ?? "");
}

/** An audit entry that records work which would invalidate a prior approval. */
function isDocumentChange(e: AuditEntry): boolean {
  const haystack = `${e.context ?? ""} ${e.ai_response ?? ""}`;
  return /수정|revise|revision|재작성|갱신|업데이트|update/i.test(haystack);
}

export interface ApprovalState {
  /** True when the document has been approved and nothing has changed since. */
  approved: boolean;
  /** The approving entry's index, for display. */
  approvedAtIndex: number | null;
}

/**
 * Decide whether the approval gate should still be shown.
 *
 * Approved-and-unchanged means: the newest approval entry is also the newest
 * entry that matters. If a change was logged after it, the approval is stale
 * and the gate returns so the PM can approve the revised document.
 */
export function deriveApprovalState(entries: AuditEntry[]): ApprovalState {
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
