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
} as const;
