"use client";
// frontend/app/manual/page.tsx — 사용 매뉴얼.
//
// **로그인 없이 열린다**(lib/auth/gate.ts의 PUBLIC_EXACT). 계정을 기다리는
// 사람이 이 도구가 무엇인지 읽을 수 있어야 하고, 초대 메일에 넣을 링크가
// 그것이다.
//
// 언어는 별도 토글을 두지 않는다 — 헤더의 기존 LanguageSwitcher(aipds_lang 쿠키)를
// 그대로 따른다. app/layout.tsx가 서버에서 그 쿠키를 읽어 <html lang>과
// LocaleProvider를 맞추므로, 매뉴얼도 첫 페인트부터 맞는 언어로 나온다.
import { useMemo, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ManualBlocks } from "@/components/manual/ManualBlocks";
import { ManualToc } from "@/components/manual/ManualToc";
import { MANUAL_ORDER, manualFor } from "@/content/manual";
import { useLocale, useT } from "@/lib/i18n/provider";
import { filterSections } from "@/lib/manualSearch";
import { anchorIds } from "@/lib/manualToc";
import { useScrollSpy } from "@/lib/useScrollSpy";

export default function ManualPage() {
  const t = useT();
  const locale = useLocale();
  const [query, setQuery] = useState("");

  const sections = useMemo(() => {
    const content = manualFor(locale);
    return MANUAL_ORDER.map((id) => content[id]);
  }, [locale]);

  const visible = useMemo(() => filterSections(sections, query), [sections, query]);

  // 검색 중에는 감시를 끈다: 본문이 걸러져 있어 "지금 읽는 위치"가 문서
  // 순서와 어긋나고, 걸러진 절을 강조하게 된다.
  const searching = query.trim() !== "";
  const activeId = useScrollSpy(useMemo(() => anchorIds(visible), [visible]), !searching);

  return (
    <>
      <AppHeader activeTab="manual" />
      <div className="mx-auto max-w-7xl px-6 py-8">
        {/* id="top" — 본문 끝의 "맨 위로"가 가리키는 앵커. */}
        <header id="top" className="mb-8 scroll-mt-20">
          <h1 className="text-2xl font-bold">{t("manual.title")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("manual.subtitle")}</p>
        </header>

        <div className="gap-10 lg:flex">
          {/* 목차. sticky의 top은 헤더(h-16=4rem)와 여백을 더한 값이다. */}
          <aside className="mb-8 shrink-0 lg:sticky lg:top-20 lg:mb-0 lg:h-[calc(100vh-6rem)] lg:w-72 lg:overflow-y-auto">
            <ManualToc
              sections={visible}
              activeId={activeId}
              query={query}
              onQueryChange={setQuery}
            />
          </aside>

          <main className="min-w-0 flex-1">
            {visible.length === 0 && (
              <p className="text-sm text-slate-500">{t("manual.searchNoResults")}</p>
            )}
            {visible.map((section) => (
              <section
                key={section.id}
                id={section.id}
                className="mb-14 scroll-mt-20 border-t border-slate-200 pt-6 first:border-t-0 first:pt-0"
              >
                <h2 className="text-xl font-bold text-slate-900">{section.title}</h2>
                <p className="mt-1 text-sm text-slate-500">{section.lede}</p>
                <ManualBlocks blocks={section.blocks} />
              </section>
            ))}
            {visible.length > 0 && (
              <p className="border-t border-slate-200 pt-4 text-sm">
                <a href="#top" className="text-violet-700 hover:underline">
                  {t("manual.backToTop")}
                </a>
              </p>
            )}
          </main>
        </div>
      </div>
    </>
  );
}
