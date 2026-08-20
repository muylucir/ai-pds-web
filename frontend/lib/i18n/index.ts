// frontend/lib/i18n/index.ts — 로케일 타입과 딕셔너리 조회의 단일 출처.
//
// URL 세그먼트 라우팅(/ko/..., /en/...)을 쓰지 않는다. 그러면 middleware.ts의
// gateDecision, lib/auth/safeNext.ts, lib/api/rewriteLocation.ts, 그리고
// /api/proto/{pid}/{slug}/ 프록시 프리픽스가 전부 로케일 세그먼트를 다뤄야
// 한다. trailingSlash/basePath 리다이렉트 루프를 이미 겪은 프록시 계층을 언어
// 때문에 다시 건드릴 이유가 없다. 쿠키 기반, 경로 불변.
import { ko } from "./ko";
import { en } from "./en";

export type Locale = "ko" | "en";

// 쿠키 없음 / Provider 밖 / 알 수 없는 값 → 전부 이 값. 기존 사용자와 기존
// 테스트 535건이 현재 화면을 그대로 보는 것이 이 기본값에 달려 있다.
export const DEFAULT_LOCALE: Locale = "ko";

// httpOnly가 아니다 — LanguageSwitcher가 클라이언트에서 써야 하고, 보안 값이
// 아니다. app/layout.tsx가 서버에서 읽어 <html lang>을 첫 페인트에 맞춘다.
export const LANG_COOKIE = "aipds_lang";

export type Dict = Record<keyof typeof ko, string>;

export function isLocale(value: unknown): value is Locale {
  return value === "ko" || value === "en";
}

export function dictFor(locale: Locale): Dict {
  return locale === "en" ? en : ko;
}

// 언어의 표시 이름. **딕셔너리를 타지 않는다** — 언어 이름은 항상 그 언어
// 자체로 적는다(LanguageSwitcher의 라벨과 같은 규약). "한국어"를 영어 UI에서
// "Korean"으로 바꾸면 그 프로젝트의 문서가 실제로 어떤 글자로 나오는지
// 흐려진다. 헤더 배지와 프로젝트 목록이 같은 값을 쓰도록 여기서 소유한다.
export const LANGUAGE_LABEL: Record<Locale, string> = { ko: "한국어", en: "English" };

export { ko, en };
