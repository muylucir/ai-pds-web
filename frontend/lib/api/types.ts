// These types mirror the backend Pydantic models EXACTLY, including snake_case
// field names, because the backend serializes JSON with those keys and the
// client does no key remapping. Sources:
//   backend/aipds/models.py       (QuestionOption, Question, QuestionFile,
//                                        StageState, ProjectState, AuditEntry,
//                                        AgentEvent)
//   API Completion plan                (GET /projects item shape)

export interface QuestionOption {
  letter: string;
  text: string;
  is_other: boolean;
  recommended: boolean;
}

export interface Question {
  number: number;
  category: string | null;
  text: string;
  options: QuestionOption[];
  answer: string | null;
  // Optional — file-parsed questions (raw_markdown fallback path) lack this
  // field entirely; treat missing/undefined as false (single-select radios).
  multi_select?: boolean;
  // The prose **between** the category header and the question header — "why we ask
  // this". Unlike `text`, which is the body after the question header. Rendered as
  // markdown (tables and lists appear here).
  //
  // Optional for the same reason as multi_select: payloads built from AskUserQuestion
  // have no such field. It is also an empty string in most files (the question comes
  // right after the question header) — the ones with a value are the upstream
  // clarification/confirmation gate question templates.
  context?: string;
}

export interface QuestionFile {
  name: string;
  preamble: string | null;
  questions: Question[];
  parse_ok: boolean;
  raw_markdown: string | null;
}

export type StageStatus = "pending" | "in_progress" | "completed";

export interface StageState {
  name: string;
  status: StageStatus;
  note: string | null;
}

// One of the 6 official AI-PDS capabilities — the backend (aipds/capabilities.py) builds
// it by grouping `stages`. This is the unit the screen counts. `not_applicable` is a
// capability this project's path does not pass through (Intake and Prioritize in Path A, etc.).
export type CapabilityStatus = StageStatus | "not_applicable";

export interface CapabilityState {
  key: string;
  name: string;
  status: CapabilityStatus;
  note: string | null;
}

export interface ProjectState {
  project_type: string | null;
  current_stage: string | null;
  // The original list the agent wrote to `aiplc-state.md`. Its length varies by project.
  stages: StageState[];
  // Always 6, in the official order.
  capabilities: CapabilityState[];
}

export interface AuditEntry {
  index: number;
  timestamp: string;
  user_input: string;
  ai_response: string;
  context: string | null;
}

export type AgentEventKind =
  | "message"
  | "questions"
  | "stage"
  | "document"
  | "file_changed"
  | "status"
  | "done"
  // Declaration that Discovery handed the prototype over to the build (the backend's
  // agent/reconcile.py derives it from the write of build-instructions.md).
  | "prototype_ready"
  // Completion declaration of a prototype build. Paired with the Literal in the backend's models.py.
  | "build_complete"
  // Progress of a single subagent. The payload says **which** agent via `task_id`,
  // and that alone lets parallel work be drawn as several rows rather than one line
  // (rationale in the header of the backend's aipds/agent_activity.py).
  | "agent_activity"
  // Turn log record that the user answered a question during a prototype build. payload =
  // {"answers": {...}}. Paired with the Literal in the backend's models.py.
  | "answers"
  // Context window usage of the main agent. payload = ContextUsage (JSON). Sent only
  // when the value changes (backend aipds/context_usage.py).
  | "context"
  // The file the model is writing right now. payload = DraftPayload (JSON). Only on the
  // live stream (backend agent/tool_input_draft.py).
  | "draft"
  | "error";

/** The agent's context window usage — the shape of the backend's context_usage.summarize.
 *  `left_pct` is the share left **until auto-compaction**: at 0% the CLI summarizes the
 *  prior conversation and clears the context. We measure to that point rather than the end
 *  of the window because that moment is the event that matters to the user (the agent
 *  starts forgetting earlier details). */
export interface ContextUsage {
  total_tokens: number;
  max_tokens: number;
  /** The usage at which compaction happens. Equals the window size when there is no compaction buffer. */
  compact_at_tokens: number;
  left_pct: number;
}

export interface AgentEvent {
  kind: AgentEventKind;
  text: string | null;
  path: string | null;
  payload: string | null;
}

// Structured payload shapes carried as a JSON string in AgentEvent.payload for
// the "questions" / "stage" / "document" kinds (Task 1's AgentEvent extension).
export interface QuestionsPayload {
  // Identifier of the parked turn. Empty string for a round that came from a file — then
  // there is no turn to resume and the answers go to the file.
  interrupt_id: string;
  questions: QuestionFile;
  // When set, this round came **straight from a question file**, and the value is the
  // workspace-relative path. Answers are submitted to that file, not to a parked turn
  // (submit_file_answers in backend routes/answers.py).
  //
  // This is the discriminator. Why not decide by `interrupt_id === ""`: an empty string
  // cannot distinguish "no value" from "came from a file".
  file?: string;
}

// Payload of the build_complete event. remaining is not optional — the backend
// always fills it in (args.get("remaining", "") in proto/tools.py), so the frontend
// only has to handle an empty string and needs no undefined branch.
export interface BuildCompletePayload {
  summary: string;
  remaining: string;
}

// An update to one subagent row. Paired with the backend's
// aipds/agent_activity.activity_payload.
//
// **Missing fields arrive as undefined (not null).** The backend deliberately omits
// keys whose value is None, and the merge rule depends on that absence: an update
// without `detail` reads as "keep the previous value", not "clear the target" (protoAgents.ts).
export interface AgentActivityPayload {
  /** The row key. Same value means the same agent. */
  task_id: string;
  state: "started" | "progress" | "done";
  /** started only. The work that agent took on. */
  label?: string;
  /** progress only. The tool name — the frontend builds the label in the UI language. */
  tool?: string;
  /** progress only. The tool's target (a file path, etc.). */
  detail?: string;
  /** done only. completed | failed | stopped | killed. */
  status?: string;
  /** done only. The one-line note that agent left. */
  summary?: string;
}

export interface StagePayload {
  stage: string;
  status: StageStatus;
  summary: string;
  // Snapshot of the 6 capabilities grouped from the state file that produced this event.
  // The sidebar uses only the last snapshot.
  capabilities?: CapabilityState[];
}

/** Progress while a write tool's input is streaming — the shape of the backend's agent/tool_input_draft.py.
 *  The path arrives in the event's `path` (null until settled). The body is not included. */
export interface DraftPayload {
  id: string;
  tool: string;
  state: "writing" | "written";
  chars: number;
}

export interface DocumentPayload {
  path: string;
  version: string;
  summary: string;
}

// The payload the handoff emits. This card is what leaves the user something to click
// **even if the agent forgets the guidance sentence** — until 2026-08-17, without that
// guidance the user got stuck in Discovery.
export interface PrototypeReadyPayload {
  slug: string;
  spec_path: string;
}

// GET /projects?page&size → ProjectPage; POST /projects → ProjectSummary.
export interface ProjectProgress {
  current_stage: string | null;
  completed: number;
  total: number;
}

// Project permission (backend aipds/project_access.py). admin and owner read and write; member
// only reads — the purpose of a member is viewing, not collaboration.
export type ProjectAccess = "admin" | "owner" | "member";

/** Whether write buttons may be drawn. An unknown value (old backend) is treated as writable — the
 *  server is the final gate, and hiding buttons and blocking existing users is worse. */
export function canWrite(access: ProjectAccess | null | undefined): boolean {
  return access !== "member";
}

export interface ProjectSummary {
  project_id: string;
  name: string | null;
  // Creation time (ISO). May be null for a project restored from an old manifest.
  created_at?: string | null;
  // Only in the list response (fail-soft: null if the state file is missing or unreadable).
  progress?: ProjectProgress | null;
  // The Bedrock model id this project runs on. null = unspecified (it runs on the server's
  // env default — the frontend cannot know that value).
  model_id?: string | null;
  // The effort copied along with the model from the catalog at creation time. null = CLI default.
  effort?: string | null;
  // This project's output language. Separate from the UI language (aipds_lang cookie) — the
  // language the documents, prototype, and chat come out in. The backend always fills it in
  // (unspecified is settled as "ko"), but it is optional because old backend responses may lack it.
  language?: "ko" | "en";
  /** The owner's Cognito username (email local part). */
  owner?: string;
  /** The permission this requester has on this project. Decides whether the screen draws write buttons. */
  access?: ProjectAccess | null;
  /** Usernames of viewing members. Included only for the owner and admins. */
  members?: string[];
}

// GET /projects/{pid} → ProjectDetail. This is what the header's model badge calls.
export interface ProjectDetail {
  project_id: string;
  name: string | null;
  created_at: string | null;
  model_id: string | null;
  // The effort copied along with the model from the catalog at creation time. null = CLI default.
  effort?: string | null;
  // This project's output language. Separate from the UI language (aipds_lang cookie) — the
  // language the documents, prototype, and chat come out in. The backend always fills it in
  // (unspecified is settled as "ko"), but it is optional because old backend responses may lack it.
  language?: "ko" | "en";
  /** The owner's Cognito username (email local part). */
  owner?: string;
  /** The permission this requester has on this project. Decides whether the screen draws write buttons. */
  access?: ProjectAccess | null;
  /** Usernames of viewing members. Included only for the owner and admins. */
  members?: string[];
}

export interface ProjectPage {
  projects: ProjectSummary[];
  total: number;
  page: number;
  size: number;
}

// GET /projects/{pid}/turn → { turn: TurnSummary | null }. A turn is a server job, not a
// request (backend aipds/turn_job.py) — the screen attaches by `turn_id` and reattaches by
// passing back what it received up to `last_seq` as `after`.
export interface TurnSummary {
  turn_id: string;
  kind: "message" | "answers";
  state: "running" | "done" | "error" | "interrupted";
  last_seq: number;
}

// GET /projects/{pid}/history → { items: HistoryItem[] } (Task 1). Restores
// the chat timeline on workspace mount — a "card" role item marks a
// previously-presented questions file (by `name`), never re-rendered as the
// live interactive form.
export interface HistoryTraceEntry {
  kind: "status" | "file_changed";
  text: string | null;
  path: string | null;
  // What the tool did. **The same value** that arrives as the status payload when live
  // (backend/aipds/tool_trace.py builds both).
  detail?: string | null;
}

export interface HistoryItem {
  role: "user" | "ai" | "card";
  text: string | null;
  card: "questions" | null;
  name: string | null;
  trace: HistoryTraceEntry[];
  // Structured answers of an answer-submission turn. If present, the frontend builds the
  // wording in the UI language; if absent (free-form answer, or an old backend that does not
  // know this field), text is used as is.
  answers?: Record<string, string> | null;
  // The question payload of that round. When it comes with answers, the bubble is built with
  // **the same** answerSummary() as live (question numbers, option letters, and option text
  // come from here). It is also carried on card entries to restore "what was asked".
  questions?: QuestionFile | null;
}
