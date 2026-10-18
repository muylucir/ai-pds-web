// frontend/lib/chatItems.ts
import type { HistoryItem, QuestionFile } from "@/lib/api/types";

// UI VIEW-STATE (not a backend contract): how streamed AgentEvent frames are
// projected into the chat timeline. Backend contract types stay in
// lib/api/types.ts.
export interface TraceEntry {
  // "thinking" is the point where the model entered a thinking block. Why it is the only kind
  // where `text`/`path`/`detail` are all empty: thinking **text** does not exist on this path
  // (measured on Bedrock 2026-09-04 — even with `--thinking-display summarized`,
  // thinking_delta is 0 characters and only the signature arrives). What is kept is the
  // interval's position, and that makes the trace the turn's timeline: think → Read → think →
  // write. "agent" is the point where one sub-agent **finished**. Why the start is not kept:
  // the Agent tool call itself already enters the trace as the lead's status, carrying even
  // what was delegated (tool_trace's `Agent → description`) — keeping the start too would
  // make the same fact two lines.
  kind: "status" | "file_changed" | "thinking" | "agent";
  text: string | null;
  path: string | null;
  // **What the tool did** — the file read, the command run, the search pattern.
  //
  // Live, it comes as the status event's payload (`{"detail": "…"}`); on restore, it comes as a
  // HistoryTraceEntry field. The value is produced in one place, the backend
  // (backend/aipds/tool_trace.py) — if live and restore diverged, the screen would differ before
  // and after a refresh. Only the icon and separator are added here.
  //
  // For kind "agent", it is the summary that agent left.
  detail?: string | null;
  // kind "agent" only. completed | failed | stopped | killed — completion and failure must not
  // look like the same line in the trace.
  status?: string | null;
  // kind "agent" only. That agent's task_id — **the key for updating the line.**
  //
  // One agent's end can arrive twice, and the summary is only on the later one (the rationale
  // for backend builder._close_agent_row). Appending another line then would show completion
  // twice in the trace, so the line with the same key is updated in place. The backend cannot
  // know which end is the last, so the responsibility for folding lies on this side.
  taskId?: string | null;
}
/** The single **most recent thing that happened**, shown by the pinned line above the input.
 *
 *  **Why it is settled by event order.** Taking the "last tool" from the trace leaves that tool's
 *  name in place even after the tool finishes and the answer text is flowing — it says
 *  "자료를 확인하고 있어요" (checking the materials) while it is actually writing. The stream
 *  hook sees events in order, so letting the last event to arrive overwrite this value means
 *  that drift structurally cannot occur.
 *
 *  Live only — restored turns do not have it (the pinned line appears only for a running turn). */
export type LiveActivity =
  | { kind: "thinking" }
  | { kind: "writing" }
  | { kind: "tool"; tool: string | null; detail: string | null }
  | { kind: "file"; path: string | null }
  // A write tool's input is streaming — what (null until the path is settled) and how many
  // characters in.
  | { kind: "drafting"; path: string | null; chars: number };

export interface UserItem {
  id: string;
  role: "user";
  text: string;
  // The structured answers of a restored answer-submission turn (HistoryItem.answers from GET
  // /history). If present, ChatTimeline rebuilds the text in the UI language — the backend's
  // text is a Korean fallback for consumers that do not know this field. Live turns do not
  // have it (there, answerSummary has already built it by expanding choice letters into
  // option text).
  answers?: Record<string, string> | null;
  // The question payload paired with answers (HistoryItem.questions from GET /history). If both
  // are present, ChatTimeline calls the same answerSummary() as live to build the same text —
  // otherwise it falls back to listing the answers like "1: A".
  questions?: QuestionFile | null;
}
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
  // The user cut off this turn. It is a separate field rather than trace because its nature is
  // different — trace is a record of tool runs, and this is the turn's termination reason.
  interrupted?: boolean;
  // What is happening right now — the value the pinned line above the input reads. It pairs
  // with trace but its nature differs: trace is the list "so far", and this is the single
  // "now". It exists only in the live stream (restored turns do not have it).
  activity?: LiveActivity;
}

// A questions file presented in a PAST turn (a history restore) — a static
// summary marker (role "history-card"), not an interactive form.
export interface HistoryCardItem {
  id: string;
  role: "history-card";
  name: string | null;
  // The questions actually asked in that round (HistoryItem.questions from GET /history).
  // They remain structured in the transcript's tool_use.input, so they can be restored —
  // previously this was discarded, so the card was just one line, "질문 제시됨" (questions
  // presented). It is still **read-only**: it is not a live form.
  file?: QuestionFile | null;
}

/** One GET /history item → a timeline item. The workspace and the build panel use the same
 *  conversion — both screens draw with the same ChatTimeline, so if the restore representation
 *  diverged, the same conversation would look different on each screen. The id is received from
 *  the calling hook. */
export function historyItemToChatItem(
  it: HistoryItem, id: string,
): UserItem | AiItem | HistoryCardItem {
  if (it.role === "card") {
    return { id, role: "history-card", name: it.name, file: it.questions ?? null };
  }
  // Carry answers and questions over as is — ChatTimeline uses them to build the text in the UI
  // language. Dropping them here would show the backend's Korean fallback text as is in the
  // English UI, and dropping questions would make it impossible to call the same answerSummary
  // as live, falling back to a "1: A" listing.
  if (it.role === "user") {
    return { id, role: "user", text: it.text ?? "",
             answers: it.answers ?? null, questions: it.questions ?? null };
  }
  return {
    id,
    role: "ai",
    text: it.text ?? "",
    // The restored tool trace — it has the same shape as a live turn's status/file_changed
    // events, so AiMessage's "추론 과정" (reasoning) accordion renders it as is.
    trace: (it.trace ?? []).map((t) => ({
      kind: t.kind, text: t.text, path: t.path, detail: t.detail ?? null })),
    streaming: false,
    error: null,
  };
}
