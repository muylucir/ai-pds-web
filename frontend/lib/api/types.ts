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
  // 프로토타입 빌드의 완료 선언. 백엔드 models.py의 Literal과 한 쌍이다.
  | "build_complete"
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

// build_complete 이벤트의 payload. remaining은 옵셔널이 아니다 — 백엔드가
// 항상 채워 보내므로(proto/tools.py의 args.get("remaining", "")) 프론트는
// 빈 문자열만 다루면 되고 undefined 분기가 필요 없다.
export interface BuildCompletePayload {
  summary: string;
  remaining: string;
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

// GET /projects?page&size → ProjectPage; POST /projects → ProjectSummary.
export interface ProjectProgress {
  current_stage: string | null;
  completed: number;
  total: number;
}

export interface ProjectSummary {
  project_id: string;
  name: string | null;
  // 목록 응답에만 실림(fail-soft: 상태 파일이 없거나 읽기 실패면 null).
  progress?: ProjectProgress | null;
  // 이 프로젝트가 도는 Bedrock 모델 id. null = 미지정(서버의 env 기본값으로
  // 돈다 — 프론트는 그 값을 알 수 없다).
  model_id?: string | null;
  // 이 프로젝트의 생성물 언어. UI 언어(pf_lang 쿠키)와 별개다 — 문서·
  // 프로토타입·채팅이 어느 언어로 나오는지. 백엔드는 항상 채워 보내지만
  // (미지정은 "ko"로 확정) 구 백엔드 응답에는 없을 수 있어 옵셔널이다.
  language?: "ko" | "en";
}

// GET /projects/{pid} → ProjectDetail. 헤더의 모델 배지가 부르는 곳이다.
export interface ProjectDetail {
  project_id: string;
  name: string | null;
  created_at: string | null;
  model_id: string | null;
  // 이 프로젝트의 생성물 언어. UI 언어(pf_lang 쿠키)와 별개다 — 문서·
  // 프로토타입·채팅이 어느 언어로 나오는지. 백엔드는 항상 채워 보내지만
  // (미지정은 "ko"로 확정) 구 백엔드 응답에는 없을 수 있어 옵셔널이다.
  language?: "ko" | "en";
}

export interface ProjectPage {
  projects: ProjectSummary[];
  total: number;
  page: number;
  size: number;
}

// GET /projects/{pid}/history → { items: HistoryItem[] } (Task 1). Restores
// the chat timeline on workspace mount — a "card" role item marks a
// previously-presented questions file (by `name`), never re-rendered as the
// live interactive form.
export interface HistoryTraceEntry {
  kind: "status" | "file_changed";
  text: string | null;
  path: string | null;
}

export interface HistoryItem {
  role: "user" | "ai" | "card";
  text: string | null;
  card: "questions" | null;
  name: string | null;
  trace: HistoryTraceEntry[];
}
