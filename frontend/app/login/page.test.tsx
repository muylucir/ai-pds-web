import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LoginPage from "./page";

// The screen uses useSearchParams, so next/navigation is mocked. suspend is a switch that reproduces the state
// where the params are not yet resolved (prerendering at build time).
const searchParams = { value: new URLSearchParams(), suspend: false };
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => {
    if (searchParams.suspend) throw new Promise<void>(() => {});
    return searchParams.value;
  },
  // Called so the language switch re-renders in the chosen language (LanguageSwitcher).
  useRouter: () => ({ refresh }),
}));

function withParams(query: string) {
  searchParams.value = new URLSearchParams(query);
  searchParams.suspend = false;
}

describe("/login", () => {
  it("links to the Hosted UI login route", () => {
    withParams("");
    render(<LoginPage />);
    const link = screen.getByRole("link", { name: /로그인/ });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("carries the next path into the login route", () => {
    // Not losing the next the middleware attached is what returns the user to the screen they were headed to.
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
    // Deployed without the auth env — it tells you what to fix.
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

  it("renders the login card while search params are still unresolved", () => {
    // Next 15 prerenders /login at build time. If useSearchParams() suspends at that point,
    // the build breaks unless there is a Suspense boundary ("should be wrapped in a
    // suspense boundary"). Only the inside of the boundary may suspend, and the login button must stay in the server HTML
    // — login must be clickable even if JS dies.
    searchParams.suspend = true;
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: /로그인/ }))
      .toHaveAttribute("href", "/api/auth/login");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not reflect the raw error code into the page", () => {
    // Rendering the query parameter as is would create a reflected XSS surface.
    withParams("error=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E");
    render(<LoginPage />);
    expect(document.body.innerHTML).not.toContain("onerror");
    // Check that it is replaced with generic text, rather than passing simply by rendering nothing.
    expect(screen.getByRole("alert")).toHaveTextContent(/로그인에 실패했습니다/);
  });

  it("offers a language choice before signing in", async () => {
    // This is the only way to pick one's language before logging in — it does not hide even on narrow screens.
    withParams("");
    render(<LoginPage />);
    const group = screen.getByRole("group", { name: /Language/ });
    expect(group.className).not.toMatch(/(^|\s)hidden(\s|$)/);
    const english = screen.getByRole("button", { name: "English" });
    english.click();
    expect(document.cookie).toMatch(/aipds_lang=en/);
    expect(refresh).toHaveBeenCalled();
  });
});
