// 미들웨어 리다이렉트의 Location 헤더 계약.
//
// 실측 배포 버그: CloudFront(https://d1...cloudfront.net) 뒤에서 최초 접속이
// `https://localhost:3000/login?next=%2F`로 리다이렉트됐다. Next 15의 미들웨어는
// `req.url`을 Host 헤더가 아니라 `next start`가 아는 자체 origin으로 조립하므로,
// `new URL("/login", req.url)`은 프록시 뒤에서 항상 내부 주소를 새게 만든다
// (인스턴스에서 확인: Host를 무엇으로 주든 Location이 localhost:3000).
//
// 해법은 절대 URL을 만들지 않는 것이다. Location이 상대 경로면 브라우저가 현재
// 오리진 기준으로 해석하므로, 프록시·CloudFront·로컬 어디서든 맞는다.
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";

// 프록시 뒤 상황 재현: 요청 URL의 오리진(Next 내부 주소)과 사용자가 보는
// 오리진(CloudFront)이 다르다.
const INTERNAL = "http://localhost:3000";

function req(path: string, opts: { token?: string } = {}) {
  const r = new NextRequest(new URL(path, INTERNAL), { method: "GET" });
  if (opts.token) r.cookies.set(ACCESS_COOKIE, opts.token);
  return r;
}

// 서명을 검증하지 않는 게이트라 payload 모양만 맞으면 된다(middleware.ts 주석).
function tokenFor(groups: string[]): string {
  const body = Buffer.from(JSON.stringify({ "cognito:groups": groups }))
    .toString("base64url");
  return `x.${body}.y`;
}

describe("middleware의 Location은 오리진을 새지 않는다", () => {
  it("sends an unauthenticated visitor to a RELATIVE /login", () => {
    const res = middleware(req("/"));
    const location = res.headers.get("location")!;
    // 절대 URL이면 프록시 뒤에서 내부 주소가 사용자에게 노출된다.
    expect(location).not.toContain("localhost");
    expect(location).not.toMatch(/^https?:\/\//);
    expect(location.startsWith("/login")).toBe(true);
  });

  it("keeps the next param so the user returns to where they were going", () => {
    const res = middleware(req("/projects/p1/dashboard"));
    const location = res.headers.get("location")!;
    expect(location).toBe("/login?next=%2Fprojects%2Fp1%2Fdashboard");
  });

  it("sends a pm who typed an admin URL to a RELATIVE home", () => {
    const res = middleware(req("/admin/users", { token: tokenFor(["pm"]) }));
    const location = res.headers.get("location")!;
    expect(location).not.toContain("localhost");
    expect(location).not.toMatch(/^https?:\/\//);
    expect(location).toBe("/");
  });

  it("lets an allowed request through without a Location", () => {
    const res = middleware(req("/", { token: tokenFor(["admin"]) }));
    expect(res.headers.get("location")).toBeNull();
  });

  it("does not gate the public survey route", () => {
    // 계정 없는 최종 사용자용 경로 — 로그인으로 보내면 설문을 못 받는다.
    const res = middleware(req("/survey/tok123"));
    expect(res.headers.get("location")).toBeNull();
  });
});
