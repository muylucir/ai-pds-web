import { beforeEach, describe, expect, it, vi } from "vitest";

// route handler는 process.env를 모듈 로드 시점이 아니라 호출 시점에 읽어야
// 한다(cognitoEnv()가 함수인 이유). 테스트가 env를 먼저 세팅한다.
beforeEach(() => {
  process.env.COGNITO_HOSTED_UI_DOMAIN = "pool.auth.ap-northeast-2.amazoncognito.com";
  process.env.COGNITO_CLIENT_ID = "client-abc";
  process.env.COGNITO_CLIENT_SECRET = "secret-xyz";
  process.env.APP_BASE_URL = "https://app.example.com";
  vi.restoreAllMocks();
});

function request(method: "GET" | "POST"): Request {
  return new Request("https://app.example.com/api/auth/logout", { method });
}

/** 브라우저가 이 리다이렉트를 따라갈 때 메서드를 바꾸는가?
 *
 * 303 See Other와 302 Found는 POST를 GET으로 바꿔 따라간다. 307/308은
 * **메서드를 보존한다** — 그게 이 파일이 지키는 불변식의 핵심이다. */
const METHOD_PRESERVING = [307, 308];

describe("POST /api/auth/logout", () => {
  // 실측 결함(2026-08-04): 헤더의 로그아웃 버튼을 누르면 브라우저 콘솔에
  //   POST https://<pool>.auth.<region>.amazoncognito.com/logout?... 405 (Method Not Allowed)
  // 가 떴고 로그아웃이 되지 않았다.
  //
  // 원인은 NextResponse.redirect()의 **기본 status가 307**이라는 것이다
  // (next/dist/server/web/spec-extension/response.js: `?? 307`). 307은 스펙상
  // 메서드를 보존하므로, 버튼의 POST가 Cognito Hosted UI의 /logout으로 POST로
  // 재발행된다. 그 엔드포인트는 GET만 받으므로 405다.
  //
  // 우리 쪽 응답은 200도 아니고 에러도 아니어서(정상적인 307) 서버 로그에
  // 아무것도 남지 않는다 — 브라우저 콘솔에만 보이는 실패다.
  it("redirects to the Hosted UI with a status that turns the POST into a GET", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    expect(METHOD_PRESERVING).not.toContain(res.status);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toContain("/logout?");
  });

  it("still sends the user to the Hosted UI logout with our client_id and logout_uri", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    const location = res.headers.get("location") ?? "";
    expect(location).toContain("pool.auth.ap-northeast-2.amazoncognito.com/logout");
    // logout_uri는 Cognito 앱 클라이언트에 등록된 값과 전수 일치해야 한다.
    expect(location).toContain("client_id=client-abc");
    expect(location).toContain(encodeURIComponent("https://app.example.com/login"));
  });

  it("clears all three auth cookies on the way out", async () => {
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    // 쿠키를 지우지 않으면 Hosted UI에서 돌아온 뒤에도 게이트가 통과한다.
    const setCookie = res.headers.getSetCookie().join("\n");
    for (const name of ["pf_access", "pf_id", "pf_refresh"]) {
      expect(setCookie).toContain(`${name}=`);
    }
    expect(setCookie).toContain("Max-Age=0");
  });

  it("falls back to /login when Cognito is not configured (local bypass)", async () => {
    process.env.COGNITO_HOSTED_UI_DOMAIN = "";
    process.env.COGNITO_CLIENT_ID = "";
    const { POST } = await import("./route");
    const res = await POST(request("POST") as never);

    // 이 경로도 같은 이유로 메서드를 보존해선 안 된다 — /login은 페이지(GET)다.
    expect(METHOD_PRESERVING).not.toContain(res.status);
    expect(res.headers.get("location")).toBe("/login");
  });
});

describe("GET /api/auth/logout", () => {
  it("works too, for a plain link or a direct visit", async () => {
    const { GET } = await import("./route");
    const res = await GET(request("GET") as never);

    expect(res.headers.get("location")).toContain("/logout?");
    // GET에는 메서드 보존 문제가 없지만, 두 진입점이 같은 응답을 내는 것이
    // 이 라우트의 계약이다(POST가 GET을 부른다).
    expect(res.status).toBe(303);
  });
});
