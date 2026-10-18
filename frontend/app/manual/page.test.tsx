// frontend/app/manual/page.test.tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// AppHeader draws LanguageSwitcher, which calls useRouter() — the vitest
// environment has no AppRouter context (same handling as app/page.test.tsx).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

import { MANUAL_ORDER, manualFor } from "@/content/manual";
import { LocaleProvider } from "@/lib/i18n/provider";

import ManualPage from "./page";

const ko = manualFor("ko");
const en = manualFor("en");

/** Hangul syllables. */
const HANGUL = /[가-힣]/;

describe("매뉴얼 화면", () => {
  it("모든 절을 순서대로 그린다", () => {
    render(<ManualPage />);
    expect(screen.getByRole("heading", { level: 1, name: "사용 매뉴얼" })).toBeInTheDocument();
    for (const id of MANUAL_ORDER) {
      expect(screen.getByRole("heading", { level: 2, name: ko[id].title })).toBeInTheDocument();
    }
  });

  it("목차의 앵커가 실제로 그려진 절의 id와 맞는다", () => {
    const { container } = render(<ManualPage />);
    // If a table-of-contents link points to something not in the document, clicking it does nothing — an
    // inconspicuous breakage, so it is caught here.
    const nav = screen.getByRole("navigation", { name: "매뉴얼 목차" });
    const hrefs = Array.from(nav.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((h) => h.startsWith("#"));
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(container.querySelector(`#${href.slice(1)}`), href).not.toBeNull();
    }
  });

  it("목업의 문구를 앱 딕셔너리에서 가져온다", () => {
    render(<ManualPage />);
    // The button in the project creation mockup — exactly the value of "project.create" in ko.ts.
    // If the mockup wrote the sentence directly, this assertion would lose its meaning.
    expect(screen.getAllByText("프로젝트 생성").length).toBeGreaterThan(0);
    expect(screen.getAllByText("승인 게이트").length).toBeGreaterThan(0);
  });
});

describe("매뉴얼 화면 — 영어", () => {
  it("영어 로케일에서 절 제목과 껍데기가 영어다", () => {
    render(
      <LocaleProvider locale="en">
        <ManualPage />
      </LocaleProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "User manual" })).toBeInTheDocument();
    for (const id of MANUAL_ORDER) {
      expect(screen.getByRole("heading", { level: 2, name: en[id].title })).toBeInTheDocument();
    }
  });

  it("영어 화면에 한국어가 남아 있지 않다 — 언어 이름 하나만 예외다", () => {
    // This assertion covers the core risk of this screen: the manual body does not go through the
    // dictionary (content/manual/en/), so if one English section is left out, Korean shows up in its
    // place as is. Types catch a **missing** section, but not Korean left inside an English
    // file.
    //
    // "한국어" is the exception — the on-screen language switch really says that, so the English
    // manual must refer to it with those characters too (the LANGUAGE_LABEL convention,
    // the same exception in lib/i18n/noHardcodedKorean.test.ts).
    const { container } = render(
      <LocaleProvider locale="en">
        <ManualPage />
      </LocaleProvider>,
    );
    const text = (container.textContent ?? "").replaceAll("한국어", "");
    const offenders = text.split("\n").filter((line) => HANGUL.test(line));
    expect(offenders, offenders.join("\n")).toEqual([]);
  });
});

describe("매뉴얼 검색", () => {
  it("일치하는 절만 남긴다", async () => {
    const user = userEvent.setup();
    render(<ManualPage />);
    await user.type(screen.getByRole("searchbox", { name: "매뉴얼 검색" }), "설문");

    expect(screen.getByRole("heading", { level: 2, name: ko.survey.title })).toBeInTheDocument();
    // Sections without the search term disappear.
    expect(screen.queryByRole("heading", { level: 2, name: ko.admin.title })).toBeNull();
  });

  it("일치하는 것이 없으면 그렇다고 말한다", async () => {
    const user = userEvent.setup();
    render(<ManualPage />);
    await user.type(
      screen.getByRole("searchbox", { name: "매뉴얼 검색" }),
      "존재하지않는단어xyz",
    );
    expect(screen.getAllByText("일치하는 내용이 없습니다.").length).toBeGreaterThan(0);
  });

  it("지우기를 누르면 전체가 돌아온다", async () => {
    const user = userEvent.setup();
    render(<ManualPage />);
    const box = screen.getByRole("searchbox", { name: "매뉴얼 검색" });
    await user.type(box, "설문");
    expect(screen.queryByRole("heading", { level: 2, name: ko.admin.title })).toBeNull();

    await user.click(screen.getByRole("button", { name: "검색어 지우기" }));
    expect(screen.getByRole("heading", { level: 2, name: ko.admin.title })).toBeInTheDocument();
  });
});
