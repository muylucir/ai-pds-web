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

function request(url: string, cookies: Record<string, string> = {}) {
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`).join("; ");
  return new Request(url, { headers: cookie ? { cookie } : {} });
}

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function mockTokenEndpoint() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({
      access_token: fakeJwt({ "cognito:groups": ["admin"], username: "a@b.io" }),
      id_token: fakeJwt({ email: "a@b.io" }),
      refresh_token: "rt",
      expires_in: 3600,
    }), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

describe("GET /api/auth/callback", () => {
  it("sets three httpOnly cookies and redirects to the saved next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "verifier-1", pf_state: "s1", pf_next: "/projects/p1/dashboard" },
    ) as never);

    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("/projects/p1/dashboard");

    const setCookies = res.headers.getSetCookie();
    const joined = setCookies.join("\n");
    for (const name of ["pf_access", "pf_id", "pf_refresh"]) {
      expect(joined).toContain(`${name}=`);
    }
    // 토큰이 JS에 노출되지 않아야 한다 — 이 설계의 핵심 성질이다.
    for (const c of setCookies.filter((c) => /^pf_(access|id|refresh)=/.test(c))) {
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=Lax/i);
    }
    // 왕복용 쿠키는 소비 후 삭제된다.
    expect(joined).toMatch(/pf_pkce=;|pf_pkce=""/);
    expect(joined).toMatch(/pf_state=;|pf_state=""/);
  });

  it("defaults to / when no next cookie was saved", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });

  it("rejects a state mismatch without calling the token endpoint", async () => {
    // CSRF 방어: 공격자가 유도한 콜백은 우리가 심은 state 쿠키와 맞지 않는다.
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=attacker",
      { pf_pkce: "v", pf_state: "ours" },
    ) as never);
    expect(res.status).toBe(302);
    expect(res.headers.get("location"))
      .toBe("/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("clears the round-trip cookies even on a state mismatch", async () => {
    // 실패 경로에서도 pf_pkce/pf_state/pf_next를 지운다 — 성공 경로에서만
    // 지우면 실패한 시도의 PKCE 자재가 다음 로그인까지 브라우저에 남는다.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=attacker",
      { pf_pkce: "v", pf_state: "ours", pf_next: "/somewhere" },
    ) as never);
    const joined = res.headers.getSetCookie().join("\n");
    expect(joined).toMatch(/pf_pkce=;|pf_pkce=""/);
    expect(joined).toMatch(/pf_state=;|pf_state=""/);
    expect(joined).toMatch(/pf_next=;|pf_next=""/);
  });

  it("rejects a missing verifier cookie", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("surfaces a Hosted UI error without attempting an exchange", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?error=access_denied",
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=access_denied");
    expect(f).not.toHaveBeenCalled();
  });

  it("redirects to /login when the token exchange fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 }));
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=stale&state=s1",
      { pf_pkce: "v", pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("/login?error=exchange_failed");
  });

  it("refuses an off-site next path", async () => {
    // open redirect 방어: next는 우리 사이트 내부 경로여야 한다.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "https://evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });

  it("refuses a protocol-relative next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "//evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("/");
  });
});

describe("GET /api/auth/callback — Location은 오리진을 새지 않는다", () => {
  // 실측 배포 버그: CloudFront 뒤에서 Location이 https://localhost:3000/...로
  // 나왔다. Next 15는 req.url을 Host 헤더가 아니라 서버 자체 origin으로
  // 조립하므로 new URL(next, req.url)은 프록시 뒤에서 내부 주소를 샌다.
  // 상대 Location이면 브라우저가 현재 오리진(CloudFront)으로 해석한다.
  it("redirects relatively after a successful exchange", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      // 프록시 뒤 상황 재현: 내부 주소로 들어온 요청.
      "http://localhost:3000/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "/projects/p1/dashboard" },
    ) as never);
    const location = res.headers.get("location")!;
    expect(location).toBe("/projects/p1/dashboard");
    expect(location).not.toContain("localhost");
    expect(location).not.toMatch(/^https?:\/\//);
  });

  it("redirects relatively on failure too", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "http://localhost:3000/api/auth/callback?code=c1&state=attacker",
      { pf_pkce: "v", pf_state: "ours" },
    ) as never);
    const location = res.headers.get("location")!;
    expect(location).toBe("/login?error=state_mismatch");
    expect(location).not.toContain("localhost");
  });

  it("still refuses an off-site next cookie (open redirect)", async () => {
    // 상대 Location으로 바꾸면서 safeNext 방어가 느슨해지지 않았는지 —
    // "//evil.example"은 상대값처럼 보이지만 브라우저는 오프사이트로 읽는다.
    const { GET } = await import("./route");
    for (const evil of ["https://evil.example/x", "//evil.example", "/\\evil.example"]) {
      // 반복마다 mock을 새로 세운다 — 한 번만 세우면 두 번째 교환이 실패해
      // exchange_failed로 떨어지고, 방어가 동작한 것처럼 오해하게 된다.
      mockTokenEndpoint();
      const res = await GET(request(
        "https://app.example.com/api/auth/callback?code=c1&state=s1",
        { pf_pkce: "v", pf_state: "s1", pf_next: evil },
      ) as never);
      const location = res.headers.get("location")!;
      expect(location, `next=${evil}`).toBe("/");
      expect(location).not.toContain("evil.example");
    }
  });
});
