"use client";
// frontend/components/LanguageSwitcher.tsx — UI language switch (header and sign-in screen).
//
// Why it is split out of AppHeader: AppHeader itself is also a client component, but writing
// the cookie and router.refresh() are a separate responsibility (UserMenu is split out the same way).
//
// The labels are not translated — each language option is always written in that language itself.
// Writing "영어" (English) would leave a user who does not know Korean unable to find their own language.
import { useRouter } from "next/navigation";

import { LANG_COOKIE, type Locale } from "@/lib/i18n";
import { useLocale } from "@/lib/i18n/provider";

const OPTIONS: Array<{ locale: Locale; label: string }> = [
  { locale: "ko", label: "한국어" },
  { locale: "en", label: "English" },
];

// 1 year. As a session cookie it would revert to Korean every time the browser closed, which
// makes the user choose again.
const MAX_AGE = 60 * 60 * 24 * 365;

// In the header it is hidden on narrow screens — the header is short on room, and language is not changed often.
// The sign-in screen is different: it is the only way to pick your language before signing in, so it must always show.
export function LanguageSwitcher({ alwaysVisible = false }: { alwaysVisible?: boolean } = {}) {
  const current = useLocale();
  const router = useRouter();

  function choose(locale: Locale) {
    // Picking the same language again is a no-op. Calling refresh() causes a server round
    // trip, so we do not make a request that changes nothing.
    if (locale === current) return;
    // It is not httpOnly, so it can be written here. secure is not set because the cookie
    // would then not be stored during local http development — this value is not a
    // credential.
    document.cookie =
      `${LANG_COOKIE}=${locale}; path=/; max-age=${MAX_AGE}; samesite=lax`;
    // Writing only the cookie leaves the screen unchanged: <html lang> and LocaleProvider's
    // initial value are decided on the server by app/layout.tsx, so that must re-render.
    router.refresh();
  }

  return (
    <div
      className={`${alwaysVisible ? "inline-flex" : "hidden sm:inline-flex"} items-center rounded-full border border-slate-200 p-0.5`}
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
