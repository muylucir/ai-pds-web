import { describe, expect, it } from "vitest";
import { redirectTo, redirectToLogin } from "./redirectTo";

describe("redirectTo", () => {
  it("emits a relative Location so the browser keeps the current origin", () => {
    // The core of the observed deployment bug: an absolute URL leaks the internal address behind the proxy.
    const res = redirectTo("/projects/p1");
    expect(res.headers.get("location")).toBe("/projects/p1");
    expect(res.status).toBe(307);
  });

  it("accepts an explicit 302 for OAuth callback semantics", () => {
    expect(redirectTo("/", 302).status).toBe(302);
  });

  it("refuses a protocol-relative path that would go off-site", () => {
    // "//evil.example" looks relative, but the browser resolves it as off-site.
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
    // There is a path that carries the error value from the Hosted UI as is — without encoding,
    // the query structure breaks or parameters get injected.
    expect(redirectToLogin("a&b=c").headers.get("location"))
      .toBe("/login?error=a%26b%3Dc");
  });
});
