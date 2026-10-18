import type { AuditEntry } from "@/lib/api/types";
import { isApprovalText, isChatApprovalInGateContext } from "@/lib/approvalMarker";

// The basis for judging approval has **two layers**.
//
// Primary: the approval record (backend/aipds/approval_store.py). A structured record the
//   backend writes the moment the gate button is pressed.
// Fallback: parsing the audit log. Used only for existing projects without records.
//
// **Why the record came about.** Previously there was only the fallback. That is, the fact that
// the user pressed the button was thrown away, and what the agent transcribed in natural language
// was recovered with regexes. Measured (41 entries in ws-m's audit.md): only 2 of 5 approval
// gates were recognized — "승인" (approve) worked, but "동의" (agree — a final approval!),
// "진행" (proceed), and multiple-choice "A" failed. On top of that, 'update' in a normal progress
// narrative invalidated even the remaining approvals (idx=40 erased idx=37).
// The screen showed only "기록된 승인 이력이 없습니다" (no recorded approval history).
//
// The same symptom was fixed three times (ca8c508 parser, 68e143f display condition, e18d681
// language), and every fix was about "how to read the agent's output". As long as we rely on a
// value we do not control, it will break again on the next phrasing.

/** An audit entry that records an approval decision at the gate. */
function isApproval(e: AuditEntry): boolean {
  // The judgment logic is owned by approvalMarker.ts — it must live in the same file as the text
  // the gate sends so that only one side never changes. It accepts both languages, so existing
  // Korean audit logs keep being recognized.
  //
  // The reason it looks at INPUT and not the AI's prose stays the same: if "승인" (approve)
  // appearing in some other answer were counted as a decision, the gate would vanish before
  // the PM pressed it.
  const input = e.user_input ?? "";
  if (isApprovalText(input)) return true;
  // An approval answered via chat — accepted only in the context of an approval gate.
  return isChatApprovalInGateContext(input, e.context ?? "");
}

/** An audit entry that records work which would invalidate a prior approval. */
function isDocumentChange(e: AuditEntry): boolean {
  // **Only user_input is looked at.** Previously it looked at `context + ai_response`, but normal
  // progress narratives commonly contain 'update'/'갱신' (update), so approvals were arbitrarily
  // invalidated (measured: "Go-to-Market ... Written to Living Document, Discovery Phase
  // Completion" matched). Only the user **requesting** a revision invalidates an approval.
  //
  // The record path has no such judgment at all — the hash comparison states the fact.
  return /수정|revise|revision|재작성|고쳐|바꿔/i.test(e.user_input ?? "");
}

export interface ApprovalState {
  /** True when the document has been approved and nothing has changed since. */
  approved: boolean;
  /** The approving entry's index, for display. */
  approvedAtIndex: number | null;
}

/** One approval record (GET /projects/{pid}/approvals). */
export interface ApprovalRecord {
  document: string;
  doc_hash: string;
  approved_at: string;
}

export interface ApprovalEvidence {
  /** Approval history, in chronological order. If empty, judged via the audit log fallback. */
  approvals: ApprovalRecord[];
  /** The hash of the document content currently on screen. null while loading. */
  currentDocHash: string | null;
}

/**
 * Decide whether the approval gate should still be shown.
 *
 * If there are records, only they are looked at — if the hash matches it is approved; if it
 * differs the document has changed, so re-approval is needed. This comparison is a fact, not
 * a guess.
 *
 * Without records it falls back to the audit log: is the most recent approval still the most
 * recent valid entry? If a revision request came after it, the approval is stale and the gate
 * returns.
 */
export function deriveApprovalState(entries: AuditEntry[],
                                    evidence?: ApprovalEvidence): ApprovalState {
  const approvals = evidence?.approvals ?? [];
  if (approvals.length > 0) {
    // While the hash is unknown (document loading), approval is not asserted. That is safer than
    // briefly showing a screen where the gate is gone even though the document actually changed.
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
