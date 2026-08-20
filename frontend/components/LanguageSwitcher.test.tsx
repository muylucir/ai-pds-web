import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

import { LanguageSwitcher } from "./LanguageSwitcher";
import { LocaleProvider } from "@/lib/i18n/provider";

beforeEach(() => {
  refresh.mockClear();
  // 쿠키는 jsdom 문서에 남으므로 테스트 간 지운다.
  document.cookie = "aipds_lang=; max-age=0; path=/";
});

afterEach(() => {
  document.cookie = "aipds_lang=; max-age=0; path=/";
});

describe("LanguageSwitcher", () => {
  it("두 언어를 버튼으로 보여준다", () => {
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    expect(screen.getByRole("button", { name: "한국어" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
  });

  it("현재 로케일을 aria-pressed로 표시한다", () => {
    render(
      <LocaleProvider locale="en">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    expect(screen.getByRole("button", { name: "English" }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "한국어" }))
      .toHaveAttribute("aria-pressed", "false");
  });

  it("클릭하면 쿠키를 쓰고 refresh를 부른다", async () => {
    const user = userEvent.setup();
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(document.cookie).toContain("aipds_lang=en");
    // refresh가 없으면 layout.tsx가 다시 렌더되지 않아 <html lang>과 Provider
    // 초기값이 그대로 남는다 — 쿠키만 바뀌고 화면은 안 바뀐다.
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("이미 그 언어면 아무것도 하지 않는다", async () => {
    const user = userEvent.setup();
    render(
      <LocaleProvider locale="ko">
        <LanguageSwitcher />
      </LocaleProvider>,
    );
    await user.click(screen.getByRole("button", { name: "한국어" }));
    expect(refresh).not.toHaveBeenCalled();
  });
});
