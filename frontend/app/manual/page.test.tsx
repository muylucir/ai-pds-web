// frontend/app/manual/page.test.tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// AppHeader가 LanguageSwitcher를 그리고 그것이 useRouter()를 부른다 — vitest
// 환경에는 AppRouter 컨텍스트가 없다(app/page.test.tsx와 같은 처리).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

import { MANUAL_ORDER, manualFor } from "@/content/manual";
import { LocaleProvider } from "@/lib/i18n/provider";

import ManualPage from "./page";

const ko = manualFor("ko");
const en = manualFor("en");

/** 한글 음절. */
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
    // 목차 링크가 가리키는 곳이 문서에 없으면 눌러도 아무 일이 없다 — 눈에
    // 띄지 않는 고장이므로 여기서 잡는다.
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
    // 프로젝트 생성 목업의 버튼 — ko.ts의 "project.create" 값 그대로다.
    // 목업에 문장을 직접 쓰면 이 단정이 의미를 잃는다.
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
    // 이 단정이 이 화면의 핵심 위험을 덮는다: 매뉴얼 본문은 딕셔너리를 타지
    // 않으므로(content/manual/en/), 영어 절을 하나 빠뜨리면 그 자리에 한국어가
    // 그대로 나온다. 타입이 절 **누락**은 잡지만, 영어 파일 안에 한국어를
    // 남겨 두는 것은 잡지 못한다.
    //
    // "한국어"는 예외다 — 화면의 언어 스위치에 실제로 그렇게 적혀 있으므로
    // 영어 매뉴얼도 그 글자로 가리켜야 한다(LANGUAGE_LABEL 규약,
    // lib/i18n/noHardcodedKorean.test.ts의 같은 예외).
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
    // 검색어가 없는 절은 사라진다.
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
