"use client";
// frontend/app/manual/page.tsx — the user manual.
//
// **It opens without login** (PUBLIC_EXACT in lib/auth/gate.ts). People waiting for an account
// must be able to read what this tool is, and this is the link to put in the invitation
// message.
//
// There is no separate language toggle — it follows the header's existing LanguageSwitcher
// (aipds_lang cookie) as is. app/layout.tsx reads that cookie on the server and aligns <html
// lang> and the LocaleProvider, so the manual also comes out in the right language from the
// first paint.
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

  // Observation is turned off while searching: the body is filtered, so "the position being read
  // now" diverges from document order, and filtered-out sections would get highlighted.
  const searching = query.trim() !== "";
  const activeId = useScrollSpy(useMemo(() => anchorIds(visible), [visible]), !searching);

  return (
    <>
      <AppHeader activeTab="manual" />
      <div className="mx-auto max-w-7xl px-6 py-8">
        {/* id="top" — the anchor the "맨 위로" (back to top) at the end of the body points to. */}
        <header id="top" className="mb-8 scroll-mt-20">
          <h1 className="text-2xl font-bold">{t("manual.title")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("manual.subtitle")}</p>
        </header>

        <div className="gap-10 lg:flex">
          {/* Table of contents. The sticky top is the header (h-16=4rem) plus margin. */}
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
