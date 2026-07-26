import { describe, expect, it } from "vitest";
import { redirectTo, redirectToLogin } from "./redirectTo";

describe("redirectTo", () => {
  it("emits a relative Location so the browser keeps the current origin", () => {
    // 실측 배포 버그의 핵심: 절대 URL이면 프록시 뒤에서 내부 주소가 샌다.
    const res = redirectTo("/projects/p1");
    expect(res.headers.get("location")).toBe("/projects/p1");
    expect(res.status).toBe(307);
  });

  it("accepts an explicit 302 for OAuth callback semantics", () => {
    expect(redirectTo("/", 302).status).toBe(302);
  });

  it("refuses a protocol-relative path that would go off-site", () => {
    // "//evil.example"는 상대값처럼 보이지만 브라우저가 오프사이트로 해석한다.
    expect(redirectTo("//evil.example").headers.get("location")).toBe("/");
  });

  it("refuses an absolute URL", () => {
    expect(redirectTo("https://evil.example/x").headers.get("location")).toBe("/");
  });

  it("refuses a path that does not start with a slash", () => {
    expect(redirectTo("login").headers.get("location")).toBe("/");
  });

  it("never emits a Location containing an origin", () => {
    for (const p of ["/", "/login", "//evil.example", "https://evil.example", "javascript:alert(1)"]) {
      const location = redirectTo(p).headers.get("location")!;
      expect(location).not.toMatch(/^[a-z]+:/i);
      expect(location).not.toMatch(/^\/\//);
    }
  });
});

describe("redirectToLogin", () => {
  it("goes to a relative /login with no reason", () => {
    expect(redirectToLogin().headers.get("location")).toBe("/login");
  });

  it("carries the reason as an encoded query param", () => {
    expect(redirectToLogin("state_mismatch").headers.get("location"))
      .toBe("/login?error=state_mismatch");
  });

  it("encodes a reason that would otherwise break the query", () => {
    // Hosted UI가 준 error 값을 그대로 싣는 경로가 있다 — 인코딩하지 않으면
    // 쿼리 구조가 깨지거나 파라미터가 주입된다.
    expect(redirectToLogin("a&b=c").headers.get("location"))
      .toBe("/login?error=a%26b%3Dc");
  });
});
