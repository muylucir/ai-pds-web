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
} as const;
