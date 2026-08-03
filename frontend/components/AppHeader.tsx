"use client";
import Link from "next/link";

import type { Locale } from "@/lib/i18n";
import { useT } from "@/lib/i18n/provider";

import { LanguageSwitcher } from "./LanguageSwitcher";
import { UserMenu } from "./UserMenu";

export type HeaderTab = "dashboard" | "workspace" | "review" | "prototypes" | "projects";

// 언어 배지의 표기. 딕셔너리를 타지 않는다 — 언어 이름은 항상 그 언어 자체로
// 적는다(LanguageSwitcher의 라벨과 같은 규약). "한국어"를 영어 UI에서 "Korean"
// 으로 바꾸면 그 프로젝트의 문서가 실제로 어떤 글자로 나오는지 흐려진다.
const LANGUAGE_LABEL: Record<Locale, string> = { ko: "한국어", en: "English" };

// Ported from the shared <header> in files/ui/01–03. `projectId` is optional so
// the project-list screen (no project chosen yet) can render the header. When no
// project is selected the per-project tabs render DISABLED (non-clickable, not
// links) — they require a project, so a live link there would navigate to a
// dead `#/…` route and appear broken. Once a project is selected the tabs link
// into that project's routes.
export function AppHeader({
  activeTab,
  projectId,
  modelLabel,
  projectLanguage,
}: {
  activeTab: HeaderTab;
  projectId?: string;
  // 이 프로젝트가 도는 모델의 표시 이름. null/undefined면 배지를 그리지
  // 않는다 — 프로젝트가 없는 화면이거나, 모델 미지정(서버 env 기본값)이다.
  modelLabel?: string | null;
  // 이 프로젝트의 **생성물 언어**. UI 로케일과 다를 수 있고, 그것이 정상이다 —
  // 영어 UI로 한국어 프로젝트를 열면 문서는 한국어로 나온다. 이 배지가 그
  // 사실을 화면에 드러낸다. null/undefined면 그리지 않는다(프로젝트 없는
  // 화면, 또는 언어를 모르는 구 백엔드 응답).
  projectLanguage?: Locale | null;
}) {
  const t = useT();

  const tab = (key: HeaderTab, label: string, href: string) => {
    const active = key === activeTab;
    const base = "px-3 py-2 rounded-lg text-sm";
    // Per-project tab with no project selected: disabled, not a link.
    if (!projectId) {
      return (
        <span
          className={`${base} text-slate-300 cursor-not-allowed select-none`}
          aria-disabled="true"
          title={t("nav.needProject")}
        >
          {label}
        </span>
      );
    }
    const cls = active
      ? `${base} bg-violet-50 text-violet-700 font-medium`
      : `${base} hover:bg-slate-100 text-slate-600`;
    return (
      <Link href={href} className={cls} aria-current={active ? "page" : undefined}>
        {label}
      </Link>
    );
  };

  const base = `/projects/${projectId}`;
  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Link href="/" className="flex items-center gap-2 font-bold text-lg text-violet-700">
            <span className="w-8 h-8 rounded-lg bg-violet-600 text-white flex items-center justify-center text-sm font-bold">
              AI
            </span>
            Pathfinder
          </Link>
          <nav className="hidden md:flex items-center gap-1" aria-label={t("nav.ariaLabel")}>
            {tab("dashboard", t("nav.dashboard"), `${base}/dashboard`)}
            {tab("workspace", t("nav.workspace"), `${base}/workspace`)}
            {tab("review", t("nav.review"), `${base}/review`)}
            {tab("prototypes", t("nav.prototypes"), `${base}/prototypes`)}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {projectLanguage && (
            <span
              data-testid="language-badge"
              title={t("header.languageBadgeTitle")}
              className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-slate-50 text-slate-600 border border-slate-200"
            >
              {LANGUAGE_LABEL[projectLanguage]}
            </span>
          )}
          {modelLabel && (
            <span
              data-testid="model-badge"
              title={t("header.modelBadgeTitle")}
              className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-violet-50 text-violet-700 border border-violet-200"
            >
              {modelLabel}
            </span>
          )}
          <span className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> {t("header.bedrockConnected")}
          </span>
          <LanguageSwitcher />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
