"use client";
// frontend/components/manual/ManualToc.tsx — 좌측 목차와 검색창.
//
// 앵커는 <a href="#id">다. next/link가 아닌 이유: 같은 페이지 안의 이동이므로
// 라우터를 태울 필요가 없고, 브라우저의 기본 앵커 스크롤(그리고 scroll-mt로
// 준 헤더 여백)이 그대로 동작한다.
import type { ManualSection } from "@/content/manual";
import { useT } from "@/lib/i18n/provider";
import { headingsOf } from "@/lib/manualToc";

export function ManualToc({
  sections,
  activeId,
  query,
  onQueryChange,
}: {
  /** 보여줄 절 — 검색 중이면 걸러진 목록이다. */
  sections: ManualSection[];
  /** 지금 읽고 있는 앵커. 없으면 강조하지 않는다. */
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
          // 절 자신이 활성이거나, 그 절의 소제목이 활성이면 절도 활성으로 본다.
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
              {/* 소제목은 그 절을 읽고 있을 때만 펼친다 — 11개 절의 소제목을
                  항상 다 보이면 목차가 본문만큼 길어진다. */}
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
