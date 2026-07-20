// These types mirror the backend Pydantic models EXACTLY, including snake_case
// field names, because the backend serializes JSON with those keys and the
// client does no key remapping. Sources:
//   backend/pathfinder/models.py       (QuestionOption, Question, QuestionFile,
//                                        StageState, ProjectState, AuditEntry)
//   backend/pathfinder/sandbox/base.py (AgentEvent, TurnResult)
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

export interface ProjectState {
  project_type: string | null;
  current_stage: string | null;
  stages: StageState[];
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
  | "error";

export interface AgentEvent {
  kind: AgentEventKind;
  text: string | null;
  path: string | null;
  payload: string | null;
}

export interface TurnResult {
  events: AgentEvent[];
}

// Structured payload shapes carried as a JSON string in AgentEvent.payload for
// the "questions" / "stage" / "document" kinds (Task 1's AgentEvent extension).
export interface QuestionsPayload {
  interrupt_id: string;
  questions: QuestionFile;
}

export interface StagePayload {
  stage: string;
  status: StageStatus;
  summary: string;
}

export interface DocumentPayload {
  path: string;
  version: string;
  summary: string;
}

// GET /projects → { projects: ProjectSummary[] }; POST /projects → ProjectSummary.
export interface ProjectSummary {
  project_id: string;
  name: string | null;
}

// GET /projects/{pid}/history → { items: HistoryItem[] } (Task 1). Restores
// the chat timeline on workspace mount — a "card" role item marks a
// previously-presented questions file (by `name`), never re-rendered as the
// live interactive form.
export interface HistoryItem {
  role: "user" | "ai" | "card";
  text: string | null;
  card: "questions" | null;
  name: string | null;
}
