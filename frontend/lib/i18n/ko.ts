// frontend/lib/i18n/ko.ts — 한국어 UI 문자열.
//
// 키는 평면이다(중첩 객체 없음). 중첩을 쓰면 `t("a.b.c")` 형태의 문자열 경로가
// 되어 타입 검사를 잃는다 — 평면 키는 `keyof typeof ko`로 오타가 컴파일 에러가
// 된다.
//
// 키 이름은 `영역.용도` 규약이다(`nav.dashboard`, `header.modelBadgeTitle`).
// 화면 문자열을 치환할 때마다 여기에 키를 추가하고, en.ts에도 같은 키를 넣는다 —
// 타입이 강제하므로 빠뜨리면 컴파일이 실패한다.
export const ko = {
  "nav.dashboard": "대시보드",
  "nav.workspace": "워크스페이스",
  "nav.review": "문서 리뷰",
  "nav.prototypes": "프로토타입",
  "nav.ariaLabel": "주요 메뉴",
  "nav.needProject": "프로젝트를 먼저 선택하세요",
  "header.modelBadgeTitle": "이 프로젝트가 사용하는 AI 모델",
  "header.bedrockConnected": "Bedrock 연결됨",
  "header.languageBadgeTitle": "이 프로젝트의 문서·프로토타입·채팅 언어",
  "chat.answersSubmitted": "답변 제출",
  "stream.turnError": "턴 처리 중 오류가 발생했습니다.",
  "stream.buildError": "빌드 중 오류가 발생했습니다.",
  "stream.disconnected": "연결이 끊어졌습니다. 다시 시도해 주세요.",
  "err.generic": "요청이 실패했습니다.",
  "err.emailExists": "이미 등록된 이메일입니다.",
  "err.userNotFound": "사용자를 찾을 수 없습니다.",
  "err.badRequest": "요청이 올바르지 않습니다.",
  "err.forbidden": "권한이 없습니다.",
  "err.tooManyRequests": "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
  "err.userAdminFailed": "사용자 관리 요청이 실패했습니다.",
  "err.userCreateFailed": "사용자 생성에 실패했습니다. 다시 시도해 주세요.",
  "err.selfTarget": "자신의 계정에는 이 작업을 할 수 없습니다. 다른 관리자에게 요청하세요.",
  "err.lastAdmin": "마지막 관리자에게는 이 작업을 할 수 없습니다. 먼저 다른 관리자를 지정하세요.",
  "err.nameRequired": "이름을 입력하세요.",
  "err.modelIdRequired": "모델 ID를 입력하세요.",
  "err.modelIdCharset": "모델 ID는 영숫자, '.', '-', '_', ':'만 포함해야 합니다.",
  "err.modelNotSelectable": "선택할 수 없는 모델입니다.",
  "err.languageUnsupported": "지원하지 않는 언어입니다.",
  "err.buildSlotsBusy": "다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요.",
  "err.buildSessionActive": "빌드 세션이 진행 중입니다 — 세션을 먼저 종료해 주세요.",
  "err.initIncomplete": "초기화가 완료되지 않았습니다 — 다시 시도해 주세요.",
  "err.surveyClosed": "이 설문은 마감되었습니다.",
  "err.surveyFull": "응답 수 상한에 도달했습니다. 설문을 마감해 주세요.",
} as const;
