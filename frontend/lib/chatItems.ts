// frontend/lib/chatItems.ts
import type { QuestionFile } from "@/lib/api/types";

// UI VIEW-STATE (not a backend contract): how streamed AgentEvent frames are
// projected into the chat timeline. Backend contract types stay in
// lib/api/types.ts.
export interface TraceEntry {
  // "thinking"은 모델이 사고 블록에 들어간 지점이다. `text`/`path`/`detail`이
  // 모두 비는 유일한 종류인 이유: 사고 **텍스트**는 이 경로에 존재하지 않는다
  // (Bedrock 실측 2026-09-04 — `--thinking-display summarized`를 붙여도
  // thinking_delta가 0자이고 signature만 온다). 남기는 것은 구간의 위치이고,
  // 그것이 트레이스를 턴의 타임라인으로 만든다: 생각 → Read → 생각 → 작성.
  // "agent"는 서브에이전트 한 명이 **끝난** 지점이다. 시작을 남기지 않는 이유:
  // Agent 도구 호출 자체가 이미 총괄의 status로 트레이스에 들어오고, 거기에
  // 무엇을 맡겼는지까지 실려 있다(tool_trace의 `Agent → description`) — 시작을
  // 또 남기면 같은 사실이 두 줄이 된다.
  kind: "status" | "file_changed" | "thinking" | "agent";
  text: string | null;
  path: string | null;
  // 도구가 **무엇을 했는지** — 읽은 파일, 돌린 명령, 검색 패턴.
  //
  // 라이브에서는 status 이벤트의 payload(`{"detail": "…"}`)로 오고, 복원에서는
  // HistoryTraceEntry의 필드로 온다. 값을 만드는 곳은 백엔드 한 곳이다
  // (backend/aipds/tool_trace.py) — 라이브와 복원이 갈라지면 새로고침 전후로
  // 화면이 달라진다. 아이콘과 구분자만 여기서 붙인다.
  //
  // kind "agent"에서는 그 에이전트가 남긴 요약이다.
  detail?: string | null;
  // kind "agent"에만. completed | failed | stopped | killed — 완료와 실패가
  // 트레이스에서 같은 줄로 보이면 안 된다.
  status?: string | null;
  // kind "agent"에만. 그 에이전트의 task_id — **줄을 갱신하기 위한 키다.**
  //
  // 한 에이전트의 종료가 두 번 올 수 있고 요약이 늦은 쪽에만 실린다(백엔드
  // builder._close_agent_row의 근거). 그때 줄을 하나 더 붙이면 트레이스에 완료가
  // 두 번 나타나므로, 같은 키의 줄을 제자리에서 갱신한다. 어느 종료가 마지막인지
  // 백엔드가 알 수 없으므로 접는 책임이 이쪽에 있다.
  taskId?: string | null;
}
/** 입력창 위 고정 줄이 보여주는 **가장 마지막에 일어난 일** 하나.
 *
 *  **왜 이벤트 순서로 확정하는가.** 트레이스에서 "마지막 도구"를 뽑아 쓰면 도구가
 *  끝나고 답변 텍스트가 흐르는 동안에도 그 도구 이름이 남는다 — 실제로는 쓰고
 *  있는데 "자료를 확인하고 있어요"라고 말한다. 스트림 훅은 이벤트를 순서대로
 *  보므로, 마지막에 온 이벤트가 이 값을 덮어쓰게 하면 그 어긋남이 구조적으로
 *  생기지 않는다.
 *
 *  라이브 전용이다 — 복원된 턴에는 없다(고정 줄은 도는 턴에만 뜬다). */
export type LiveActivity =
  | { kind: "thinking" }
  | { kind: "writing" }
  | { kind: "tool"; tool: string | null; detail: string | null }
  | { kind: "file"; path: string | null };

export interface UserItem {
  id: string;
  role: "user";
  text: string;
  // 복원된 답변 제출 턴의 구조화된 답변(GET /history의 HistoryItem.answers).
  // 있으면 ChatTimeline이 UI 언어로 문구를 다시 만든다 — 백엔드의 text는 이
  // 필드를 모르는 소비자를 위한 한국어 폴백이다. 라이브 턴에는 없다(그쪽은
  // answerSummary가 선택지 문자를 옵션 텍스트로 펼쳐 이미 만들어 둔다).
  answers?: Record<string, string> | null;
  // answers와 짝인 질문 payload(GET /history의 HistoryItem.questions). 둘이 다
  // 있으면 ChatTimeline이 라이브와 같은 answerSummary()를 불러 같은 문구를
  // 만든다 — 없으면 answers를 "1: A" 식으로 나열하는 폴백으로 떨어진다.
  questions?: QuestionFile | null;
}
export interface AiItem {
  id: string;
  role: "ai";
  text: string;
  trace: TraceEntry[];
  streaming: boolean;
  error: string | null;
  // 사용자가 이 턴을 끊었다. trace가 아닌 별도 필드인 이유는 성격이 다르기
  // 때문 — trace는 도구 실행 기록, 이것은 턴의 종결 사유다.
  interrupted?: boolean;
  // 지금 무슨 일이 일어나고 있는가 — 입력창 위 고정 줄이 읽는 값이다.
  // trace와 짝이지만 성격이 다르다: trace는 "지금까지"의 목록이고 이것은
  // "지금" 하나다. 라이브 스트림에만 있다(복원된 턴에는 없다).
  activity?: LiveActivity;
}
