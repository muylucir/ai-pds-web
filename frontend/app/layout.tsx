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

// 정적 `metadata` 객체가 아니라 `generateMetadata`인 이유: description이 UI
// 언어를 따라야 하고, 정적 객체는 쿠키를 읽기 전에 평가되므로 로케일을 알 수
// 없다. Next가 요청마다 이 함수를 부르므로 여기서는 cookies()를 쓸 수 있다.
export async function generateMetadata(): Promise<Metadata> {
  const raw = (await cookies()).get(LANG_COOKIE)?.value;
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return {
    title: "AI-PDS",   // 제품명 — 번역하지 않는다
    description: dictFor(locale)["app.description"],
  };
}

// 이 앱에서 cookies()를 부르는 유일한 지점이다. 로케일을 서버에서 읽는 이유는
// <html lang>을 첫 페인트에 맞추기 위해서다 — localStorage는 서버에서 보이지
// 않아 한국어로 그린 뒤 영어로 바뀌는 깜빡임이 생긴다.
//
// async가 된 것에 주의: Next 15의 cookies()는 Promise를 돌려준다.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const raw = (await cookies()).get(LANG_COOKIE)?.value;
  // 알 수 없는 값(손으로 고친 쿠키, 옛 값)은 조용히 기본값으로 떨어진다.
  // 던지면 모든 페이지가 500이 되는데, 언어 하나 때문에 그럴 이유가 없다.
  const locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  return (
    <html lang={locale} className={notoSansKr.variable}>
      <body className="font-sans">
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
