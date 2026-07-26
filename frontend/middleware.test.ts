// 미들웨어 리다이렉트의 Location 헤더 계약.
//
// 실측 배포 버그 두 개를 함께 고정한다.
//
// 1) `new URL(path, req.url)`은 프록시 뒤에서 내부 주소를 샌다. CloudFront로
//    접속하면 Location이 `https://localhost:3000/login?next=%2F`이었다. Next
//    15는 req.url을 Host 헤더가 아니라 `next start`가 아는 자체 origin으로
//    조립하기 때문이다(EC2에서 확인: Host를 CloudFront 도메인으로 줘도,
//    X-Forwarded-Host를 줘도 결과가 같았다).
//
// 2) 그렇다고 상대 Location을 쓸 수도 없다. **미들웨어는 Edge 런타임에서 돌고,
//    그 런타임이 응답의 location 헤더를 내부적으로 new URL()로 파싱한다** —
//    상대값이면 `TypeError: Invalid URL, input: '/login?next=%2F'`로 500이
//    난다(실측). route handler(Node 런타임)는 상대값이 통하지만 미들웨어는 안
//    된다. 이 차이가 유닛 테스트로 안 잡혔던 이유는 테스트가 NextResponse
//    객체만 보고 런타임의 헤더 파싱을 거치지 않기 때문이다.
//
// 결론: 미들웨어는 절대 URL을 쓰되 origin을 req.url이 아니라 **Host 헤더**에서
// 얻는다. 그게 사용자가 실제로 접속한 호스트다(nginx가 proxy_set_header Host로
// 전달한다).
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";
import { ACCESS_COOKIE } from "@/lib/auth/cookies";

// 프록시 뒤 상황: 요청 URL의 오리진은 Next 내부 주소이고, 사용자가 실제로
// 접속한 호스트는 Host 헤더에만 있다.
const INTERNAL = "http://localhost:3000";
const PUBLIC_HOST = "d1wyghhz9isoih.cloudfront.net";

function req(path: string, opts: { token?: string; host?: string | null;
                                   proto?: string } = {}) {
  const headers = new Headers();
  if (opts.host !== null) headers.set("host", opts.host ?? PUBLIC_HOST);
  headers.set("x-forwarded-proto", opts.proto ?? "https");
  const r = new NextRequest(new URL(path, INTERNAL), { method: "GET", headers });
  if (opts.token) r.cookies.set(ACCESS_COOKIE, opts.token);
  return r;
}

// 서명을 검증하지 않는 게이트라 payload 모양만 맞으면 된다(middleware.ts 주석).
function tokenFor(groups: string[]): string {
  const body = Buffer.from(JSON.stringify({ "cognito:groups": groups }))
    .toString("base64url");
  return `x.${body}.y`;
}

describe("middleware의 Location은 사용자가 접속한 호스트를 쓴다", () => {
  it("sends an unauthenticated visitor to /login on the PUBLIC host", () => {
    const location = middleware(req("/")).headers.get("location")!;
    // 내부 주소가 새면 사용자는 접속 불가 상태가 된다.
    expect(location).not.toContain("localhost");
    expect(location).toBe(`https://${PUBLIC_HOST}/login?next=%2F`);
  });

  it("emits an ABSOLUTE url — the Edge runtime rejects a relative Location", () => {
    // 상대값이면 런타임이 new URL()에서 던져 500이 된다(실측 배포 오류).
    const location = middleware(req("/")).headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
    expect(location).toMatch(/^https:\/\//);
  });

  it("keeps the next param so the user returns to where they were going", () => {
    const location = middleware(req("/projects/p1/dashboard")).headers.get("location")!;
    expect(location)
      .toBe(`https://${PUBLIC_HOST}/login?next=%2Fprojects%2Fp1%2Fdashboard`);
  });

  it("sends a pm who typed an admin URL home on the PUBLIC host", () => {
    const location = middleware(req("/admin/users", { token: tokenFor(["pm"]) }))
      .headers.get("location")!;
    expect(location).not.toContain("localhost");
    expect(location).toBe(`https://${PUBLIC_HOST}/`);
  });

  it("honours the forwarded protocol so local http dev is not forced to https", () => {
    const location = middleware(req("/", { host: "localhost:3000", proto: "http" }))
      .headers.get("location")!;
    expect(location).toBe("http://localhost:3000/login?next=%2F");
  });

  it("falls back to the request origin when there is no Host header", () => {
    // Host 없는 요청(HTTP/1.0 등)에도 던지지 않고 동작해야 한다 — 미들웨어가
    // 예외를 내면 모든 페이지가 500이 된다.
    const location = middleware(req("/", { host: null })).headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
    expect(location.endsWith("/login?next=%2F")).toBe(true);
  });

  it("ignores a Host header that would break URL parsing", () => {
    // Host는 사용자가 조작할 수 있는 값이다. 파싱 불가한 값이 오면 요청
    // origin으로 떨어지고, 절대 예외를 던지지 않는다.
    const location = middleware(req("/", { host: "bad host\\value" }))
      .headers.get("location")!;
    expect(() => new URL(location)).not.toThrow();
  });

  it("lets an allowed request through without a Location", () => {
    const res = middleware(req("/", { token: tokenFor(["admin"]) }));
    expect(res.headers.get("location")).toBeNull();
  });

  it("does not gate the public survey route", () => {
    // 계정 없는 최종 사용자용 경로 — 로그인으로 보내면 설문을 못 받는다.
    expect(middleware(req("/survey/tok123")).headers.get("location")).toBeNull();
  });
});
