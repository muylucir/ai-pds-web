"use client";
// frontend/components/LanguageSwitcher.tsx — 헤더의 UI 언어 전환.
//
// AppHeader에서 분리한 이유: AppHeader 자체도 클라이언트 컴포넌트지만, 쿠키
// 쓰기와 router.refresh()는 별개 책임이다(UserMenu가 같은 형태로 분리돼 있다).
//
// 라벨은 번역하지 않는다 — 언어 선택지는 항상 그 언어 자체로 표기한다.
// "영어"라고 쓰면 한국어를 모르는 사용자가 자기 언어를 찾을 수 없다.
import { useRouter } from "next/navigation";

import { LANG_COOKIE, type Locale } from "@/lib/i18n";
import { useLocale } from "@/lib/i18n/provider";

const OPTIONS: Array<{ locale: Locale; label: string }> = [
  { locale: "ko", label: "한국어" },
  { locale: "en", label: "English" },
];

// 1년. 세션 쿠키로 두면 브라우저를 닫을 때마다 한국어로 돌아가고, 그것은
// 사용자가 선택을 다시 하게 만든다.
const MAX_AGE = 60 * 60 * 24 * 365;

export function LanguageSwitcher() {
  const current = useLocale();
  const router = useRouter();

  function choose(locale: Locale) {
    // 같은 언어를 다시 고르는 것은 no-op. refresh()를 부르면 서버 왕복이
    // 일어나므로 아무 변화 없는 요청을 만들지 않는다.
    if (locale === current) return;
    // httpOnly가 아니므로 여기서 쓸 수 있다. secure를 붙이지 않는 이유는
    // 로컬 http 개발에서 쿠키가 저장되지 않게 되기 때문이다 — 이 값은
    // 자격증명이 아니다.
    document.cookie =
      `${LANG_COOKIE}=${locale}; path=/; max-age=${MAX_AGE}; samesite=lax`;
    // 쿠키만 쓰고 끝내면 화면이 그대로다: <html lang>과 LocaleProvider의
    // 초기값은 app/layout.tsx가 서버에서 정하므로, 그것을 다시 렌더해야 한다.
    router.refresh();
  }

  return (
    <div
      className="hidden sm:inline-flex items-center rounded-full border border-slate-200 p-0.5"
      role="group"
      aria-label="Language / 언어"
    >
      {OPTIONS.map(({ locale, label }) => {
        const active = locale === current;
        return (
          <button
            key={locale}
            type="button"
            onClick={() => choose(locale)}
            aria-pressed={active}
            className={`px-2.5 py-1 text-xs rounded-full ${
              active
                ? "bg-violet-600 text-white font-medium"
                : "text-slate-500 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
