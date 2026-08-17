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
  // Discovery가 프로토타입을 빌드로 넘겼다는 선언(handoff_prototype).
  | "prototype_ready"
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

// handoff_prototype이 흘리는 payload. 이 카드가 있어야 **에이전트가 안내 문장을
// 잊어도** 사용자에게 클릭할 곳이 남는다 — 2026-08-17까지는 안내가 없으면
// 사용자가 Discovery에서 막혔다.
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

export interface ProjectSummary {
  project_id: string;
  name: string | null;
  // 생성 시각(ISO). 구 매니페스트로 복원된 프로젝트는 null일 수 있다.
  created_at?: string | null;
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
  // 답변 제출 턴의 구조화된 답변. 있으면 프론트가 UI 언어로 문구를 만들고,
  // 없으면(자유 서술 답변, 또는 이 필드를 모르는 구 백엔드) text를 그대로 쓴다.
  answers?: Record<string, string> | null;
  // 그 라운드의 질문 payload. answers와 함께 오면 라이브와 **같은**
  // answerSummary()로 말풍선을 만든다(문항 번호·보기 letter·보기 텍스트가
  // 여기서 나온다). card 항목에도 실려 "무엇을 물었는지"를 복원한다.
  questions?: QuestionFile | null;
}
