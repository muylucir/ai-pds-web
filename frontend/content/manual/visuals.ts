// frontend/content/manual/visuals.ts — 목업·도식의 식별자.
//
// 콘텐츠(데이터)와 컴포넌트(JSX)를 갈라 두기 위해 id만 여기에 둔다. 콘텐츠
// 파일이 React 컴포넌트를 직접 임포트하면 두 언어의 콘텐츠 파일이 각자 컴포넌트
// 트리를 들고 있게 되고, 목업을 하나 바꿀 때 두 파일을 고쳐야 한다.
//
// id → 컴포넌트 매핑은 components/manual/registry.tsx가 소유한다. 그 파일의
// Record 타입이 여기 있는 id를 **전부** 덮도록 강제하므로, id를 추가하고
// 컴포넌트를 잊으면 컴파일이 실패한다.

export type MockupId =
  /** 프로젝트 생성 폼 — ID·이름·모델·문서 언어 */
  | "project-create"
  /** 워크스페이스 3분할 — 스테이지 / 채팅 / 산출물 패널 */
  | "workspace"
  /** 질문 시트 — 단일·복수 선택, Other, AI 추천, 부연 설명 */
  | "question-sheet"
  /** 문서 리뷰의 승인 게이트 */
  | "approval-gate"
  /** 프로토타입 카드와 빌드 완료 카드 */
  | "prototype-card"
  /** 검증 설문 패널 — 링크 공유와 집계 */
  | "survey-panel"
  /** 대시보드 — 진행률 카드와 스테이지 타임라인 */
  | "dashboard";

export type DiagramId =
  /** 세 진입점과 Path A·B가 프로토타입에서 합류하는 전체 흐름 */
  | "entry-points"
  /** 프로토타입 → 설문 → Discovery 반영의 순환 */
  | "validation-loop";

// 도식마다 상자의 **이름**을 정해 둔다. 콘텐츠는 이 키로 라벨을 채우고
// (types.ts의 ManualDiagramBlock이 Record로 전부 요구한다), 컴포넌트는 이 키로
// 읽는다. 배열 인덱스로 주고받으면 두 언어의 순서가 어긋난 것을 잡을 수 없다.

/** entry-points 도식의 상자들. */
export type EntryPointNode =
  /** 고객 문제(페인 포인트)에서 시작 */
  | "pain"
  /** 이미 정리된 유스케이스에서 시작 */
  | "usecase"
  /** 이미 있는 프로토타입 명세에서 시작 */
  | "spec"
  /** 세 경로가 합류하는 지점 — 프로토타입을 만든다 */
  | "build"
  /** 만든 것을 사람에게 물어 검증한다 */
  | "validate"
  /** 그 뒤로 이어지는 제품 전략·시장 진입 */
  | "ship";

/** validation-loop 도식의 상자들. */
export type ValidationLoopNode =
  /** 프로토타입을 만들거나 고친다 */
  | "build"
  /** 설문으로 반응을 모은다 */
  | "ask"
  /** 그 결과를 문서에 반영한다 */
  | "reflect";
