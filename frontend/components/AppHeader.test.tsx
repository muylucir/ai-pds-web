import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

// AppHeader draws LanguageSwitcher, which calls useRouter() —
// in unit tests where the app router is not mounted, that hook throws "invariant expected app
// router to be mounted". The switch's behavior itself is verified by
// LanguageSwitcher.test.tsx, so here we only make it mount.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

import { AppHeader } from "./AppHeader";
import { LocaleProvider } from "@/lib/i18n/provider";

describe("AppHeader without a selected project", () => {
  it("renders the brand and shows the per-project tabs DISABLED (not links)", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.getByText("AI-PDS")).toBeInTheDocument();
    // The per-project tabs require a project, so with none selected they must
    // not be clickable links (a live link would navigate to a dead #/… route
    // and appear broken). They render as disabled spans instead.
    for (const label of ["대시보드", "워크스페이스", "문서 리뷰", "프로토타입"]) {
      expect(screen.queryByRole("link", { name: label })).toBeNull();
      expect(screen.getByText(label)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("no longer shows the retired 질문 답변 / 빌드 캔버스 tabs", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.queryByText("질문 답변")).not.toBeInTheDocument();
    expect(screen.queryByText("빌드 캔버스")).not.toBeInTheDocument();
  });
});

describe("AppHeader with a selected project", () => {
  it("renders the per-project tabs as links into that project's routes", () => {
    render(<AppHeader activeTab="dashboard" projectId="ws-m" />);
    expect(screen.getByRole("link", { name: "대시보드" })).toHaveAttribute(
      "href", "/projects/ws-m/dashboard",
    );
    expect(screen.getByRole("link", { name: "워크스페이스" })).toHaveAttribute(
      "href", "/projects/ws-m/workspace",
    );
    expect(screen.getByRole("link", { name: "문서 리뷰" })).toHaveAttribute(
      "href", "/projects/ws-m/review",
    );
    expect(screen.getByRole("link", { name: "프로토타입" })).toHaveAttribute(
      "href", "/projects/ws-m/prototypes",
    );
  });

  it("marks the active tab with aria-current", () => {
    render(<AppHeader activeTab="workspace" projectId="ws-m" />);
    expect(screen.getByRole("link", { name: "워크스페이스" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "대시보드" })).not.toHaveAttribute("aria-current");
  });

  it("no longer shows the retired 질문 답변 / 빌드 캔버스 tabs", () => {
    render(<AppHeader activeTab="workspace" projectId="ws-m" />);
    expect(screen.queryByRole("link", { name: "질문 답변" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "빌드 캔버스" })).not.toBeInTheDocument();
  });
});

describe("AppHeader model badge", () => {
  it("shows the model badge when a label is given", () => {
    render(<AppHeader activeTab="workspace" projectId="p1" modelLabel="Opus 5" />);
    expect(screen.getByText("Opus 5")).toBeInTheDocument();
  });

  it("shows no model badge without a label", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.queryByTestId("model-badge")).toBeNull();
  });
});

describe("AppHeader 로케일", () => {
  it("영어 로케일에서 탭 라벨이 영어다", () => {
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="dashboard" projectId="ws-m" />
      </LocaleProvider>,
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href", "/projects/ws-m/dashboard",
    );
    expect(screen.getByRole("link", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Document Review" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Prototypes" })).toBeInTheDocument();
  });

  it("Provider 없이 렌더하면 한국어다 — 기존 테스트가 그대로 통과하는 근거", () => {
    render(<AppHeader activeTab="dashboard" projectId="ws-m" />);
    expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  });

  it("언어 스위치를 그린다", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.getByRole("group", { name: "Language / 언어" })).toBeInTheDocument();
  });
});

describe("AppHeader 언어 배지", () => {
  it("프로젝트 언어를 배지로 보여준다", () => {
    render(<AppHeader activeTab="dashboard" projectId="ws-m" projectLanguage="en" />);
    const badge = screen.getByTestId("language-badge");
    expect(badge).toHaveTextContent("English");
  });

  it("UI 언어와 프로젝트 언어가 달라도 프로젝트 언어를 보여준다", () => {
    // Exposing exactly this situation is the purpose of this badge — opening a Korean project from
    // the English UI produces documents in Korean.
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="dashboard" projectId="ws-m" projectLanguage="ko" />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("language-badge")).toHaveTextContent("한국어");
  });

  it("언어를 모르면 배지를 그리지 않는다", () => {
    render(<AppHeader activeTab="dashboard" projectId="ws-m" />);
    expect(screen.queryByTestId("language-badge")).toBeNull();
  });
});

describe("AppHeader 매뉴얼 링크", () => {
  it("프로젝트가 없어도 매뉴얼로 가는 링크가 살아 있다", () => {
    // The opposite of the four tabs — per-project tabs are dead without a project, but the manual is
    // needed most when there is no project (and before login).
    render(<AppHeader activeTab="projects" />);
    expect(screen.getByRole("link", { name: "매뉴얼" }))
      .toHaveAttribute("href", "/manual");
  });

  it("매뉴얼 화면에서는 링크가 아니라 현재 위치로 표시한다", () => {
    render(<AppHeader activeTab="manual" />);
    expect(screen.queryByRole("link", { name: "매뉴얼" })).toBeNull();
    expect(screen.getByText("매뉴얼")).toHaveAttribute("aria-current", "page");
  });

  it("영어 로케일에서 라벨이 영어다", () => {
    render(
      <LocaleProvider locale="en">
        <AppHeader activeTab="projects" />
      </LocaleProvider>,
    );
    expect(screen.getByRole("link", { name: "Manual" }))
      .toHaveAttribute("href", "/manual");
  });
});
