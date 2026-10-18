import { describe, expect, it } from "vitest";
import { gateDecision } from "./gate";

function jwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

const ADMIN = jwt({ "cognito:groups": ["admin"] });
const PM = jwt({ "cognito:groups": ["pm"] });

describe("public paths need no cookie", () => {
  // Survey respondents and prototype evaluators have no account. This list must correspond
  // to the backend's PUBLIC_PATHS.
  it.each([
    "/login",
    "/survey/abc123",
    "/proto/p1/demo/",
    "/proto/p1/demo/styles.css",
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/logout",
    // The manual must be readable before getting an account — the link in the invite email is
    // how people first encounter this tool.
    "/manual",
  ])("allows %s", (path) => {
    expect(gateDecision(path, undefined)).toEqual({ kind: "allow" });
  });

  // /manual is an exact match. Opening subpaths by prefix would let any /manual/* added
  // later inherit the decision.
  it("does not open sub-paths under /manual", () => {
    expect(gateDecision("/manual/secret", undefined))
      .toEqual({ kind: "login", next: "/manual/secret" });
  });
});

describe("protected paths without a cookie", () => {
  it("sends the user to /login with the intended path preserved", () => {
    expect(gateDecision("/projects/p1/dashboard", undefined))
      .toEqual({ kind: "login", next: "/projects/p1/dashboard" });
  });

  it("guards the project list at the root", () => {
    expect(gateDecision("/", undefined)).toEqual({ kind: "login", next: "/" });
  });

  it("guards the admin page", () => {
    expect(gateDecision("/admin/users", undefined))
      .toEqual({ kind: "login", next: "/admin/users" });
  });
});

describe("with a cookie", () => {
  it("lets any role through to project pages", () => {
    expect(gateDecision("/projects/p1/dashboard", PM)).toEqual({ kind: "allow" });
    expect(gateDecision("/projects/p1/dashboard", ADMIN)).toEqual({ kind: "allow" });
  });

  it("lets admin into /admin", () => {
    expect(gateDecision("/admin/users", ADMIN)).toEqual({ kind: "allow" });
  });

  it("bounces pm away from /admin", () => {
    // This is a UX gate — it does not show a pm a screen that would not open for them. The real
    // line of defense is the backend's require_admin (403).
    expect(gateDecision("/admin/users", PM)).toEqual({ kind: "home" });
  });

  it("bounces a roleless token away from /admin", () => {
    expect(gateDecision("/admin/users", jwt({ "cognito:groups": [] })))
      .toEqual({ kind: "home" });
  });

  it("bounces an undecodable cookie away from /admin", () => {
    // If the cookie is broken, the role is unknown — do not open the admin screen.
    expect(gateDecision("/admin/users", "garbage")).toEqual({ kind: "home" });
  });

  it("still allows non-admin pages with an undecodable cookie", () => {
    // If there is a cookie, do not drop the user into a sign-in loop — when the backend returns
    // 401, the frontend sends them to /login then. Bouncing here would cause an endless round
    // trip right after expiry.
    expect(gateDecision("/projects/p1/dashboard", "garbage"))
      .toEqual({ kind: "allow" });
  });
});

describe("api proxy paths", () => {
  it("passes /api/* through — the proxy and backend judge those", () => {
    // If the middleware redirected /api, fetch would receive the HTML sign-in page and break
    // with a JSON parse error. Passing the 401 straight through is correct.
    expect(gateDecision("/api/projects", undefined)).toEqual({ kind: "allow" });
  });
});
