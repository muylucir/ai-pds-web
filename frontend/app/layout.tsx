import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";

import { DEFAULT_LOCALE, dictFor, isLocale, LANG_COOKIE } from "@/lib/i18n";
import { LocaleProvider } from "@/lib/i18n/provider";

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-noto-sans-kr",
  display: "swap",
});

// Why `generateMetadata` rather than a static `metadata` object: the description must follow the
// UI language, and a static object is evaluated before the cookie is read, so it cannot know the
// locale. Next calls this function on every request, so cookies() can be used here.
export async function generateMetadata(): Promise<Metadata> {
  const raw = (await cookies()).get(LANG_COOKIE)?.value;
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return {
    title: "AI-PDS",   // product name — not translated
    description: dictFor(locale)["app.description"],
  };
}

// This is the only place in this app that calls cookies(). The locale is read on the server
// to get <html lang> right on the first paint — localStorage is not visible on the server,
// causing a flicker where it is drawn in Korean and then switches to English.
//
// Note that it became async: Next 15's cookies() returns a Promise.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const raw = (await cookies()).get(LANG_COOKIE)?.value;
  // Unknown values (hand-edited cookies, old values) silently fall back to the default.
  // Throwing would make every page a 500, and there is no reason for that over one language.
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return (
    <html lang={locale} className={notoSansKr.variable}>
      <body className="font-sans">
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
