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
      .toBe("https://app.example.com/projects/p1/dashboard");

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
    expect(res.headers.get("location")).toBe("https://app.example.com/");
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
      .toBe("https://app.example.com/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects a missing verifier cookie", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_state: "s1" },
    ) as never);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=state_mismatch");
    expect(f).not.toHaveBeenCalled();
  });

  it("surfaces a Hosted UI error without attempting an exchange", async () => {
    const f = mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?error=access_denied",
    ) as never);
    expect(res.headers.get("location"))
      .toBe("https://app.example.com/login?error=access_denied");
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
      .toBe("https://app.example.com/login?error=exchange_failed");
  });

  it("refuses an off-site next path", async () => {
    // open redirect 방어: next는 우리 사이트 내부 경로여야 한다.
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "https://evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("https://app.example.com/");
  });

  it("refuses a protocol-relative next path", async () => {
    mockTokenEndpoint();
    const { GET } = await import("./route");
    const res = await GET(request(
      "https://app.example.com/api/auth/callback?code=c1&state=s1",
      { pf_pkce: "v", pf_state: "s1", pf_next: "//evil.example/steal" },
    ) as never);
    expect(res.headers.get("location")).toBe("https://app.example.com/");
  });
});
