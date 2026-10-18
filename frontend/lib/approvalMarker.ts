// frontend/lib/approvalMarker.ts — the predicate that recognises an approval decision in the audit log.
//
// The backend decides the approval turn's text (`turn_text` in
// backend/aipds/routes/approval.py). If this predicate drifts from that text,
// the approval gate silently fails to open: the sent text cannot be found again
// in the audit log, and the user sees that they pressed the button and nothing
// happened.
//
// Why not an opaque marker (`[APPROVED]`): this text is not a machine signal.
// The approval turn stays in the transcript and chat history as a user speech
// bubble. The agent must understand it as approval and a human must be able to
// read it. An approval word in the project language works without extra prompt
// support, because the agent is already conversing in that language.
//
// The upstream AI-PLC rules only require "user explicitly approves" and define
// no keyword (envision.md:412, product-strategy.md:154, go-to-market.md:160) --
// i.e. this word is a protocol we defined, so we have to handle both languages.

// Accept both languages. Existing Korean audit logs must keep being recognised;
// the same discipline as parsers/audit.py accepting both `사용자 입력|User Raw Input`.
//
// Why the `i` flag for English: the audit log is the agent's transcription of
// user input, so the spelling wobbles ("approved", "Approved"). Korean has no
// letter case, so it is unaffected.
//
// Wrapped in `^...$` to require a full match -- counting a mention inside a
// sentence as the decision makes the gate disappear before the PM presses it
// (that test in approvalState.test.ts).
const APPROVAL_RE = /^\s*(승인|Approved)\s*$/i;

/** Is this audit log entry's user_input an approval decision? */
export function isApprovalText(text: string): boolean {
  return APPROVAL_RE.test(text);
}

// ---- Approval answered in chat (only within a gate context) ----
//
// The turn a gate button runs is always one of the two words above, so the
// predicate above suffices. But users **also approve in chat** -- the bottom of
// the workspace invites free input, and when the agent asks for approval as
// multiple choice, they answer with a letter.
//
// Measured (ws-m's audit.md): 3 of 5 approval gates took this form and went
// unrecognised -- idx=41 "동의" (agree; the **final approval**), idx=33 "진행"
// (proceed), idx=17 "A".
//
// This list must not be used without a context restriction. If "진행" in
// ordinary conversation were counted as a decision, the gate would disappear
// before the PM pressed it -- the invariant that approvalState test guards. So
// it applies only when `context` points at an approval gate. In the ws-m log,
// that context pointed at exactly the approval gates and nothing else.
const CHAT_APPROVAL_RE = /^\s*(승인|동의|진행|좋아요?|네|예|확인|[A-F]|Approved?|Agreed?|Proceed|Yes|OK)\s*$/i;

//: Whether `context` is an approval gate. Accepts the spellings seen in real logs as-is:
//: "Envision — Step 6 Approval Gate", "Discovery Phase Complete — Final Approval",
//: "최종 승인".
const GATE_CONTEXT_RE = /approval\s*gate|final\s*approval|최종\s*승인|승인\s*게이트/i;

/** Is this entry the user approving in chat **within an approval gate context**?
 *
 *  A fallback for existing projects without records (approval_store). When a
 *  record exists, this predicate is not used -- the record is far more accurate.
 */
export function isChatApprovalInGateContext(text: string, context: string): boolean {
  if (!GATE_CONTEXT_RE.test(context)) return false;
  return CHAT_APPROVAL_RE.test(text);
}
