"use client";
// frontend/lib/i18n/provider.tsx — UI 로케일을 컴포넌트 트리에 내려준다.
//
// 서버용 경로(getT())가 없는 이유: 이 앱에서 서버에서 렌더되는 것은
// app/layout.tsx와 redirect()만 하는 두 페이지뿐이고, 그 셋에는 UI 문자열이
// 없다. `"use client"`가 없는 컴포넌트가 26개 있지만(AppHeader 등) 전부
// 클라이언트 페이지 트리 아래에서만 임포트되므로 이미 클라이언트 컴포넌트다 —
// Next.js는 클라이언트 컴포넌트가 임포트한 것을 클라이언트 번들에 넣는다.
import { createContext, useContext, useMemo } from "react";

import { DEFAULT_LOCALE, dictFor, type Dict, type Locale } from "./index";

// null이 "Provider 밖"을 뜻한다. 기본값을 DEFAULT_LOCALE로 두지 않는 이유는
// 없지만(결과가 같다), null이 의도를 드러낸다 — 폴백이 일어났다는 사실이
// 컨텍스트 값에 남는다.
const LocaleContext = createContext<Locale | null>(null);

export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: React.ReactNode;
}) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

/** 현재 UI 로케일. Provider 밖에서는 DEFAULT_LOCALE. */
export function useLocale(): Locale {
  return useContext(LocaleContext) ?? DEFAULT_LOCALE;
}

/**
 * 번역 함수.
 *
 * **Provider 밖에서 던지지 않는다.** 기존 컴포넌트 테스트 535건이 Provider로
 * 감싸지 않고 render()하므로, 던지면 그 전부가 깨진다. 폴백은 DEFAULT_LOCALE
 * (=ko)이고, 그것이 그 테스트들이 단정하는 화면이다.
 */
export function useT(): (key: keyof Dict) => string {
  const locale = useLocale();
  // 로케일이 바뀌지 않는 한 같은 함수를 돌려준다 — 이 함수를 의존성 배열에
  // 넣는 useEffect/useMemo가 매 렌더마다 다시 돌지 않게 한다.
  return useMemo(() => {
    const dict = dictFor(locale);
    return (key: keyof Dict) => dict[key];
  }, [locale]);
}
