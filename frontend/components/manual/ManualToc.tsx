"use client";
// frontend/components/manual/ManualToc.tsx — the left table of contents and search box.
//
// Anchors are <a href="#id">. Why not next/link: this is navigation within the same page, so
// there is no need to go through the router, and the browser's default anchor scrolling (plus the header margin
// set with scroll-mt) works as-is.
import type { ManualSection } from "@/content/manual";
import { useT } from "@/lib/i18n/provider";
import { headingsOf } from "@/lib/manualToc";

export function ManualToc({
  sections,
  activeId,
  query,
  onQueryChange,
}: {
  /** Sections to show — the filtered list while searching. */
  sections: ManualSection[];
  /** The anchor currently being read. If none, nothing is highlighted. */
  activeId: string | null;
  query: string;
  onQueryChange: (value: string) => void;
}) {
  const t = useT();

  return (
    <nav aria-label={t("manual.tocLabel")} className="space-y-3">
      <div className="relative">
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          aria-label={t("manual.searchLabel")}
          placeholder={t("manual.searchPlaceholder")}
          className="w-full rounded-lg border border-slate-300 px-3 py-1.5 pr-7 text-sm"
        />
        {query !== "" && (
          <button
            type="button"
            onClick={() => onQueryChange("")}
            aria-label={t("manual.searchClear")}
            className="absolute right-1 top-1 rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-100"
          >
            ✕
          </button>
        )}
      </div>

      {query !== "" && (
        <p className="text-xs text-slate-500">
          {sections.length === 0
            ? t("manual.searchNoResults")
            : t("manual.searchResultCount").replace("{n}", String(sections.length))}
        </p>
      )}

      <ol className="space-y-0.5">
        {sections.map((section, index) => {
          // A section is treated as active if it is active itself or if one of its subheadings is active.
          const headings = headingsOf(section);
          const activeHere =
            activeId === section.id || headings.some((h) => h.id === activeId);
          return (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                aria-current={activeId === section.id ? "location" : undefined}
                className={`flex gap-2 rounded-md px-2 py-1.5 text-sm ${
                  activeHere
                    ? "bg-violet-50 font-medium text-violet-700"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                <span aria-hidden="true" className="w-4 shrink-0 text-right text-xs text-slate-400">
                  {index + 1}
                </span>
                <span>{section.title}</span>
              </a>
              {/* Subheadings expand only while that section is being read — always showing the
                  subheadings of all 11 sections would make the TOC as long as the body. */}
              {activeHere && headings.length > 0 && (
                <ul className="mb-1 ml-6 space-y-0.5 border-l border-slate-200 pl-2">
                  {headings.map((h) => (
                    <li key={h.id}>
                      <a
                        href={`#${h.id}`}
                        aria-current={activeId === h.id ? "location" : undefined}
                        className={`block rounded px-2 py-1 text-xs ${
                          activeId === h.id
                            ? "font-medium text-violet-700"
                            : "text-slate-500 hover:text-slate-800"
                        }`}
                      >
                        {h.text}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
