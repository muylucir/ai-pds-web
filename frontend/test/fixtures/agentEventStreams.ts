import type { AgentEvent } from "@/lib/api/types";

// Realistic SSE frame sequences (shape matches backend turns.py / sandbox base
// AgentEvent). During a prototype build/iterate turn the agent emits status +
// file_changed frames (surfaced as the "추론 과정" trace / build log) and
// message frames (the AI reply), terminated by a done frame.
export const normalTurn: AgentEvent[] = [
  { kind: "status", text: "요청을 분석하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "prototype/src/components/FilterBar.tsx" },
  { kind: "message", text: "기획전 필터 기능을 추가했습니다.", path: null },
  { kind: "message", text: " 우측 프리뷰에서 확인해 주세요.", path: null },
  { kind: "done", text: null, path: null },
];

// The agent-reported failure path (an "error"-KIND frame), distinct from a
// transport error. streamEvents dispatches this via onEvent AND then terminates
// the stream (onDone), so useTurnStream must handle kind==="error" in onEvent.
export const errorTurn: AgentEvent[] = [
  { kind: "status", text: "프로토타입 빌드를 시작합니다…", path: null },
  { kind: "error", text: "빌드에 실패했습니다: 의존성 설치 오류", path: null },
];

// C2: turns whose file_changed path should materialize a structured timeline
// card in useTurnStream's onDone derivation (see deriveCardsFromPaths).
export const questionsTurn: AgentEvent[] = [
  { kind: "status", text: "Product Strategy 질문지를 생성하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "aiplc-docs/discovery/product-strategy/strategy-questions.md" },
  { kind: "message", text: "포지셔닝·차별화·비즈니스 모델에 관한 13개 질문을 준비했습니다.", path: null },
  { kind: "done", text: null, path: null },
];

export const documentTurn: AgentEvent[] = [
  { kind: "status", text: "Discovery Document를 갱신하고 있습니다…", path: null },
  { kind: "file_changed", text: null, path: "aiplc-docs/discovery/discovery-document.md" },
  { kind: "message", text: "PR/FAQ를 Discovery Document에 작성했습니다.", path: null },
  { kind: "done", text: null, path: null },
];
