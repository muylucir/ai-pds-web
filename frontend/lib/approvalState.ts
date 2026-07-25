import type { AuditEntry } from "@/lib/api/types";

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
  // The gate sends the literal turn text "승인"; the agent logs it as the raw
  // user input. Matching the INPUT (not the AI's prose) keeps a narrative
  // mention of "승인" in some other answer from counting as a decision.
  return /^\s*승인\s*$/.test(e.user_input ?? "");
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
