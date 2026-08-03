// frontend/lib/i18n/en.ts — English UI strings.
//
// `Record<keyof typeof ko, string>` 타입이 ko.ts와 키 집합이 정확히 같음을
// 강제한다. 키를 빠뜨리면 컴파일 에러이므로, 런타임에 `undefined`가 화면에
// 뜨는 일이 없다. `as const`를 쓰지 않는 이유: 그러면 값의 리터럴 타입까지
// 좁혀져서 ko와 값이 다르다는 이유로 타입이 어긋난다.
import type { ko } from "./ko";

export const en: Record<keyof typeof ko, string> = {
  "nav.dashboard": "Dashboard",
  "nav.workspace": "Workspace",
};
