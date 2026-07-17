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

export type AgentEventKind = "message" | "file_changed" | "status" | "done" | "error";

export interface AgentEvent {
  kind: AgentEventKind;
  text: string | null;
  path: string | null;
}

export interface TurnResult {
  events: AgentEvent[];
}

// GET /projects → { projects: ProjectSummary[] }; POST /projects → ProjectSummary.
export interface ProjectSummary {
  project_id: string;
  name: string | null;
}
