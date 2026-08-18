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
  // 카테고리 헤더와 문항 헤더 **사이**의 산문 — "왜 이걸 묻는가". `text`가 문항
  // 헤더 뒤의 본문인 것과 다르다. 마크다운으로 렌더한다(표·목록이 들어온다).
  //
  // Optional인 이유는 multi_select와 같다: AskUserQuestion에서 만든 페이로드에는
  // 이 필드가 없다. 대부분의 파일에서도 빈 문자열이다(문항 헤더 바로 뒤에 질문이
  // 오는 형태) — 값이 있는 것은 상류의 명확화/확인 게이트 질문 템플릿이다.
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
  // Discovery가 프로토타입을 빌드로 넘겼다는 선언(백엔드 agent/reconcile.py가
  // build-instructions.md 쓰기에서 유도한다).
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
  // 파킹된 턴의 식별자. 파일에서 온 라운드는 빈 문자열이다 — 그때는 이어갈 턴이
  // 없고 답변이 파일로 간다.
  interrupt_id: string;
  questions: QuestionFile;
  // 설정되면 이 라운드는 **질문 파일에서 그대로** 왔다는 뜻이고, 그 값이 워크스페이스
  // 상대 경로다. 답변은 파킹된 턴이 아니라 그 파일로 제출한다
  // (backend routes/answers.py의 submit_file_answers).
  //
  // 이것이 판별자다. `interrupt_id === ""`로 판단하지 않는 이유: 빈 문자열은
  // "값이 없다"와 "파일에서 왔다"를 구별하지 못한다.
  file?: string;
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

// 인계가 흘리는 payload. 이 카드가 있어야 **에이전트가 안내 문장을
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
  // 도구가 무엇을 했는지. 라이브에서는 status payload로 오는 것과 **같은 값**이다
  // (backend/pathfinder/tool_trace.py가 양쪽을 만든다).
  detail?: string | null;
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
