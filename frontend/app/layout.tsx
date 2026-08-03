import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";

import { DEFAULT_LOCALE, isLocale, LANG_COOKIE } from "@/lib/i18n";
import { LocaleProvider } from "@/lib/i18n/provider";

const notoSansKr = Noto_Sans_KR({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-noto-sans-kr",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pathfinder",
  description: "AI-PLC Discovery 웹 서비스",
};

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
