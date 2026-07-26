import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LoginPage from "./page";

// useSearchParams를 쓰는 화면이라 next/navigation을 목한다.
const searchParams = { value: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams.value,
}));

function withParams(query: string) {
  searchParams.value = new URLSearchParams(query);
}

describe("/login", () => {
  it("links to the Hosted UI login route", () => {
    withParams("");
    render(<LoginPage />);
    const link = screen.getByRole("link", { name: /로그인/ });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("carries the next path into the login route", () => {
    // 미들웨어가 붙여준 next를 잃지 않아야 원래 가려던 화면으로 돌아간다.
    withParams("next=%2Fprojects%2Fp1%2Fdashboard");
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /로그인/ }))
      .toHaveAttribute("href", "/api/auth/login?next=%2Fprojects%2Fp1%2Fdashboard");
  });

  it("explains a state mismatch in Korean", () => {
    withParams("error=state_mismatch");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/다시 시도/);
  });

  it("explains a cancelled login", () => {
    withParams("error=access_denied");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/취소/);
  });

  it("explains an unconfigured deployment", () => {
    // 인증 env 없이 배포된 경우 — 무엇을 고쳐야 하는지 알려준다.
    withParams("error=not_configured");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/설정되지 않았습니다/);
  });

  it("falls back to a generic message for an unknown error code", () => {
    withParams("error=weird_thing");
    render(<LoginPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(/로그인에 실패했습니다/);
  });

  it("shows no alert when there is no error", () => {
    withParams("");
    render(<LoginPage />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not reflect the raw error code into the page", () => {
    // 쿼리 파라미터를 그대로 렌더하면 반사형 XSS 표면이 된다.
    withParams("error=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E");
    render(<LoginPage />);
    expect(document.body.innerHTML).not.toContain("onerror");
  });
});
