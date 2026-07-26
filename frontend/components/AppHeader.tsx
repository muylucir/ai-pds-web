import Link from "next/link";

import { UserMenu } from "./UserMenu";

export type HeaderTab = "dashboard" | "workspace" | "review" | "prototypes" | "projects";

// Ported from the shared <header> in files/ui/01–03. `projectId` is optional so
// the project-list screen (no project chosen yet) can render the header. When no
// project is selected the per-project tabs render DISABLED (non-clickable, not
// links) — they require a project, so a live link there would navigate to a
// dead `#/…` route and appear broken. Once a project is selected the tabs link
// into that project's routes.
export function AppHeader({
  activeTab,
  projectId,
}: {
  activeTab: HeaderTab;
  projectId?: string;
}) {
  const tab = (key: HeaderTab, label: string, href: string) => {
    const active = key === activeTab;
    const base = "px-3 py-2 rounded-lg text-sm";
    // Per-project tab with no project selected: disabled, not a link.
    if (!projectId) {
      return (
        <span
          className={`${base} text-slate-300 cursor-not-allowed select-none`}
          aria-disabled="true"
          title="프로젝트를 먼저 선택하세요"
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
          <nav className="hidden md:flex items-center gap-1" aria-label="주요 메뉴">
            {tab("dashboard", "대시보드", `${base}/dashboard`)}
            {tab("workspace", "워크스페이스", `${base}/workspace`)}
            {tab("review", "문서 리뷰", `${base}/review`)}
            {tab("prototypes", "프로토타입", `${base}/prototypes`)}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden sm:inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Bedrock 연결됨
          </span>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
