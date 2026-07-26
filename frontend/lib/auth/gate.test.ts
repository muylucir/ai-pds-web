import { describe, expect, it } from "vitest";
import { gateDecision } from "./gate";

function jwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

const ADMIN = jwt({ "cognito:groups": ["admin"] });
const PM = jwt({ "cognito:groups": ["pm"] });

describe("public paths need no cookie", () => {
  // 설문 응답자와 프로토타입 평가자는 계정이 없다. 이 목록이 백엔드의
  // PUBLIC_PATHS와 대응해야 한다.
  it.each([
    "/login",
    "/survey/abc123",
    "/proto/p1/demo/",
    "/proto/p1/demo/styles.css",
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/logout",
  ])("allows %s", (path) => {
    expect(gateDecision(path, undefined)).toEqual({ kind: "allow" });
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
    // UX 게이트다 — pm에게 열리지 않을 화면을 보여주지 않는다. 실제 방어선은
    // 백엔드의 require_admin(403)이다.
    expect(gateDecision("/admin/users", PM)).toEqual({ kind: "home" });
  });

  it("bounces a roleless token away from /admin", () => {
    expect(gateDecision("/admin/users", jwt({ "cognito:groups": [] })))
      .toEqual({ kind: "home" });
  });

  it("bounces an undecodable cookie away from /admin", () => {
    // 쿠키가 깨졌으면 역할을 알 수 없다 — 관리 화면을 열지 않는다.
    expect(gateDecision("/admin/users", "garbage")).toEqual({ kind: "home" });
  });

  it("still allows non-admin pages with an undecodable cookie", () => {
    // 쿠키가 있으면 로그인 루프에 빠뜨리지 않는다 — 백엔드가 401을 주면 그때
    // 프론트가 /login으로 보낸다. 여기서 되돌리면 만료 직후 무한 왕복이 된다.
    expect(gateDecision("/projects/p1/dashboard", "garbage"))
      .toEqual({ kind: "allow" });
  });
});

describe("api proxy paths", () => {
  it("passes /api/* through — the proxy and backend judge those", () => {
    // 미들웨어가 /api를 리다이렉트하면 fetch가 HTML 로그인 페이지를 받아
    // JSON 파싱 오류로 깨진다. 401을 그대로 흘려보내는 것이 맞다.
    expect(gateDecision("/api/projects", undefined)).toEqual({ kind: "allow" });
  });
});
